import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import os

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")
st.title("Brewery Invoice Parser (Diagnostic Mode)")

# --- PERSISTENCE ---
DEFAULT_RULES = {
    "Generic / Unknown": "Use standard logic.",
    "Simple Things Fermentations": "PREFIX REMOVAL: Remove codes like '30EK', '9G'. COLLABORATION: Look for 'STF/Partner'.",
    "Polly's Brew Co.": "PRODUCT NAME: Stop at first hyphen. Handle 18-packs.",
    "North Riding Brewery": "DISCOUNT: Handle negative '(discount)' line.",
    "Neon Raptor": "Handle 'Discount' column. Merge multi-line descriptions."
}

if 'rules' not in st.session_state:
    st.session_state.rules = DEFAULT_RULES

# --- SIDEBAR & DIAGNOSTICS ---
with st.sidebar:
    st.header("Settings")
    api_key = st.text_input("Google API Key", type="password")
    
    st.markdown("---")
    st.subheader("🔧 Connection Test")
    if st.button("List Available Models"):
        if not api_key:
            st.error("Enter a key first!")
        else:
            try:
                genai.configure(api_key=api_key)
                # Ask Google what models we can use
                models = list(genai.list_models())
                names = [m.name for m in models if 'generateContent' in m.supported_generation_methods]
                
                if names:
                    st.success("Connection Successful! Your key supports:")
                    st.code("\n".join(names))
                    st.session_state.valid_model = names[0] # Auto-select the first working one
                else:
                    st.error("Connection worked, but no 'generateContent' models found. Check your Google Cloud Project settings.")
            except Exception as e:
                st.error(f"Connection Failed: {e}")

    # Supplier Selection
    supplier_options = list(st.session_state.rules.keys())
    selected_supplier = st.selectbox("Select Supplier", supplier_options)

# --- GLOBAL RULES ---
VALID_FORMATS = """Cask | 9 Gallon\nKeyKeg | 30 Litre\nCans | 44cl\n(Assume full list here...)"""

# --- MAIN APP ---
uploaded_file = st.file_uploader("Upload Invoice (PDF)", type="pdf")

if uploaded_file and api_key:
    # 1. OCR
    try:
        with st.spinner("OCR Scanning..."):
            images = convert_from_bytes(uploaded_file.read(), dpi=300)
            full_text = ""
            for img in images:
                full_text += pytesseract.image_to_string(img) + "\n"
    except Exception as e:
        st.error(f"OCR Failed. Did you add 'poppler-utils' to packages.txt? Error: {e}")
        st.stop()

    # 2. AI Processing
    # We try to use the model found in the diagnostic, or default to flash
    model_name = st.session_state.get('valid_model', 'models/gemini-1.5-flash')
    
    if st.button(f"Extract Data using {model_name}"):
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
            
            prompt = f"""
            Extract invoice line items into a JSON list.
            COLUMNS: "Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price".
            RULES: {st.session_state.rules[selected_supplier]}
            INVOICE TEXT: {full_text}
            """
            
            with st.spinner("AI Processing..."):
                response = model.generate_content(prompt)
                json_text = response.text.strip().replace("```json", "").replace("```", "")
                df = pd.DataFrame(json.loads(json_text))
                st.dataframe(df, use_container_width=True)
                
        except Exception as e:
            st.error(f"AI Error: {e}")
            st.write("Tip: If you got a 404, click 'List Available Models' in the sidebar to see what your key allows.")
