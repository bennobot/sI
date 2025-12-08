import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import re

# Import the Brain
from knowledge_base import GLOBAL_RULES_TEXT, SUPPLIER_RULEBOOK

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")
st.title("Brewery Invoice Parser ⚡")

# --- DATA PROCESSING FUNCTIONS ---

def clean_product_names(df):
    """
    Post-processing to remove artifacts like pipes (|), sizes, and excess whitespace 
    that the AI might leave behind in the Product Name column.
    """
    if df is None or df.empty: return df
    
    def cleaner(name):
        if not isinstance(name, str): return name
        # Remove pipe characters
        name = name.replace('|', '')
        # Remove common size patterns (e.g. 24x33cl, 9g, 30L) if they leaked in
        name = re.sub(r'\b\d+x\d+cl\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\b\d+g\b', '', name, flags=re.IGNORECASE)
        # Clean whitespace
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
    """
    Creates a specific formatted table for checking ERP codes.
    Format: "Supplier / Product / ABV / Format"
    Size: "PackSize x Volume" (or just Volume if PackSize is empty)
    """
    if df is None or df.empty: return pd.DataFrame()
    
    checker_rows = []
    for _, row in df.iterrows():
        # Build Col 1: Composite String
        # Handle ABV formatting
        abv = str(row['ABV']).replace('%', '') + "%" if row['ABV'] else ""
        
        # Build parts list, filtering out empty values
        parts = [
            str(row['Supplier_Name']),
            str(row['Product_Name']),
            abv,
            str(row['Format'])
        ]
        col1 = " / ".join([p for p in parts if p and p.lower() != 'none'])
        
        # Build Col 2: Size String
        pack = str(row['Pack_Size']).replace('.0', '') if row['Pack_Size'] else ""
        vol = str(row['Volume'])
        
        if pack and pack != '0' and pack != '1':
            col2 = f"{pack}x{vol}"
        else:
            col2 = vol
            
        checker_rows.append({
            "ERP_String": col1,
            "Size_String": col2
        })
        
    return pd.DataFrame(checker_rows).drop_duplicates()

# --- SESSION STATE ---
if 'header_data' not in st.session_state: st.session_state.header_data = None
if 'line_items' not in st.session_state: st.session_state.line_items = None
if 'matrix_data' not in st.session_state: st.session_state.matrix_data = None
if 'checker_data' not in st.session_state: st.session_state.checker_data = None

# --- SIDEBAR ---
with st.sidebar:
    st.header("Settings")
    
    with st.form(key='process_form'):
        api_key = st.text_input("Google API Key", type="password")
        st.info("Logic loaded from `knowledge_base.py`")
        st.divider()
        
        st.subheader("🧪 The Lab")
        st.caption("Test a new rule here. Press Ctrl+Enter to apply.")
        custom_rule = st.text_area("Inject Temporary Rule:", height=150)
        
        submit_button = st.form_submit_button("🚀 Process Invoice", type="primary")

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
    
    # Updated Tabs
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
