import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import re
import time

# Import the Brain
from knowledge_base import GLOBAL_RULES_TEXT, SUPPLIER_RULEBOOK

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")

# ==========================================
# 0. AUTHENTICATION (PASSWORD GATE)
# ==========================================

def check_password():
    """Returns `True` if the user had the correct password."""

    # 1. If password is not set in secrets, allow access (Dev mode)
    if "APP_PASSWORD" not in st.secrets:
        return True

    # 2. Check session state
    if "password_correct" not in st.session_state:
        st.session_state.password_correct = False

    if st.session_state.password_correct:
        return True

    # 3. Show Login Form
    st.title("🔒 Login Required")
    pwd_input = st.text_input("Enter Password", type="password")
    
    if st.button("Log In"):
        if pwd_input == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("😕 Password incorrect")
            
    return False

# STOP HERE IF NOT LOGGED IN
if not check_password():
    st.stop()

# ==========================================
# 1. MAIN APPLICATION START
# ==========================================

st.title("Brewery Invoice Parser ⚡")

# --- DATA PROCESSING FUNCTIONS ---

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
    grouped = df.groupby(group_cols)
    
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
        matrix_rows.append(row)
        
    matrix_df = pd.DataFrame(matrix_rows)
    
    base_cols = ['Supplier_Name', 'Collaborator', 'Product_Name', 'ABV']
    format_cols = []
    for i in range(1, 4):
        format_cols.extend([f'Format{i}', f'Pack_Size{i}', f'Volume{i}', f'Item_Price{i}'])
    
    final_cols = base_cols + [c for c in format_cols if c in matrix_df.columns]
    return matrix_df[final_cols]

def create_product_checker(df):
    if df is None or df.empty: return pd.DataFrame()
    
    checker_rows = []
    for _, row in df.iterrows():
        abv = str(row['ABV']).replace('%', '') if row['ABV'] else ""
        parts = [str(row['Supplier_Name']), str(row['Product_Name']), abv, str(row['Format'])]
        col1 = " / ".join([p for p in parts if p and p.lower() != 'none'])
        
        pack = str(row['Pack_Size']).replace('.0', '') if row['Pack_Size'] else ""
        vol = str(row['Volume'])
        col2 = f"{pack}x{vol}" if (pack and pack != '0' and pack != '1') else vol
            
        checker_rows.append({"ERP_String": col1, "Size_String": col2})
        
    return pd.DataFrame(checker_rows).drop_duplicates()

# --- SESSION STATE ---
if 'header_data' not in st.session_state: st.session_state.header_data = None
if 'line_items' not in st.session_state: st.session_state.line_items = None
if 'matrix_data' not in st.session_state: st.session_state.matrix_data = None
if 'checker_data' not in st.session_state: st.session_state.checker_data = None

# --- SIDEBAR ---
with st.sidebar:
    st.header("Settings")
    
    # Attempt to load API Key from Secrets
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("API Key loaded from Secrets 🔑")
    else:
        api_key = None 

    # The Form
    with st.form(key='process_form'):
        if not api_key:
            api_key = st.text_input("Google API Key", type="password")
        
        st.info("Logic loaded from `knowledge_base.py`")
        st.divider()
        
        st.subheader("🧪 The Lab")
        st.caption("Test a new rule here. Press Ctrl+Enter to apply.")
        custom_rule = st.text_area("Inject Temporary Rule:", height=150)
        
        submit_button = st.form_submit_button("🚀 Process Invoice", type="primary")
    
    # Logout Button
    st.divider()
    if st.button("Log Out"):
        st.session_state.password_correct = False
        st.rerun()

# --- FILE UPLOADER ---
st.subheader("1. Upload Invoice")
uploaded_file = st.file_uploader("Drop PDF here", type="pdf")

if uploaded_file:
    if 'last_uploaded_file' not in st.session_state or st.session_state.last_uploaded_file != uploaded_file.name:
        st.session_state.header_data = None
        st.session_state.line_items = None
        st.session_state.matrix_data = None
        st.session_state.checker_data = None
        st.session_state.last_uploaded_file = uploaded_file.name

# --- MAIN LOGIC ---
if uploaded_file and api_key and submit_button:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        
        with st.spinner("OCR Scanning & Parsing..."):
            uploaded_file.seek(0)
            images = convert_from_bytes(uploaded_file.read(), dpi=300)
            full_text = ""
            for img in images:
                full_text += pytesseract.image_to_string(img) + "\n"

            injected_rules = ""
            if custom_rule:
                injected_rules = f"\n!!! URGENT USER OVERRIDE !!!\n{custom_rule}\n"

            prompt = f"""
            You are a financial data expert. Extract data to JSON.
            
            STRUCTURE:
            {{
                "header": {{
                    "Payable_To": "Name on Invoice Header",
                    "Invoice_Number": "...",
                    "Issue_Date": "...",
                    "Payment_Terms": "...",
                    "Due_Date": "...",
                    "Total_Net": 0.00,
                    "Total_VAT": 0.00,
                    "Total_Gross": 0.00,
                    "Total_Discount_Amount": 0.00,
                    "Shipping_Charge": 0.00
                }},
                "line_items": [
                    {{
                        "Supplier_Name": "...",
                        "Collaborator": "...",
                        "Product_Name": "...",
                        "ABV": "...",
                        "Format": "...",
                        "Pack_Size": "...",
                        "Volume": "...",
                        "Quantity": 1,
                        "Item_Price": 10.00
                    }}
                ]
            }}
            
            SUPPLIER RULEBOOK:
            {json.dumps(SUPPLIER_RULEBOOK, indent=2)}
            
            GLOBAL RULES:
            {GLOBAL_RULES_TEXT}
            
            {injected_rules}
            
            Return ONLY valid JSON.
            
            INVOICE TEXT:
            {full_text}
            """

            response = model.generate_content(prompt)
            json_text = response.text.strip().replace("```json", "").replace("```", "")
            data = json.loads(json_text)
            
            st.session_state.header_data = pd.DataFrame([data['header']])
            
            df_lines = pd.DataFrame(data['line_items'])
            
            # --- CLEANING STEP ---
            df_lines = clean_product_names(df_lines)
            
            cols = ["Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price", "Quantity"]
            existing_cols = [c for c in cols if c in df_lines.columns]
            st.session_state.line_items = df_lines[existing_cols]

            st.session_state.matrix_data = create_product_matrix(st.session_state.line_items)
            st.session_state.checker_data = create_product_checker(st.session_state.line_items)

    except Exception as e:
        st.error(f"Error: {e}")

# --- DISPLAY RESULTS ---
if st.session_state.header_data is not None:
    
    if custom_rule:
        st.success("✅ Processed using Custom Rules")
        try:
            detected_supplier = st.session_state.header_data.iloc[0]['Payable_To']
        except:
            detected_supplier = "Unknown Supplier"
        formatted_snippet = f'"{detected_supplier}": """\n{custom_rule}\n""",'
        with st.expander("📩 Developer Snippet"):
            st.code(formatted_snippet, language="python")

    st.divider()
    
    tab1, tab2, tab3, tab4 = st.tabs(["📄 Header", "📝 Line Items", "📊 Matrix", "🔍 Product Checker"])
    
    with tab1:
        st.dataframe(st.session_state.header_data, use_container_width=True)
        csv_head = st.session_state.header_data.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Header CSV", csv_head, "header.csv", "text/csv")
        
    with tab2:
        edited_lines = st.data_editor(st.session_state.line_items, num_rows="dynamic", use_container_width=True)
        csv_lines = edited_lines.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Lines CSV", csv_lines, "lines.csv", "text/csv")
        
    with tab3:
        if st.session_state.matrix_data is not None:
            st.dataframe(st.session_state.matrix_data, use_container_width=True)
            csv_matrix = st.session_state.matrix_data.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Matrix CSV", csv_matrix, "matrix.csv", "text/csv")
            
    with tab4:
        st.subheader("ERP Product Checker")
        if st.session_state.checker_data is not None:
            st.dataframe(st.session_state.checker_data, use_container_width=True)
            csv_check = st.session_state.checker_data.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Checker CSV", csv_check, "product_checker.csv", "text/csv")
