import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json

# IMPORT THE BRAIN
from knowledge_base import GLOBAL_RULES_TEXT, SUPPLIER_RULEBOOK

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")
st.title("Brewery Invoice Parser ⚡")

# ... (Sidebar for API Key) ...

# ... (File Uploader) ...

if uploaded_file and api_key:
    if st.button("🚀 Process"):
        # ... (OCR Code) ...

        # THE PROMPT IS NOW CLEAN AND SIMPLE
        prompt = f"""
        You are a data entry expert. 
        
        STEP 1: Identify the Supplier.
        STEP 2: Check "SUPPLIER RULEBOOK". Apply rules if match found.
        STEP 3: Apply "GLOBAL RULES".
        STEP 4: Extract line items to JSON.
        
        SUPPLIER RULEBOOK:
        {json.dumps(SUPPLIER_RULEBOOK, indent=2)}
        
        GLOBAL RULES:
        {GLOBAL_RULES_TEXT}
        
        COLUMNS:
        "Supplier_Name", "Collaborator", "Product_Name", "ABV", "Format", "Pack_Size", "Volume", "Quantity", "Item_Price" (Net).
        
        INVOICE TEXT:
        {full_text}
        """
        
        # ... (Rest of processing code) ...
