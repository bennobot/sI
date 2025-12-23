import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import re
import io
import requests
import time
from urllib.parse import quote
from streamlit_gsheets import GSheetsConnection
from thefuzz import process, fuzz
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Import the Brain
from knowledge_base import GLOBAL_RULES_TEXT, SUPPLIER_RULEBOOK

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")

# ==========================================
# 0. AUTHENTICATION
# ==========================================
def check_password():
    if "APP_PASSWORD" not in st.secrets: return True
    if "password_correct" not in st.session_state: st.session_state.password_correct = False
    if st.session_state.password_correct: return True
    st.title("🔒 Login Required")
    pwd_input = st.text_input("Enter Password", type="password")
    if st.button("Log In"):
        if pwd_input == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            st.rerun()
        else: st.error("Incorrect Password")
    return False

if not check_password(): st.stop()

st.title("Brewery Invoice Parser ⚡")

# ==========================================
# 1A. CIN7 CORE ENGINE
# ==========================================

def get_cin7_headers():
    if "cin7" not in st.secrets: return None
    creds = st.secrets["cin7"]
    return {
        "api-auth-accountid": creds.get("account_id"),
        "api-auth-applicationkey": creds.get("api_key"),
        "Content-Type": "application/json"
    }

def get_cin7_base_url():
    if "cin7" not in st.secrets: return None
    return st.secrets["cin7"].get("base_url", "https://inventory.dearsystems.com/ExternalApi/v2")

def get_cin7_product_id(sku):
    headers = get_cin7_headers()
    if not headers: return None
    url = f"{get_cin7_base_url()}/product?Sku={sku}"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if "Products" in data and len(data["Products"]) > 0:
                return data["Products"][0]["ID"]
    except: pass
    return None

def get_cin7_supplier(name):
    headers = get_cin7_headers()
    if not headers: return None
    
    # 1. Exact Match
    safe_name = quote(name)
    url = f"{get_cin7_base_url()}/supplier?Name={safe_name}"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if "Suppliers" in data and len(data["Suppliers"]) > 0:
                return data["Suppliers"][0]
    except: pass
    
    # 2. Fallback "&" -> "and"
    if "&" in name:
        return get_cin7_supplier(name.replace("&", "and"))
    
    # 3. DEBUG MODE: Fetch ALL suppliers if specific search failed
    try:
        if not st.session_state.get('cin7_supplier_list'):
            all_suppliers = []
            page = 1
            while page <= 5: 
                r = requests.get(f"{get_cin7_base_url()}/supplier?Page={page}&Limit=100", headers=headers)
                if r.status_code == 200:
                    d = r.json()
                    if "Suppliers" in d:
                        all_suppliers.extend([s['Name'] for s in d['Suppliers']])
                        if len(d['Suppliers']) < 100: break
                page += 1
            st.session_state.cin7_supplier_list = sorted(all_suppliers)
            
        # Local Fuzzy Match
        if st.session_state.cin7_supplier_list:
            match, score = process.extractOne(name, st.session_state.cin7_supplier_list)
            if score >= 90:
                real_name = quote(match)
                r = requests.get(f"{get_cin7_base_url()}/supplier?Name={real_name}", headers=headers)
                return r.json()['Suppliers'][0]

    except Exception as e:
        print(f"Cin7 Debug Error: {e}")

    return None

def match_cin7_supplier_header(header_df):
    """
    Updates the header DataFrame with Cin7 Supplier ID and Name.
    """
    if header_df.empty: return header_df, "Header empty."
    
    supplier_name = header_df.iloc[0]['Payable_To']
    cin7_data = get_cin7_supplier(supplier_name)
    
    if cin7_data:
        header_df['Cin7_Supplier_ID'] = cin7_data['ID']
        header_df['Cin7_Supplier_Name'] = cin7_data['Name']
        return header_df, f"✅ Matched to: {cin7_data['Name']}"
    else:
        header_df['Cin7_Supplier_ID'] = "NOT FOUND"
        header_df['Cin7_Supplier_Name'] = "NOT FOUND"
        return header_df, f"❌ Supplier '{supplier_name}' not found in Cin7."

# ==========================================
# 1B. SHOPIFY ENGINE
# ==========================================

def fetch_shopify_products_by_vendor(vendor):
    if "shopify" not in st.secrets: return []
    creds = st.secrets["shopify"]
    shop_url = creds.get("shop_url")
    token = creds.get("access_token")
    version = creds.get("api_version", "2024-04")
    endpoint = f"https://{shop_url}/admin/api/{version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    query = """query ($query: String!) { products(first: 50, query: $query) { edges { node { id title status format_meta: metafield(namespace: "custom", key: "Format") { value } abv_meta: metafield(namespace: "custom", key: "ABV") { value } variants(first: 20) { edges { node { id title sku inventoryQuantity } } } } } } }"""
    search_vendor = vendor.replace("'", "\\'") 
    variables = {"query": f"vendor:'{search_vendor}'"} 
    try:
        response = requests.post(endpoint, json={"query": query, "variables": variables}, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if "data" in data and "products" in data["data"]: return data["data"]["products"]["edges"]
    except: pass
    return []

def normalize_vol_string(v_str):
    if not v_str: return "0"
    v_str = str(v_str).lower().strip()
    nums = re.findall(r'\d+', v_str)
    if not nums: return "0"
    val = float(nums[0])
    if "ml" in v_str: val = val / 10
    return str(int(val))

def run_reconciliation_check(lines_df):
    if lines_df.empty: return lines_df, ["No Lines to check."]
    logs = []
    df = lines_df.copy()
    
    # Init columns
    df['Shopify_Status'] = "Pending"
    df['London_SKU'] = ""     
    df['Cin7_London_ID'] = "" 
    df['Gloucester_SKU'] = "" 
    df['Cin7_Glou_ID'] = ""   
    
    suppliers = df['Supplier_Name'].unique()
    shopify_cache = {}
    
    progress_bar = st.progress(0)
    for i, supplier in enumerate(suppliers):
        progress_bar.progress((i)/len(suppliers))
        logs.append(f"🔎 **Fetching Shopify Data:** `{supplier}`")
        products = fetch_shopify_products_by_vendor(supplier)
        shopify_cache[supplier] = products
        logs.append(f"   -> Found {len(products)} products.")
    progress_bar.progress(1.0)

    results = []
    for _, row in df.iterrows():
        status = "❓ Vendor Not Found"
        london_sku, glou_sku, cin7_l_id, cin7_g_id = "", "", "", ""
        supplier = row['Supplier_Name']
        inv_prod_name = row['Product_Name']
        inv_pack = str(row.get('Pack_Size', '1')).replace('.0', '')
        if inv_pack in ["", "nan", "0"]: inv_pack = "1"
        inv_vol = normalize_vol_string(row.get('Volume', ''))
        
        logs.append(f"Checking: **{inv_prod_name}**")

        if supplier in shopify_cache and shopify_cache[supplier]:
            candidates = shopify_cache[supplier]
            scored_candidates = []
            for edge in candidates:
                prod = edge['node']
                shop_title_full = prod['title']
                shop_prod_name_clean = shop_title_full
                if "/" in shop_title_full:
                    parts = [p.strip() for p in shop_title_full.split("/")]
                    if len(parts) >= 2: shop_prod_name_clean = parts[1]
                score = fuzz.token_sort_ratio(inv_prod_name, shop_prod_name_clean)
                if inv_prod_name.lower() in shop_prod_name_clean.lower(): score += 10
                if score > 40: scored_candidates.append((score, prod))
            
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            match_found = False
            
            for score, prod in scored_candidates:
                if score < 60: continue
                for v_edge in prod['variants']['edges']:
                    variant = v_edge['node']
                    v_title = variant['title'].lower()
                    v_sku = str(variant.get('sku', '')).strip()
                    pack_ok = False
                    if inv_pack == "1":
                        if " x " not in v_title: pack_ok = True
                    else:
                        if f"{inv_pack} x" in v_title or f"{inv_pack}x" in v_title: pack_ok = True
                    vol_ok = False
                    if inv_vol in v_title: vol_ok = True
                    if len(inv_vol) == 2 and f"{inv_vol}0" in v_title: vol_ok = True 
                    if pack_ok and vol_ok:
                        logs.append(f"   ✅ MATCH: `{variant['title']}` | SKU: `{v_sku}`")
                        status = "✅ Matched"
                        match_found = True
                        if v_sku and len(v_sku) > 2:
                            base_sku = v_sku[2:]
                            london_sku = f"L-{base_sku}"
                            glou_sku = f"G-{base_sku}"
                        break
                if match_found: break
            if not match_found: status = "❌ Size Missing" if scored_candidates else "🆕 New Product"
        
        if london_sku: cin7_l_id = get_cin7_product_id(london_sku)
        if glou_sku: cin7_g_id = get_cin7_product_id(glou_sku)

        row['Shopify_Status'] = status
        row['London_SKU'] = london_sku
        row['Cin7_London_ID'] = cin7_l_id
        row['Gloucester_SKU'] = glou_sku
        row['Cin7_Glou_ID'] = cin7_g_id
        results.append(row)
    
    return pd.DataFrame(results), logs

# ==========================================
# 2. DATA & DRIVE FUNCTIONS
# ==========================================

def get_master_supplier_list():
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df = conn.read(worksheet="MasterData", ttl=600)
        return df['Supplier_Master'].dropna().astype(str).tolist()
    except: return []

def normalize_supplier_names(df, master_list):
    if df is None or df.empty or not master_list: return df
    def match_name(name):
        if not isinstance(name, str): return name
        match, score = process.extractOne(name, master_list)
        return match if score >= 88 else name
    if 'Supplier_Name' in df.columns:
        df['Supplier_Name'] = df['Supplier_Name'].apply(match_name)
    return df

def clean_product_names(df):
    if df is None or df.empty: return df
    def cleaner(name):
        if not isinstance(name, str): return name
        name = name.replace('|', '')
        name = re.sub(r'\b\d+x\d+cl\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\b\d+g\b', '', name, flags=re.IGNORECASE)
        return ' '.join(name.split())
    if 'Product_Name' in df.columns:
        df['Product_Name'] = df['Product_Name'].apply(cleaner)
    return df

def create_product_matrix(df):
    if df is None or df.empty: return pd.DataFrame()
    df = df.fillna("")
    if 'Shopify_Status' in df.columns:
        df = df[df['Shopify_Status'] != "✅ Matched"]
    if df.empty: return pd.DataFrame()

    group_cols = ['Supplier_Name', 'Collaborator', 'Product_Name', 'ABV']
    grouped = df.groupby(group_cols, sort=False)
    matrix_rows = []
    
    for name, group in grouped:
        row = {'Supplier_Name': name[0], 'Collaborator': name[1], 'Product_Name': name[2], 'ABV': name[3]}
        for i, (_, item) in enumerate(group.iterrows()):
            if i >= 3: break
            suffix = str(i + 1)
            row[f'Format{suffix}'] = item['Format']
            row[f'Pack_Size{suffix}'] = item['Pack_Size']
            row[f'Volume{suffix}'] = item['Volume']
            row[f'Item_Price{suffix}'] = item['Item_Price']
            row[f'Create{suffix}'] = False 
        matrix_rows.append(row)
        
    matrix_df = pd.DataFrame(matrix_rows)
    base_cols = ['Supplier_Name', 'Collaborator', 'Product_Name', 'ABV']
    format_cols = []
    for i in range(1, 4):
        format_cols.extend([f'Format{i}', f'Pack_Size{i}', f'Volume{i}', f'Item_Price{i}', f'Create{i}'])
    
    final_cols = base_cols + [c for c in format_cols if c in matrix_df.columns]
    return matrix_df[final_cols]

def reconstruct_lines_from_matrix(matrix_df):
    if matrix_df is None or matrix_df.empty: return pd.DataFrame()
    new_lines = []
    for _, row in matrix_df.iterrows():
        base = {
            'Supplier_Name': row.get('Supplier_Name', ''),
            'Collaborator': row.get('Collaborator', ''),
            'Product_Name': row.get('Product_Name', ''),
            'ABV': row.get('ABV', '')
        }
        for i in range(1, 4):
            fmt = row.get(f'Format{i}')
            if pd.notna(fmt) and str(fmt).strip():
                line = base.copy()
                line['Format'] = fmt
                line['Pack_Size'] = row.get(f'Pack_Size{i}', '')
                line['Volume'] = row.get(f'Volume{i}', '')
                line['Item_Price'] = row.get(f'Item_Price{i}', 0.0)
                line['Quantity'] = row.get(f'Quantity{i}', 0)
                new_lines.append(line)
    return pd.DataFrame(new_lines)

# --- GOOGLE DRIVE HELPERS ---
def get_drive_service():
    if "connections" in st.secrets and "gsheets" in st.secrets["connections"]:
        creds_dict = st.secrets["connections"]["gsheets"]
        creds = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        return build('drive', 'v3', credentials=creds)
    return None

def list_files_in_folder(folder_id):
    service = get_drive_service()
    if not service: return []
    query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    results = service.files().list(q=query, pageSize=100, fields="files(id, name)").execute()
    files = results.get('files', [])
    files.sort(key=lambda x: x['name'].lower())
    return files

def download_file_from_drive(file_id):
    service = get_drive_service()
    if not service: return None
    request = service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    file_stream.seek(0)
    return file_stream

# ==========================================
# 3. SESSION & SIDEBAR
# ==========================================

# Initialize Session State
if 'header_data' not in st.session_state: st.session_state.header_data = None
if 'line_items' not in st.session_state: st.session_state.line_items = None
if 'matrix_data' not in st.session_state: st.session_state.matrix_data = None
if 'checker_data' not in st.session_state: st.session_state.checker_data = None
if 'master_suppliers' not in st.session_state: st.session_state.master_suppliers = get_master_supplier_list()
if 'drive_files' not in st.session_state: st.session_state.drive_files = []
if 'selected_drive_id' not in st.session_state: st.session_state.selected_drive_id = None
if 'selected_drive_name' not in st.session_state: st.session_state.selected_drive_name = None
if 'shopify_logs' not in st.session_state: st.session_state.shopify_logs = []
if 'cin7_supplier_list' not in st.session_state: st.session_state.cin7_supplier_list = []

with st.sidebar:
    st.header("Settings")
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("API Key Loaded 🔑")
    else:
        api_key = st.text_input("Enter API Key", type="password")

    st.info("Logic loaded from `knowledge_base.py`")
    
    st.divider()
    st.subheader("📂 Google Drive")
    folder_id = st.text_input("Drive Folder ID", help="Copy the ID string from the URL")
    
    if st.button("🔍 Scan Folder"):
        if folder_id:
            try:
                with st.spinner("Scanning..."):
                    files = list_files_in_folder(folder_id)
                    st.session_state.drive_files = files
                if files:
                    st.success(f"Found {len(files)} PDFs!")
                else:
                    st.warning("No PDFs found or Access Denied.")
            except Exception as e:
                st.error(f"Error: {e}")
    
    st.divider()
    
    st.subheader("🧪 The Lab")
    with st.form("teaching_form"):
        st.caption("Test a new rule here. Press Ctrl+Enter to apply.")
        custom_rule = st.text_area("Inject Temporary Rule:", height=100)
        st.form_submit_button("Set Rule")

    st.divider()
    if st.button("Log Out"):
        st.session_state.password_correct = False
        st.rerun()

# ==========================================
# 4. MAIN LOGIC (SOURCE SELECTION)
# ==========================================

st.subheader("1. Select Invoice Source")
tab_upload, tab_drive = st.tabs(["⬆️ Manual Upload", "☁️ Google Drive"])

target_stream = None
source_name = "Unknown"

with tab_upload:
    uploaded_file = st.file_uploader("Drop PDF here", type="pdf")
    if uploaded_file:
        target_stream = uploaded_file
        source_name = uploaded_file.name

with tab_drive:
    if st.session_state.drive_files:
        file_names = [f['name'] for f in st.session_state.drive_files]
        selected_name = st.selectbox("Select Invoice from Drive List:", options=file_names, index=None, placeholder="Choose a file...")
        if selected_name:
            file_data = next(f for f in st.session_state.drive_files if f['name'] == selected_name)
            st.session_state.selected_drive_id = file_data['id']
            st.session_state.selected_drive_name = file_data['name']
            
            if not uploaded_file:
                source_name = selected_name
    else:
        st.info("👈 Enter a Folder ID in the sidebar and click Scan to see files here.")

# --- PROCESS BUTTON ---
if st.button("🚀 Process Invoice", type="primary"):
    
    if not uploaded_file and st.session_state.selected_drive_id:
        try:
            with st.status(f"Downloading {source_name}...", expanded=False) as status:
                target_stream = download_file_from_drive(st.session_state.selected_drive_id)
                status.update(label="Download Complete", state="complete")
        except Exception as e:
            st.error(f"Download Failed: {e}")
            st.stop()

    if target_stream and api_key:
        try:
            with st.status("Processing Document...", expanded=True) as status:
                
                genai.configure(api_key=api_key)
                model = genai.GenerativeModel('models/gemini-2.5-flash')
                
                st.write("1. Converting PDF to Images (OCR Prep)...")
                target_stream.seek(0)
                images = convert_from_bytes(target_stream.read(), dpi=300)
                
                st.write(f"2. Extracting Text from {len(images)} pages...")
                full_text = ""
                for i, img in enumerate(images):
                    st.write(f"   - Scanning page {i+1}...")
                    full_text += pytesseract.image_to_string(img) + "\n"

                st.write("3. Sending Text to AI Model...")
                injected = f"\n!!! USER OVERRIDE !!!\n{custom_rule}\n" if custom_rule else ""

                prompt = f"""
                Extract invoice data to JSON.
                STRUCTURE:
                {{
                    "header": {{
                        "Payable_To": "Supplier Name", "Invoice_Number": "...", "Issue_Date": "...", 
                        "Payment_Terms": "...", "Due_Date": "...", "Total_Net": 0.00, 
                        "Total_VAT": 0.00, "Total_Gross": 0.00, "Total_Discount_Amount": 0.00, "Shipping_Charge": 0.00
                    }},
                    "line_items": [
                        {{
                            "Supplier_Name": "...", "Collaborator": "...", "Product_Name": "...", "ABV": "...", 
                            "Format": "...", "Pack_Size": "...", "Volume": "...", "Quantity": 1, "Item_Price": 10.00
                        }}
                    ]
                }}
                SUPPLIER RULEBOOK: {json.dumps(SUPPLIER_RULEBOOK)}
                GLOBAL RULES: {GLOBAL_RULES_TEXT}
                {injected}
                INVOICE TEXT:
                {full_text}
                """

                response = model.generate_content(prompt)
                
                st.write("4. Parsing Response...")
                try:
                    json_text = response.text.strip().replace("```json", "").replace("```", "")
                    data = json.loads(json_text)
                except Exception as e:
                    st.error(f"AI returned invalid JSON: {response.text}")
                    st.stop()
                
                st.write("5. Finalizing Data...")
                
                st.session_state.header_data = pd.DataFrame([data['header']])
                df_lines = pd.DataFrame(data['line_items'])
                
                df_lines = clean_product_names(df_lines)
                if st.session_state.master_suppliers:
                    df_lines = normalize_supplier_names(df_lines, st.session_state.master_suppliers)

                cols = ["Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price", "Quantity"]
                existing = [c for c in cols if c in df_lines.columns]
                st.session_state.line_items = df_lines[existing]
                
                # Clear Logs
                st.session_state.shopify_logs = []
                st.session_state.cin7_supplier_list = []
                st.session_state.matrix_data = None
                
                status.update(label="Processing Complete!", state="complete", expanded=False)

        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.warning("Please upload a file or select one from Google Drive first.")

# ==========================================
# 5. DISPLAY
# ==========================================

if st.session_state.header_data is not None:
    if custom_rule:
        st.success("✅ Used Custom Rules")
        try: sup = st.session_state.header_data.iloc[0]['Payable_To']
        except: sup = "Unknown"
        with st.expander("📩 Developer Snippet"):
            st.code(f'"{sup}": """\n{custom_rule}\n""",', language="python")

    st.divider()
    t1, t2, t3 = st.tabs(["📝 Line Items (Work Area)", "📊 Missing Products Report", "📄 Invoice Header"])
    
    with t1:
        st.subheader("1. Review & Edit Lines")
        
        edited_lines = st.data_editor(
            st.session_state.line_items, 
            num_rows="dynamic", 
            width=1000,
            key="line_editor"
        )
        st.session_state.line_items = edited_lines

        col1, col2 = st.columns([1, 4])
        with col1:
            if "shopify" in st.secrets:
                if st.button("🛒 Check Inventory & Generate Report"):
                    with st.spinner("Checking..."):
                        updated_lines, logs = run_reconciliation_check(st.session_state.line_items)
                        st.session_state.line_items = updated_lines
                        st.session_state.shopify_logs = logs
                        st.session_state.matrix_data = create_product_matrix(updated_lines)
                        st.success("Check Complete!")
                        st.rerun()
        
        with col2:
             st.download_button("📥 Download Lines CSV", edited_lines.to_csv(index=False), "lines.csv")
        
        if st.session_state.shopify_logs:
            with st.expander("🕵️ Debug Logs", expanded=False):
                st.markdown("\n".join(st.session_state.shopify_logs))

    with t2:
        st.subheader("2. Products to Create in Shopify")
        st.info("Check the boxes as you create these products.")
        
        if st.session_state.matrix_data is not None and not st.session_state.matrix_data.empty:
            column_config = {}
            for i in range(1, 4):
                column_config[f"Create{i}"] = st.column_config.CheckboxColumn(f"Create {i}?", default=False)

            edited_matrix = st.data_editor(
                st.session_state.matrix_data, 
                num_rows="dynamic", 
                width=1000,
                column_config=column_config
            )
            st.download_button("📥 Download To-Do List CSV", edited_matrix.to_csv(index=False), "missing_products.csv")
        elif st.session_state.matrix_data is not None:
            st.success("🎉 All products matched! Nothing to create.")
        else:
            st.warning("Run 'Check Inventory' in Tab 1 to generate this report.")

    with t3:
        # HEADER TAB WITH CIN7 MATCH BUTTON
        col_h1, col_h2 = st.columns([4, 1])
        with col_h1:
            edited_header = st.data_editor(st.session_state.header_data, num_rows="fixed", width=1000)
        with col_h2:
            if st.button("🔗 Match Supplier (Cin7)"):
                with st.spinner("Matching..."):
                    updated_header, msg = match_cin7_supplier_header(edited_header)
                    st.session_state.header_data = updated_header
                    if "✅" in msg: st.success(msg)
                    else: st.error(msg)
                    st.rerun()
        
        st.download_button("📥 Download Header CSV", edited_header.to_csv(index=False), "header.csv")
        
        # DEBUGGER (Shows if search fails)
        if st.session_state.cin7_supplier_list:
            with st.expander("🐞 Cin7 Supplier Debugger", expanded=True):
                st.write(st.session_state.cin7_supplier_list)
