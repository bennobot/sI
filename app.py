import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")
st.title("Brewery Invoice Parser ⚡ (Wholesaler + Matrix)")

# ==========================================
# 1. MASTER DATA
# ==========================================

VALID_FORMATS = """
Cask | 9 Gallon
Cask | 4.5 Gallon
Cask | 5 Litre
KeyKeg | 10 Litre
KeyKeg | 20 Litre
KeyKeg | 30 Litre
KeyKeg | 12 Litre
KeyKeg | 50 Litre
Steel Keg | 20 Litre
Steel Keg | 30 Litre
Steel Keg | 50 Litre
Steel Keg | 12 Litre
Bag in Box | 10 Litre
Bag in Box | 20 Litre
Bag in Box | 5 Litre
Bottles | 33cl
Bottles | 50cl
Bottles | 75cl
Bottles | 66cl
Bottles | 35cl
Bottles | 56.8cl
Bottles | 70cl
Bottles | 20cl
Bottles | 25cl
Bottles | 24cl
Bottles | 27.5cl
Bottles | 35.5cl
Bottles | 37.5cl
Bottles | 10cl
Bottles | 150cl
Bottles | 34cl
Bottles | 30cl
Cans | 33cl
Cans | 44cl
Cans | 25cl
Cans | 56.8cl
Cans | 50cl
Cans | 35cl
Cans | 47.3cl
Cans | 18.7cl
Cans | 10cl
Cans | 40.3cl
Cans | 35.5cl
Cans | 12.5cl
Cans | 47cl
Cans | 14cl
PolyKeg | 10 Litre
PolyKeg | 20 Litre
PolyKeg | 30 Litre
PolyKeg | 12 Litre
PolyKeg | 50 Litre
UniKeg | 10 Litre
UniKeg | 20 Litre
UniKeg | 30 Litre
UniKeg | 12 Litre
UniKeg | 50 Litre
Dolium Keg | 10 Litre
Dolium Keg | 20 Litre
Dolium Keg | 30 Litre
Dolium Keg | 12 Litre
Dolium Keg | 50 Litre
EcoKeg | 10 Litre
EcoKeg | 20 Litre
EcoKeg | 30 Litre
EcoKeg | 12 Litre
EcoKeg | 50 Litre
US Dolium Keg | 20 Litre
Cellar Equipment | 250 Pack
"""

# ==========================================
# 2. GLOBAL RULES
# ==========================================

GLOBAL_RULES_TEXT = f"""
1. **PRODUCT NAMES & COLLABORATORS**:
   - **Collaborator**: Look for names indicating a partnership (e.g. "STF/Croft"). Extract the partner into "Collaborator".
   - **Product Name**: Extract the core beer name ONLY. Remove size info, prefixes, and collaborator.
   - **Styles**: Remove generic style descriptors (IPA, Stout) unless it is the ONLY name.
   - **Title Case**: Convert Product Name to Title Case.

2. **STRICT FORMAT MAPPING**:
   - Map every item to the "VALID FORMATS LIST" below.
   - **KEGSTAR / eKEG Logic**:
     - IF description contains "Kegstar" OR "eKeg":
       - CHECK SIZE: If size is "41L" or "41 Litre" -> Map to Format: "Cask", Volume: "9 Gallon".
       - ELSE (e.g. 30L, 50L) -> Map to Format: "Steel Keg" (preserve volume).
   - Convert ml to cl (e.g. 440ml -> 44cl).
   - Convert L/Ltr -> Litre.

3. **Pack Size vs Quantity**:
   - **Pack_Size**: Items per case (e.g. 12, 24). Blank for Kegs.
   - **Quantity**: Units ordered.

4. **Item_Price**: NET price per single unit. Apply discounts.

VALID FORMATS LIST:
{VALID_FORMATS}
"""

# ==========================================
# 3. SUPPLIER RULEBOOK (Advanced Wholesaler Logic)
# ==========================================

SUPPLIER_RULEBOOK = {
    "James Clay and Sons": """
    - TYPE: Wholesaler. The Invoice Header says 'James Clay', but the line items are from various breweries.
    - STRATEGY: You must SPLIT the Description into 'Supplier_Name' and 'Product_Name' using your internal knowledge of beer brands.
    - RULES:
      1. **Supplier_Name**: Identify the actual Brewer/Brand (e.g. "Augustiner", "Duvel", "Brasserie Lefebvre").
         - If the brand is missing (e.g. "Blanche de Bruxelles"), infer it (e.g. "Brasserie Lefebvre").
      2. **Product_Name**: The remaining part of the beer name (e.g. "Hell", "Pils", "Vintage 2023").
      3. **Pack/Vol**: Extract from the "NxVol" pattern (e.g. 20x50cl).
    - EXAMPLE: 
      Input: "Augustiner Hell 20x50cl NRB"
      Output: Supplier="Augustiner-Brau", Product="Hell", Pack=20, Vol=50cl.
    """,

    "Simple Things Fermentations": """
    - PREFIX REMOVAL: Remove start codes like "30EK", "9G".
    - COLLABORATION: Look for "STF/[Partner]". If "STF/Croft 3", Collaborator is "Croft 3".
    - CODE MAPPING: "30EK"->EcoKeg 30 Litre; "9G"->Cask 9 Gallon.
    - DISCOUNT: Apply 15% discount.
    """,
    
    "Polly's Brew Co.": "PRODUCT NAME: Stop at first hyphen. Watch for 18-packs.",
    
    "North Riding Brewery": "DISCOUNT: Handle '(discount)' line item (negative total). Divide by units.",
    
    "Neon Raptor": "Handle 'Discount' column. Merge multi-line descriptions."
}

# ==========================================
# 4. PROCESSING FUNCTIONS
# ==========================================

def create_product_matrix(df):
    if df is None or df.empty: return pd.DataFrame()
    df = df.fillna("")
    
    # Group by the TRUE Supplier (Brewer) and Product
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

# ==========================================
# 5. MAIN APP
# ==========================================

if 'header_data' not in st.session_state: st.session_state.header_data = None
if 'line_items' not in st.session_state: st.session_state.line_items = None
if 'matrix_data' not in st.session_state: st.session_state.matrix_data = None

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Google API Key", type="password")
    st.info("Auto-detects supplier. Infers Wholesaler Brands.")

st.subheader("1. Upload Invoice")
uploaded_file = st.file_uploader("Drop PDF here", type="pdf")

if uploaded_file:
    if 'last_uploaded_file' not in st.session_state or st.session_state.last_uploaded_file != uploaded_file.name:
        st.session_state.header_data = None
        st.session_state.line_items = None
        st.session_state.matrix_data = None
        st.session_state.last_uploaded_file = uploaded_file.name

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
                        "Total_Discount_Amount": 0.00
                    }},
                    "line_items": [
                        {{
                            "Supplier_Name": "BREWERY Name (Not Wholesaler)",
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
                
                st.session_state.header_data = pd.DataFrame([data['header']])
                
                df_lines = pd.DataFrame(data['line_items'])
                cols = ["Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price", "Quantity"]
                existing_cols = [c for c in cols if c in df_lines.columns]
                st.session_state.line_items = df_lines[existing_cols]

                st.session_state.matrix_data = create_product_matrix(st.session_state.line_items)

        except Exception as e:
            st.error(f"Error: {e}")

if st.session_state.header_data is not None:
    st.divider()
    
    tab1, tab2, tab3 = st.tabs(["📄 Header Data", "📝 Line Items", "📊 Product Matrix"])
    
    with tab1:
        st.subheader("Invoice Header Details")
        st.dataframe(st.session_state.header_data, use_container_width=True)
        csv_head = st.session_state.header_data.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Header CSV", csv_head, "invoice_header.csv", "text/csv")
        
    with tab2:
        st.subheader("Line Items (Supplier = Brewery)")
        edited_lines = st.data_editor(st.session_state.line_items, num_rows="dynamic", use_container_width=True)
        csv_lines = edited_lines.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Download Lines CSV", csv_lines, "invoice_lines.csv", "text/csv")
        
    with tab3:
        st.subheader("Product Matrix")
        if st.session_state.matrix_data is not None:
            st.dataframe(st.session_state.matrix_data, use_container_width=True)
            csv_matrix = st.session_state.matrix_data.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Download Matrix CSV", csv_matrix, "product_matrix.csv", "text/csv")
