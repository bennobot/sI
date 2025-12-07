import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json

# Import the Brain
from knowledge_base import GLOBAL_RULES_TEXT, SUPPLIER_RULEBOOK

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")
st.title("Brewery Invoice Parser ⚡")

# --- DATA PROCESSING FUNCTION ---
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

# --- SESSION STATE ---
if 'header_data' not in st.session_state: st.session_state.header_data = None
if 'line_items' not in st.session_state: st.session_state.line_items = None
if 'matrix_data' not in st.session_state: st.session_state.matrix_data = None

# --- SIDEBAR ---
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Google API Key", type="password")
    st.info("Logic loaded from `knowledge_base.py`")

# --- FILE UPLOADER ---
st.subheader("1. Upload Invoice")
uploaded_file = st.file_uploader("Drop PDF here", type="pdf")

# Reset on new file
if uploaded_file:
    if 'last_uploaded_file' not in st.session_state or st.session_state.last_uploaded_file != uploaded_file.name:
        st.session_state.header_data = None
        st.session_state.line_items = None
        st.session_state.matrix_data = None
        st.session_state.last_uploaded_file = uploaded_file.name

# --- MAIN LOGIC ---
if uploaded_file and api_key:
    if st.button("🚀 Process Invoice", type="primary"):
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('models/gemini-2.5-flash')
            
            with st.spinner("OCR Scanning & Parsing..."):
                uploaded_file.seek(0)
                images = convert_from_bytes(uploaded_file.read(), dpi=300)
                full_text = ""
                for img in images:
                    full_text += pytesseract.image_to_string(img) + "\n"

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
                
                Return ONLY valid JSON.
                
                INVOICE TEXT:
                {full_text}
                """

                response = model.generate_content(prompt)
                json_text = response.text.strip().replace("```json", "").replace("```", "")
                data = json.loads(json_text)
                
                # Header
                st.session_state.header_data = pd.DataFrame([data['header']])
                
                # Lines
                df_lines = pd.DataFrame(data['line_items'])
                cols = ["Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price", "Quantity"]
                existing_cols = [c for c in cols if c in df_lines.columns]
                st.session_state.line_items = df_lines[existing_cols]

                # Matrix
                st.session_state.matrix_data = create_product_matrix(st.session_state.line_items)

        except Exception as e:
            st.error(f"Error: {e}")

# --- DISPLAY RESULTS ---
if st.session_state.header_data is not None:
    st.divider()
    
    tab1, tab2, tab3 = st.tabs(["📄 Header Data", "📝 Line Items", "📊 Product Matrix"])
    
    with tab1:
        st.subheader("Invoice Header Details")
        st.dataframe(st.session_state.header_data, use_container_width=True)
        csv_head = st.session_state.header_data.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Header CSV", csv_head, "invoice_header.csv", "text/csv")
        
    with tab2:
        st.subheader("Line Items (Landed Cost Applied)")
        edited_lines = st.data_editor(st.session_state.line_items, num_rows="dynamic", use_container_width=True)
        csv_lines = edited_lines.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Lines CSV", csv_lines, "invoice_lines.csv", "text/csv")
        
    with tab3:
        st.subheader("Product Matrix")
        if st.session_state.matrix_data is not None:
            st.dataframe(st.session_state.matrix_data, use_container_width=True)
            csv_matrix = st.session_state.matrix_data.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Download Matrix CSV", csv_matrix, "product_matrix.csv", "text/csv")
