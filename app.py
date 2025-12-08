import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import re
from streamlit_gsheets import GSheetsConnection
from thefuzz import process # Fuzzy matching library

# Import the Brain
from knowledge_base import GLOBAL_RULES_TEXT, SUPPLIER_RULEBOOK

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")

# ... (Password Check Code remains here) ...

st.title("Brewery Invoice Parser ⚡")

# --- SUPPLIER NORMALIZATION FUNCTION ---
def get_master_supplier_list():
    """Fetch the clean list of suppliers from Google Sheets"""
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df = conn.read(worksheet="MasterData", ttl=600) # Cache for 10 mins
        return df['Supplier_Master'].dropna().tolist()
    except Exception:
        return [] # Return empty list if connection fails (Safe Mode)

def normalize_supplier_names(df, master_list):
    """
    Checks the 'Supplier_Name' column against the Master List.
    If a close match (>85% similarity) is found, replace it with the Master Name.
    """
    if df is None or df.empty or not master_list:
        return df
    
    def match_name(name):
        # Find best match in master list
        match, score = process.extractOne(name, master_list)
        if score >= 90: # High confidence threshold
            return match
        return name # Keep original if no good match

    if 'Supplier_Name' in df.columns:
        df['Supplier_Name'] = df['Supplier_Name'].apply(match_name)
        
    return df

# ... (clean_product_names, create_product_matrix functions remain here) ...

# --- SESSION STATE ---
if 'master_suppliers' not in st.session_state:
    st.session_state.master_suppliers = get_master_supplier_list()

# ... (Sidebar Code) ...

# ... (Main Logic) ...

            # ... (After AI generates JSON data) ...
            
            df_lines = pd.DataFrame(data['line_items'])
            
            # 1. CLEAN PRODUCT NAMES
            df_lines = clean_product_names(df_lines)
            
            # 2. NORMALIZE SUPPLIER NAMES (New Step)
            if st.session_state.master_suppliers:
                df_lines = normalize_supplier_names(df_lines, st.session_state.master_suppliers)
                # Also update header if needed
                if not st.session_state.header_data.empty:
                     # Check Payable_To against master list too
                     orig_payee = st.session_state.header_data.iloc[0]['Payable_To']
                     match, score = process.extractOne(orig_payee, st.session_state.master_suppliers)
                     if score >= 90:
                         st.session_state.header_data['Payable_To'] = match

            cols = ["Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Item_Price", "Quantity"]
            existing_cols = [c for c in cols if c in df_lines.columns]
            st.session_state.line_items = df_lines[existing_cols]

            st.session_state.matrix_data = create_product_matrix(st.session_state.line_items)
            st.session_state.checker_data = create_product_checker(st.session_state.line_items)

# ... (Rest of Display Code) ...
