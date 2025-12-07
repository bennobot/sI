import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")
st.title("Brewery Invoice Parser ⚡ (Auto-Detect + Matrix View)")

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
   - **Collaborator**: Look for names indicating a partnership (e.g. "STF/Croft", "Polly's x Cloudwater"). Extract the partner name into the "Collaborator" field.
   - **Product Name**: Extract the core beer name ONLY. Remove size info, prefixes, and the collaborator name.
   - **Styles**: Remove generic style descriptors (IPA, Stout, Pale Ale) from the name unless it is the ONLY name.
   - **Title Case**: Convert Product Name to Title Case (e.g. "DARK ISLAND" -> "Dark Island").

2. **STRICT FORMAT MAPPING**:
   - Map every item to the "VALID FORMATS LIST" below.
   
   **SPECIFIC KEG/CASK RULES:**
   - "Firkin" -> "Cask" / "9 Gallon".
   - "Pin" -> "Cask" / "4.5 Gallon".
   - **KEGSTAR / eKEG Logic**:
     - IF description contains "Kegstar" OR "eKeg":
       - CHECK SIZE: If size is "41L" or "41 Litre" -> Map to Format: "Cask", Volume: "9 Gallon".
       - ELSE (e.g. 30L, 50L) -> Map to Format: "Steel Keg" (preserve volume).

   **UNIT CONVERSIONS:**
   - Convert ml to cl (e.g. 440ml -> 44cl).
   - Convert L/Ltr -> Litre.

3. **Pack Size vs Quantity**:
   - **Pack_Size**: How many items inside a case (e.g. 12, 18, 24). Blank for Kegs.
   - **Quantity**: The number of units ordered (e.g. 5 kegs, or 10 cases).

4. **Item_Price**: 
   - NET price per single unit (per keg or per case).
   - Apply line-item discounts.

VALID FORMATS LIST:
{VALID_FORMATS}
"""

# ==========================================
# 3. SUPPLIER RULEBOOK
# ==========================================

SUPPLIER_RULEBOOK = {
    "Simple Things Fermentations": """
    - PREFIX REMOVAL: Remove start codes like "30EK", "9G", "12x 440".
    - COLLABORATION: 
      1. Look for "STF/[Partner]".
      2. EXCEPTION: If text is "STF/Croft 3...", the Collaborator is "Croft 3".
    - CODE MAPPING: "30EK"->EcoKeg 30 Litre; "9G"->Cask 9 Gallon.
    - DISCOUNT: Apply 15% discount.
    """,
    
    "Polly's Brew Co.": """
    - PRODUCT NAME: Stop extracting at the first hyphen (-).
      Example: "Rivers of Green - Pale Ale" -> Product Name: "Rivers Of Green".
    - PACK SIZE: Watch for 18-packs (e.g. 18 x 440ml).
    """,
    
    "North Riding Brewery": """
    - DISCOUNT: Handle '(discount)' line item (negative total). 
      Divide total discount by count of beer units. Subtract this amount from the Unit Price.
    """,
    
    "Neon Raptor": "Handle 'Discount' column. Merge multi-line descriptions."
}

# ==========================================
# 4. DATA PROCESSING FUNCTIONS
# ==========================================

def create_product_matrix(df):
    """
    Transforms the line-item dataframe into a Product Matrix view.
    Groups by Product and spreads formats across columns.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    # Fill NaN to allow grouping
    df = df.fillna("")
    
    # Group by the unique product definition
    group_cols = ['Supplier_Name', 'Collaborator', 'Product_Name', 'ABV']
    grouped = df.groupby(group_cols)
    
    matrix_rows = []
    
    for name, group in grouped:
        # Base Row Information
        row = {
            'Supplier_Name': name[0],
            'Collaborator': name[1],
            'Product_Name': name[2],
            'ABV': name[3]
        }
        
        # Add formats (Limit to 3)
        for i, (_, item) in enumerate(group.iterrows()):
            if i >= 3: break # Safety limit
            
            suffix = str(i + 1) # 1, 2, 3
            row[f'Format{suffix}'] = item['Format']
            row[f'Pack_Size{suffix}'] = item['Pack_Size']
            row[f'Volume{suffix}'] = item['Volume']
            row[f'Item_Price{suffix}'] = item['Item_Price']
            
        matrix_rows.append(row)
        
    matrix_df = pd.DataFrame(matrix_rows)
    
    # Reorder columns to ensure Format1, PackSize1, etc appear in order
    base_cols = ['Supplier_Name', 'Collaborator', 'Product_Name', 'ABV']
    format_cols = []
    for i in range(1, 4):
        format_cols.extend([f'Format{i}', f'Pack_Size{i}', f'Volume{i}', f'Item_Price{i}'])
        
    # Filter for columns that actually exist (in case we only found 1 format max)
    final_cols = base_cols + [c for c in format_cols if c in matrix_df.columns]
    
    return matrix_df[final_cols]

# ==========================================
# 5. MAIN APPLICATION
# ==========================================

if 'processed_data' not in st.session_state:
    st.session_state.processed_data = None
if 'matrix_data' not in st.session_state:
    st.session_state.matrix_data = None

with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Google API Key", type="password")
    st.info("Auto-detects supplier. Outputs standard CSV and Product Matrix CSV.")

st.subheader("1. Upload Invoice")
uploaded_file = st.file_uploader("Drop PDF here", type="pdf")

if uploaded_file:
    if 'last_uploaded_file' not in st.session_state or st.session_state.last_uploaded_file != uploaded_file.name:
        st.session_state.processed_data = None
        st.session_state.matrix_data = None
        st.session_state.last_uploaded_file = uploaded_file.name

if uploaded_file and api_key:
    if st.button("🚀 Process Invoice", type="primary"):
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel('models/gemini-2.5-flash')
            
            with st.spinner("OCR Scanning & Processing..."):
                uploaded_file.seek(0)
                images = convert_from_bytes(uploaded_file.read(), dpi=300)
                full_text = ""
                for img in images:
                    full_text += pytesseract.image_to_string(img) + "\n"

                prompt = f"""
                You are a data entry expert. 
                
                STEP 1: Identify the Supplier Name.
                STEP 2: Check the "SUPPLIER RULEBOOK". Apply rules if match found.
                STEP 3: Apply "GLOBAL RULES".
                STEP 4: Extract line items to JSON.
                
                SUPPLIER RULEBOOK:
                {json.dumps(SUPPLIER_RULEBOOK, indent=2)}
                
                GLOBAL RULES:
                {GLOBAL_RULES_TEXT}
                
                COLUMNS:
                "Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Quantity", "Item_Price" (Net).
                
                Return ONLY valid JSON.
                
                INVOICE TEXT:
                {full_text}
                """

                response = model.generate_content(prompt)
                json_text = response.text.strip().replace("```json", "").replace("```", "")
                data = json.loads(json_text)
                
                # Create Base Dataframe
                df = pd.DataFrame(data)
                cols = ["Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price", "Quantity"]
                existing_cols = [c for c in cols if c in df.columns]
                st.session_state.processed_data = df[existing_cols]

                # Create Matrix Dataframe
                st.session_state.matrix_data = create_product_matrix(st.session_state.processed_data)

        except Exception as e:
            st.error(f"Error: {e}")

# Display Results
if st.session_state.processed_data is not None:
    st.divider()
    
    # TAB 1: Standard Invoice View
    st.subheader("2. Standard Invoice View (Line Items)")
    edited_df = st.data_editor(st.session_state.processed_data, num_rows="dynamic", use_container_width=True)
    
    csv = edited_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download Invoice CSV",
        data=csv,
        file_name="invoice_lines.csv",
        mime="text/csv"
    )

    st.divider()

    # TAB 2: Product Matrix View
    st.subheader("3. Product Matrix View (Formats Horizontal)")
    if st.session_state.matrix_data is not None:
        st.dataframe(st.session_state.matrix_data, use_container_width=True)
        
        matrix_csv = st.session_state.matrix_data.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Matrix CSV",
            data=matrix_csv,
            file_name="product_matrix.csv",
            mime="text/csv"
        )
