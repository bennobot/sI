import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import os
from streamlit_gsheets import GSheetsConnection

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser (Cloud)")
st.title("Brewery Invoice Parser ☁️ (Gemini 2.5 + Sheets)")

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

3. **Pack Size**:
   - Bottles/Cans: Extract pack count.
   - Kegs/Casks: Leave blank/null.

4. **Item_Price**: 
   - NET price per single unit (per keg or per case).
   - Apply line-item discounts.

VALID FORMATS LIST:
{VALID_FORMATS}
"""

# --- 2. PERSISTENCE: GOOGLE SHEETS CONNECTION ---

DEFAULT_SUPPLIER_RULES = {
    "Generic / Unknown": "Use standard global logic.",
    "Simple Things Fermentations": "PREFIX REMOVAL: Remove codes like '30EK'. COLLABORATION: Look for 'STF/Partner'. DISCOUNT: 15%.",
    "Polly's Brew Co.": "PRODUCT NAME: Stop at first hyphen. Handle 18-packs.",
    "North Riding Brewery": "DISCOUNT: Handle '(discount)' line item (negative total). Divide by units.",
    "Neon Raptor": "Handle 'Discount' column. Merge multi-line descriptions."
}

def load_rules_from_sheet():
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df = conn.read(worksheet="Rules", ttl=0)
        # Convert to dict
        rules_dict = pd.Series(df.Rules.values, index=df.Supplier).to_dict()
        
        # Ensure Generic exists
        if "Generic / Unknown" not in rules_dict:
            rules_dict["Generic / Unknown"] = "Use standard global logic."
        return rules_dict
    except Exception as e:
        # Fallback if sheet is empty or connection fails
        return DEFAULT_SUPPLIER_RULES

def save_rule_to_sheet(supplier, new_rule_text):
    conn = st.connection("gsheets", type=GSheetsConnection)
    df = conn.read(worksheet="Rules", ttl=0)
    
    # Check if supplier exists
    if supplier in df['Supplier'].values:
        df.loc[df['Supplier'] == supplier, 'Rules'] = new_rule_text
    else:
        new_row = pd.DataFrame([{"Supplier": supplier, "Rules": new_rule_text}])
        df = pd.concat([df, new_row], ignore_index=True)
        
    conn.update(worksheet="Rules", data=df)
    st.cache_data.clear()

# Initialize Rules
if 'rules' not in st.session_state:
    st.session_state.rules = load_rules_from_sheet()

# --- 3. SIDEBAR (SETTINGS & TEACHING) ---
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Google API Key", type="password")
    
    st.markdown("---")
    st.subheader("🧠 Supplier Logic (Cloud)")
    
    # Select Supplier
    supplier_options = list(st.session_state.rules.keys())
    selected_supplier = st.selectbox("Select Supplier", supplier_options)
    
    # Add New Supplier
    new_supplier = st.text_input("Add new supplier:")
    if st.button("Create Locally"):
        if new_supplier and new_supplier not in st.session_state.rules:
            st.session_state.rules[new_supplier] = "Enter rules..."
            st.rerun()
            
    # Edit Rules (F
