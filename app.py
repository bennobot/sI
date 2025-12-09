import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import re
from streamlit_gsheets import GSheetsConnection
from thefuzz import process

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
# 1. DATA FUNCTIONS
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

# --- MATRIX LOGIC (FIXED SORTING) ---
def create_product_matrix(df):
    if df is None or df.empty: return pd.DataFrame()
    df = df.fillna("")
    
    group_cols = ['Supplier_Name', 'Collaborator', 'Product_Name', 'ABV']
    
    # CRITICAL CHANGE: sort=False keeps the order from the original invoice
    grouped = df.groupby(group_cols, sort=False)
    
    matrix_rows = []
    for name, group in grouped:
        row = {
            'Supplier_Name': name[0],
            'Collaborator': name[1],
            'Product_Name': name[2],
            'ABV': name[3]
        }
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

# ==========================================
# 2. SESSION & SIDEBAR
# ==========================================

if 'header_data' not in st.session_state: st.session_state.header_data = None
if 'line_items' not in st.session_state: st.session_state.line_items = None
if 'matrix_data' not in st.session_state: st.session_state.matrix_data = None
if 'checker_data' not in st.session_state: st.session_state.checker_data = None
if 'master_suppliers' not in st.session_state: st.session_state.master_suppliers = get_master_supplier_list()

with st.sidebar:
    st.header("Settings")
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("API Key Loaded 🔑")
    else:
        api_key = st.text_input("Enter API Key", type="password")

    with st.form("process"):
        st.info("Logic loaded from `knowledge_base.py`")
        if st.session_state.master_suppliers:
            st.success(f"Loaded {len(st.session_state.master_suppliers)} Master Suppliers")
        else:
            st.warning("Master Supplier List not found")
            
        custom_rule = st.text_area("Inject Temporary Rule (The Lab):", height=100)
        submit_button = st.form_submit_button("🚀 Process Invoice", type="primary")
    
    st.divider()
    if st.button("Log Out"):
        st.session_state.password_correct = False
        st.rerun()

# ==========================================
# 3. MAIN LOGIC
# ==========================================

st.subheader("1. Upload Invoice")
uploaded_file = st.file_uploader("Drop PDF here", type="pdf")

if uploaded_file:
    if 'last_uploaded_file' not in st.session_state or st.session_state.last_uploaded_file != uploaded_file.name:
        st.session_state.header_data = None
        st.session_state.line_items = None
        st.session_state.matrix_data = None
        st.session_state.checker_data = None
        st.session_state.last_uploaded_file = uploaded_file.name

if uploaded_file and api_key and submit_button:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        
        with st.spinner("Processing..."):
            uploaded_file.seek(0)
            images = convert_from_bytes(uploaded_file.read(), dpi=300)
            full_text = ""
            for img in images:
                full_text += pytesseract.image_to_string(img) + "\n"

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
            json_text = response.text.strip().replace("```json", "").replace("```", "")
            data = json.loads(json_text)
            
            st.session_state.header_data = pd.DataFrame([data['header']])
            df_lines = pd.DataFrame(data['line_items'])
            
            df_lines = clean_product_names(df_lines)
            
            if st.session_state.master_suppliers:
                df_lines = normalize_supplier_names(df_lines, st.session_state.master_suppliers)

            cols = ["Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price", "Quantity"]
            existing = [c for c in cols if c in df_lines.columns]
            st.session_state.line_items = df_lines[existing]
            
            # 3. Generate Derived Tables (Sort=False applied inside function)
            st.session_state.matrix_data = create_product_matrix(st.session_state.line_items)
            st.session_state.checker_data = create_product_checker(st.session_state.line_items)

    except Exception as e:
        st.error(f"Error: {e}")

# ==========================================
# 4. DISPLAY
# ==========================================

if st.session_state.header_data is not None:
    if custom_rule:
        st.success("✅ Used Custom Rules")
        try: sup = st.session_state.header_data.iloc[0]['Payable_To']
        except: sup = "Unknown"
        with st.expander("📩 Developer Snippet"):
            st.code(f'"{sup}": """\n{custom_rule}\n""",', language="python")

    st.divider()
    t1, t2, t3, t4 = st.tabs(["📊 **Product Matrix (Edit Here)**", "📄 Header", "📝 Line Items", "🔍 Checker"])
    
    with t1:
        st.info("💡 Edit product details here. Click 'Sync' to update the other files.")
        edited_matrix = st.data_editor(st.session_state.matrix_data, num_rows="dynamic", use_container_width=True)
        colA, colB = st.columns([1, 4])
        with colA:
            if st.button("🔄 Sync & Regenerate"):
                st.session_state.matrix_data = edited_matrix
                st.session_state.line_items = reconstruct_lines_from_matrix(edited_matrix)
                st.session_state.checker_data = create_product_checker(st.session_state.line_items)
                st.success("Synced!")
                st.rerun()
        with colB:
            st.download_button("📥 Download CSV", edited_matrix.to_csv(index=False), "matrix.csv")

    with t2:
        edited_header = st.data_editor(st.session_state.header_data, num_rows="fixed", use_container_width=True)
        st.download_button("📥 Download CSV", edited_header.to_csv(index=False), "header.csv")
    with t3:
        st.caption("Generated from Matrix.")
        st.dataframe(st.session_state.line_items, use_container_width=True)
        st.download_button("📥 Download CSV", st.session_state.line_items.to_csv(index=False), "lines.csv")
    with t4:
        if st.session_state.checker_data is not None:
            st.dataframe(st.session_state.checker_data, use_container_width=True)
            st.download_button("📥 Download CSV", st.session_state.checker_data.to_csv(index=False), "checker.csv")
