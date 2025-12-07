import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import os

st.set_page_config(layout="wide", page_title="Learning Invoice Parser")
st.title("Brewery Invoice Parser (Self-Learning)")

# --- 1. PERSISTENCE LAYER (The "Memory") ---

DEFAULT_RULES = {
    "Generic / Unknown": "Use standard logic.",
    "Simple Things Fermentations": """
    - PREFIX REMOVAL: Remove codes like "30EK", "9G", "12x 440" from the start.
    - COLLABORATION: Look for "STF/Partner".
    - CODE MAPPING: "30EK"->EcoKeg 30 Litre; "9G"->Cask 9 Gallon.
    - DISCOUNT: Apply 15% discount.
    """,
    "Polly's Brew Co.": """
    - PRODUCT NAME: Stop extracting at the first hyphen (-).
      Example: "Rivers of Green - Pale Ale" -> Product Name: "Rivers Of Green".
    - Handle 18-packs.
    """,
    "North Riding Brewery": """
    - DISCOUNT: Handle '(discount)' line item (negative total). 
      Divide total discount by count of beer units. Subtract from Unit Price.
    """,
    "Neon Raptor": "Handle 'Discount' column. Merge multi-line descriptions."
}

def load_rules():
    if os.path.exists("rules.json"):
        with open("rules.json", "r") as f:
            return json.load(f)
    return DEFAULT_RULES

def save_rules(rules_dict):
    with open("rules.json", "w") as f:
        json.dump(rules_dict, f, indent=4)

# Load rules into session state
if 'rules' not in st.session_state:
    st.session_state.rules = load_rules()

# --- 2. GLOBAL CONSTANTS ---

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
Bottles | 330ml
Cans | 33cl
Cans | 44cl
PolyKeg | 30 Litre
""" # (Shortened for brevity, assumes full list is here)

GLOBAL_RULES_TEXT = f"""
1. **PRODUCT NAMES & COLLABORATIONS**:
   - **Collaborator**: Look for names indicating a partnership (e.g. "STF/Croft", "Polly's x Cloudwater"). Extract partner into "Collaborator".
   - **Product Name**: Extract core beer name ONLY. Remove styles (IPA, Stout) and prefixes.
   - **Title Case**: Convert Product Name to Title Case.

2. **STRICT FORMAT MAPPING**:
   - Map items to VALID FORMATS LIST.
   - Convert ml to cl. L/Ltr to Litre.

3. **Pack Size**:
   - Bottles/Cans: Extract pack count. Kegs: Blank.

4. **Item_Price**: NET price per single unit.

VALID FORMATS LIST:
{VALID_FORMATS}
"""

# --- 3. SIDEBAR (Configuration & Teaching) ---
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Google API Key", type="password")
    
    st.header("🧠 Teach the AI")
    
    # 1. Select Supplier
    supplier_options = list(st.session_state.rules.keys())
    selected_supplier = st.selectbox("Select Supplier to Edit", supplier_options)
    
    # 2. Add New Supplier Option
    new_supplier_name = st.text_input("Or add new supplier:")
    if st.button("Add Supplier"):
        if new_supplier_name and new_supplier_name not in st.session_state.rules:
            st.session_state.rules[new_supplier_name] = "Enter rules here..."
            save_rules(st.session_state.rules)
            st.rerun()

    # 3. Edit The Rules (The Feedback Loop)
    st.caption(f"Current Rules for: **{selected_supplier}**")
    current_rule_text = st.session_state.rules[selected_supplier]
    
    updated_rule_text = st.text_area(
        "Edit Logic:", 
        value=current_rule_text, 
        height=200,
        help="Type specific instructions here. Example: 'Note: Croft 3 is a brewery name, treat it as a collaborator.'"
    )
    
    if st.button("💾 Save Updated Rules"):
        st.session_state.rules[selected_supplier] = updated_rule_text
        save_rules(st.session_state.rules)
        st.success("Brain updated! Re-run the invoice to see changes.")

# --- 4. MAIN APP ---
uploaded_file = st.file_uploader("Upload Invoice (PDF)", type="pdf")

if uploaded_file and api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    # Determine active rules based on selection
    # If user added a new supplier in sidebar, use that, otherwise use selection
    active_supplier_rules = st.session_state.rules[selected_supplier]

    try:
        with st.spinner("OCR Scanning..."):
            images = convert_from_bytes(uploaded_file.read(), dpi=300)
            full_text = ""
            for img in images:
                full_text += pytesseract.image_to_string(img) + "\n"

        # AI Prompt
        prompt = f"""
        Extract invoice line items into a JSON list.
        
        COLUMNS TO EXTRACT:
        "Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price" (Net).
        
        GLOBAL RULES:
        {GLOBAL_RULES_TEXT}

        SUPPLIER SPECIFIC RULES (High Priority):
        {active_supplier_rules}
        
        Return ONLY valid JSON.
        
        INVOICE TEXT:
        {full_text}
        """

        with st.spinner(f"AI is processing using rules for '{selected_supplier}'..."):
            response = model.generate_content(prompt)
            json_text = response.text.strip().replace("```json", "").replace("```", "")
            data = json.loads(json_text)
            
            df = pd.DataFrame(data)
            
            # Show Editable Dataframe (User can fix typos manually before downloading)
            st.subheader("Extracted Data (Editable)")
            edited_df = st.data_editor(df, num_rows="dynamic", use_container_width=True)
            
            # Download Button
            csv = edited_df.to_csv(index=False).encode('utf-8')
            st.download_button("Download CSV", csv, "standardized_invoice.csv", "text/csv")

    except Exception as e:
        st.error(f"Error: {e}")
