import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import os

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser (2.5 Flash)")
st.title("Brewery Invoice Parser ⚡ (Gemini 2.5)")

# --- 1. CONFIGURATION: MASTER DATA ---

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

GLOBAL_RULES_TEXT = f"""
1. **PRODUCT NAMES & COLLABORATORS**:
   - **Collaborator**: Look for names indicating a partnership (e.g. "STF/Croft", "Polly's x Cloudwater"). Extract the partner name into the "Collaborator" field.
   - **Product Name**: Extract the core beer name ONLY. Remove size info, prefixes, and the collaborator name.
   - **Styles**: Remove generic style descriptors (IPA, Stout, Pale Ale) from the name unless it is the ONLY name.
   - **Title Case**: Convert Product Name to Title Case (e.g. "DARK ISLAND" -> "Dark Island").

2. **STRICT FORMAT MAPPING**:
   - Map every item to the "VALID FORMATS LIST" below.
   - "Firkin" -> "Cask" / "9 Gallon".
   - "Pin" -> "Cask" / "4.5 Gallon".
   - Convert ml to cl (e.g. 440ml -> 44cl).
   - Convert L/Ltr -> Litre.

3. **Pack Size**:
   - Bottles/Cans: Extract pack count.
   - Kegs/Casks: Leave blank/null.

4. **Item_Price**: 
   - NET price per single unit (per keg or per case).
   - Apply line-item discounts.

VALID FORMATS LIST:
{VALID_FORMATS}
"""

# --- 2. PERSISTENCE: RULES MEMORY ---

DEFAULT_SUPPLIER_RULES = {
    "Generic / Unknown": "Use standard global logic.",
    
    "Simple Things Fermentations": """
    - PREFIX REMOVAL: Remove start codes like "30EK", "9G", "12x 440".
    - COLLABORATION: 
      1. Look for "STF/[Partner]".
      2. EXCEPTION: If text is "STF/Croft 3...", the Collaborator is "Croft 3" (not just Croft).
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

def load_rules():
    if os.path.exists("rules.json"):
        with open("rules.json", "r") as f:
            return json.load(f)
    return DEFAULT_SUPPLIER_RULES

def save_rules(rules_dict):
    with open("rules.json", "w") as f:
        json.dump(rules_dict, f, indent=4)

if 'rules' not in st.session_state:
    st.session_state.rules = load_rules()

# --- 3. SIDEBAR (SETTINGS & TEACHING) ---
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Google API Key", type="password")
    
    st.markdown("---")
    st.subheader("🧠 Supplier Logic")
    
    # Select Supplier
    supplier_options = list(st.session_state.rules.keys())
    selected_supplier = st.selectbox("Select Supplier", supplier_options)
    
    # Add New Supplier
    new_supplier = st.text_input("Add new supplier:")
    if st.button("Create"):
        if new_supplier and new_supplier not in st.session_state.rules:
            st.session_state.rules[new_supplier] = "Enter rules..."
            save_rules(st.session_state.rules)
            st.rerun()
            
    # Edit Rules (Feedback Loop)
    current_text = st.session_state.rules[selected_supplier]
    updated_text = st.text_area(f"Edit rules for {selected_supplier}:", value=current_text, height=150)
    
    if st.button("💾 Save Rules"):
        st.session_state.rules[selected_supplier] = updated_text
        save_rules(st.session_state.rules)
        st.success("Saved!")

# --- 4. MAIN APP ---
uploaded_file = st.file_uploader("Upload Invoice (PDF)", type="pdf")

if uploaded_file and api_key:
    try:
        genai.configure(api_key=api_key)
        
        # UPDATED: Using the specific 2.5 Flash model from your list
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        
        with st.spinner("OCR Scanning..."):
            images = convert_from_bytes(uploaded_file.read(), dpi=300)
            full_text = ""
            for img in images:
                full_text += pytesseract.image_to_string(img) + "\n"

        # Construct Prompt
        active_rules = st.session_state.rules[selected_supplier]
        
        prompt = f"""
        Extract invoice line items into a JSON list.
        
        COLUMNS TO EXTRACT:
        "Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price" (Net).
        
        GLOBAL RULES:
        {GLOBAL_RULES_TEXT}

        SUPPLIER SPECIFIC RULES:
        {active_rules}
        
        Return ONLY valid JSON.
        
        INVOICE TEXT:
        {full_text}
        """

        with st.spinner(f"AI Processing ({selected_supplier})..."):
            response = model.generate_content(prompt)
            json_text = response.text.strip().replace("```json", "").replace("```", "")
            data = json.loads(json_text)
            
            df = pd.DataFrame(data)
            
            # Column Ordering
            cols = ["Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price"]
            existing_cols = [c for c in cols if c in df.columns]
            df = df[existing_cols]
            
            # Editable Dataframe
            st.success(f"Found {len(df)} items.")
            edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)
            
            # Download
            csv = edited_df.to_csv(index=False).encode('utf-8')
            st.download_button("Download CSV", csv, "invoice.csv", "text/csv")

    except Exception as e:
        st.error(f"Error: {e}")
