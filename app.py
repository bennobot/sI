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
# 1. SHOPIFY RECONCILIATION ENGINE
# ==========================================

def fetch_shopify_products_by_vendor(vendor):
    if "shopify" not in st.secrets: return []
    creds = st.secrets["shopify"]
    shop_url = creds.get("shop_url")
    token = creds.get("access_token")
    version = creds.get("api_version", "2024-04")
    
    endpoint = f"https://{shop_url}/admin/api/{version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    
    query = """
    query ($query: String!) {
      products(first: 50, query: $query) {
        edges {
          node {
            id
            title
            status
            format_meta: metafield(namespace: "custom", key: "Format") { value }
            abv_meta: metafield(namespace: "custom", key: "ABV") { value }
            variants(first: 20) {
              edges {
                node {
                  id
                  title
                  sku
                  inventoryQuantity
                }
              }
            }
          }
        }
      }
    }
    """
    search_vendor = vendor.replace("'", "\\'") 
    variables = {"query": f"vendor:'{search_vendor}' AND status:ACTIVE"}
    
    try:
        response = requests.post(endpoint, json={"query": query, "variables": variables}, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if "data" in data and "products" in data["data"]:
                return data["data"]["products"]["edges"]
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

def run_shopify_check(lines_df):
    """
    Checks line items against Shopify.
    Handles 'L-Supplier / Product / ABV / Format' title structure.
    """
    if lines_df.empty: return lines_df, ["No Lines to check."]
    
    logs = []
    df = lines_df.copy()
    df['Shopify_Status'] = "Pending"
    df['Shopify_Variant_ID'] = ""
    
    suppliers = df['Supplier_Name'].unique()
    shopify_cache = {}
    
    progress_bar = st.progress(0)
    for i, supplier in enumerate(suppliers):
        progress_bar.progress((i)/len(suppliers))
        logs.append(f"**Searching Vendor:** `{supplier}`")
        products = fetch_shopify_products_by_vendor(supplier)
        shopify_cache[supplier] = products
        logs.append(f" -> Found {len(products)} products.")
        
    progress_bar.progress(1.0)
    
    results = []
    for _, row in df.iterrows():
        status = "❓ Vendor Not Found"
        found_id = ""
        
        supplier = row['Supplier_Name']
        inv_prod_name = row['Product_Name']
        inv_pack = str(row.get('Pack_Size', '1')).replace('.0', '')
        if inv_pack in ["", "nan", "0"]: inv_pack = "1"
        inv_vol = normalize_vol_string(row.get('Volume', ''))

        if supplier in shopify_cache and shopify_cache[supplier]:
            candidates = shopify_cache[supplier]
            best_score = 0
            best_prod = None
            
            for edge in candidates:
                prod = edge['node']
                shop_title_full = prod['title']
                
                # --- NEW LOGIC: PARSE SHOPIFY TITLE STRUCTURE ---
                # Expected format: "L-Supplier / Product Name / ABV / Format"
                shop_prod_name_clean = shop_title_full
                
                if "/" in shop_title_full:
                    parts = [p.strip() for p in shop_title_full.split("/")]
                    # Usually Product Name is index 1
                    if len(parts) >= 2:
                        shop_prod_name_clean = parts[1]
                
                # Fuzzy Match
                score = fuzz.token_sort_ratio(inv_prod_name, shop_prod_name_clean)
                
                # Boost if exact substring
                if inv_prod_name.lower() in shop_prod_name_clean.lower():
                    score += 10
                
                if score > best_score:
                    best_score = score
                    best_prod = prod
            
            if best_prod and best_score > 70:
                logs.append(f"MATCH: `{inv_prod_name}` == `{best_prod['title']}` ({best_score}%)")
                
                # Check Variants
                variant_found = False
                for v_edge in best_prod['variants']['edges']:
                    variant = v_edge['node']
                    v_title = variant['title'].lower()
                    
                    pack_ok = False
                    if inv_pack == "1":
                        if " x " not in v_title: pack_ok = True
                    else:
                        if f"{inv_pack} x" in v_title or f"{inv_pack}x" in v_title: pack_ok = True
                    
                    vol_ok = False
                    if inv_vol in v_title: vol_ok = True
                    if len(inv_vol) == 2 and f"{inv_vol}0" in v_title: vol_ok = True 
                    
                    if pack_ok and vol_ok:
                        variant_found = True
                        found_id = variant['id']
                        break
                    else:
                        logs.append(f"  - Variant `{v_title}` failed size check (Need {inv_pack}x / {inv_vol})")
                
                if variant_found: status = "✅ Matched"
                else: status = "❌ Size Missing"
            else:
                status = "🆕 New Product"
                logs.append(f"  - No match for `{inv_prod_name}`. Best was {best_score}%")
        
        row['Shopify_Status'] = status
        row['Shopify_Variant_ID'] = found_id
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
            row[f'Quantity{suffix}'] = item['Quantity']
        matrix_rows.append(row)
    matrix_df = pd.DataFrame(matrix_rows)
    base_cols = ['Supplier_Name', 'Collaborator', 'Product_Name', 'ABV']
    format_cols = []
    for i in range(1, 4):
        format_cols.extend([f'Format{i}', f'Pack_Size{i}', f'Volume{i}', f'Item_Price{i}', f'Quantity{i}'])
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

def create_product_checker(df):
    if df is None or df.empty: return pd.DataFrame()
    checker_rows = []
    for _, row in df.iterrows():
        abv = str(row['ABV']).replace('%', '') + "%" if row['ABV'] else ""
        parts = [str(row['Supplier_Name']), str(row['Product_Name']), abv, str(row['Format'])]
        col1 = " / ".join([p for p in parts if p and p.lower() != 'none'])
        pack = str(row['Pack_Size']).replace('.0', '') if row['Pack_Size'] else ""
        vol = str(row['Volume'])
        col2 = f"{pack}x{vol}" if (pack and pack != '0' and pack != '1') else vol
        checker_rows.append({"ERP_String": col1, "Size_String": col2})
    return pd.DataFrame(checker_rows).drop_duplicates()

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

if 'header_data' not in st.session_state: st.session_state.header_data = None
if 'line_items' not in st.session_state: st.session_state.line_items = None
if 'matrix_data' not in st.session_state: st.session_state.matrix_data = None
if 'checker_data' not in st.session_state: st.session_state.checker_data = None
if 'master_suppliers' not in st.session_state: st.session_state.master_suppliers = get_master_supplier_list()
if 'drive_files' not in st.session_state: st.session_state.drive_files = []
if 'selected_drive_id' not in st.session_state: st.session_state.selected_drive_id = None
if 'selected_drive_name' not in st.session_state: st.session_state.selected_drive_name = None
if 'shopify_logs' not in st.session_state: st.session_state.shopify_logs = []

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
        source_name = uploaded_fi
