import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import re
import io
from streamlit_gsheets import GSheetsConnection
from thefuzz import process
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Import Brain
from knowledge_base import GLOBAL_RULES_TEXT, SUPPLIER_RULEBOOK

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")

# ... (Auth & Data Functions remain same) ...

# --- GOOGLE DRIVE FUNCTIONS ---
def get_drive_service():
    """Authenticate with Google Drive using Streamlit Secrets"""
    creds_dict = st.secrets["connections"]["gsheets"]
    creds = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=['https://www.googleapis.com/auth/drive.readonly']
    )
    return build('drive', 'v3', credentials=creds)

def list_files_in_folder(folder_id):
    """Recursively list all PDFs in folder"""
    service = get_drive_service()
    query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    return results.get('files', [])

def download_file_from_drive(file_id):
    """Download file into memory buffer"""
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    file_stream = io.BytesIO()
    downloader = MediaIoBaseDownload(file_stream, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    file_stream.seek(0)
    return file_stream

# ... (Session State setup) ...

# --- SIDEBAR ---
with st.sidebar:
    st.header("Settings")
    # ... (API Key logic) ...
    
    st.divider()
    
    st.header("📂 Google Drive")
    folder_id = st.text_input("Folder ID", help="Copy the ID from the Drive URL")
    
    if folder_id:
        if st.button("🔍 Scan Folder"):
            try:
                files = list_files_in_folder(folder_id)
                st.session_state.drive_files = files
                st.success(f"Found {len(files)} PDFs")
            except Exception as e:
                st.error(f"Access Denied: {e}")

# ... (Main App Logic) ...

st.title("Brewery Invoice Parser ⚡")

# TABS FOR SOURCE
tab_upload, tab_drive = st.tabs(["⬆️ Manual Upload", "☁️ Google Drive"])

selected_file_obj = None

# 1. Manual Upload Tab
with tab_upload:
    uploaded_file = st.file_uploader("Drop PDF here", type="pdf")
    if uploaded_file:
        selected_file_obj = uploaded_file

# 2. Google Drive Tab
with tab_drive:
    if 'drive_files' in st.session_state and st.session_state.drive_files:
        # Create a dictionary for the dropdown: "Filename" -> "File ID"
        file_map = {f['name']: f['id'] for f in st.session_state.drive_files}
        selected_filename = st.selectbox("Select Invoice to Process", options=list(file_map.keys()))
        
        if selected_filename:
            st.info(f"Selected: {selected_filename}")
            # We don't download yet to save bandwidth, we download when Process is clicked
            st.session_state.selected_drive_id = file_map[selected_filename]
            st.session_state.selected_drive_name = selected_filename
    else:
        st.info("Enter a Folder ID in the sidebar and click Scan.")

# --- PROCESS LOGIC ---
if st.button("🚀 Process Invoice", type="primary"):
    
    # Determine source
    pdf_stream = None
    filename = "unknown.pdf"
    
    if uploaded_file:
        pdf_stream = uploaded_file
        filename = uploaded_file.name
    elif 'selected_drive_id' in st.session_state:
        with st.spinner(f"Downloading {st.session_state.selected_drive_name} from Drive..."):
            pdf_stream = download_file_from_drive(st.session_state.selected_drive_id)
            filename = st.session_state.selected_drive_name
    
    if pdf_stream:
        # ... (Run existing OCR & AI Logic using pdf_stream) ...
