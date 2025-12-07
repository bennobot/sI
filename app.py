import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import os

# --- 1. SAFE IMPORT FOR SHEETS ---
try:
    from streamlit_gsheets import GSheetsConnection
    SHEETS_AVAILABLE = True
except ImportError:
    SHEETS_AVAILABLE = False

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")
st.title("Brewery Invoice Parser ⚡")

# --- 2. CONFIGURATION: MASTER DATA ---

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
   - **Collaborator**: Look for names indicating a partnership (e.g. "STF/Croft"). Extract partner name.
   - **Product Name**: Extract core beer name ONLY. Remove size info, prefixes, and collaborator.
   - **Styles**: Remove generic styles (IPA, Stout) unless it is the only name.
   - **Title Case**: Convert Product Name to Title Case.

2. **STRICT FORMAT MAPPING**:
   - Map items to "VALID FORMATS LIST".
   - **Kegstar / eKeg**: IF size is "41L" -> Cask 9 Gallon. ELSE -> Steel Keg.
   - Convert ml to cl. L/Ltr to Litre.

3. **Pack Size**: Bottles/Cans=Count. Kegs=Null.

4. **Item_Price**: NET price per single unit.

VALID FORMATS LIST:
{VALID_FORMATS}
"""

# --- 3. LOGIC MANAGER (Cloud or Local) ---

DEFAULT_SUPPLIER_RULES = {
    "Generic / Unknown": "Use standard global logic.",
    "Simple Things Fermentations": "PREFIX REMOVAL: Remove codes like '30EK'. COLLABORATION: Look for 'STF/Partner'. DISCOUNT: 15%.",
    "Polly's Brew Co.": "PRODUCT NAME: Stop at first hyphen. Handle 18-packs.",
    "North Riding Brewery": "DISCOUNT: Handle '(discount)' line item (negative total). Divide by units.",
    "Neon Raptor": "Handle 'Discount' column. Merge multi-line descriptions."
}

def load_rules():
    # Try Cloud first
    if SHEETS_AVAILABLE:
        try:
            conn = st.connection("gsheets", type=GSheetsConnection)
            df = conn.read(worksheet="Rules", ttl=0)
            return pd.Series(df.Rules.values, index=df.Supplier).to_dict()
        except Exception:
            pass # Fail silently to Local
            
    # Fallback to Local
    if os.path.exists("rules.json"):
        with open("rules.json", "r") as f:
            return json.load(f)
            
    return DEFAULT_SUPPLIER_RULES.copy()

def save_rules(supplier, text):
    # Try Cloud
    if SHEETS_AVAILABLE:
        try:
            conn = st.connection("gsheets", type=GSheetsConnection)
            df = conn.read(worksheet="Rules", ttl=0)
            if supplier in df['Supplier'].values:
                df.loc[df['Supplier'] == supplier, 'Rules'] = text
            else:
                new_row = pd.DataFrame([{"Supplier": supplier, "Rules": text}])
                df = pd.concat([df, new_row], ignore_index=True)
            conn.update(worksheet="Rules", data=df)
            st.cache_data.clear()
            st.toast("Saved to Cloud!")
            return
        except Exception:
            st.warning("Cloud save failed. Saving locally.")

    # Fallback Local
    current_rules = st.session_state.rules
    current_rules[supplier] = text
    with open("rules.json", "w") as f:
        json.dump(current_rules, f, indent=4)
    st.toast("Saved locally.")

if 'rules' not in st.session_state:
    st.session_state.rules = load_rules()

# --- 4. SIDEBAR ---
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Google API Key", type="password")
    
    st.divider()
    
    if not SHEETS_AVAILABLE:
        st.warning("⚠️ Cloud Database disabled (Library missing or connection failed). Using Local Mode.")
    
    # Supplier Selection
    supplier_options = list(st.session_state.rules.keys())
    if "Generic / Unknown" not in supplier_options:
        supplier_options.insert(0, "Generic / Unknown")
        
    selected_supplier = st.selectbox("Select Supplier", supplier_options)
    
    # Edit Rules
    current_text = st.session_state.rules.get(selected_supplier, "")
    updated_text = st.text_area("Edit Rules:", value=current_text, height=150)
    
    if st.button("💾 Update Rules"):
        st.session_state.rules[selected_supplier] = updated_text
        save_rules(selected_supplier, updated_text)

# --- 5. MAIN APP (THE UPLOADER) ---
st.subheader("1. Upload Invoice")
uploaded_file = st.file_uploader("Drop PDF here", type="pdf")

if uploaded_file and api_key:
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('models/gemini-2.5-flash')
        
        with st.spinner("OCR Scanning..."):
            images = convert_from_bytes(uploaded_file.read(), dpi=300)
            full_text = ""
            for img in images:
                full_text += pytesseract.image_to_string(img) + "\n"

        # Construct Prompt
        active_rules = st.session_state.rules.get(selected_supplier, "")
        
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
            
            # Ordering
            cols = ["Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price"]
            existing_cols = [c for c in cols if c in df.columns]
            df = df[existing_cols]
            
            # Results
            st.subheader("2. Extracted Data")
            edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)
            
            csv = edited_df.to_csv(index=False).encode('utf-8')
            st.download_button("Download CSV", csv, "invoice.csv", "text/csv")

    except Exception as e:
        st.error(f"Error: {e}")
