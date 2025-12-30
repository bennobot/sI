import streamlit as st
import pandas as pd
from pdf2image import convert_from_bytes
import pytesseract
import google.generativeai as genai
import json
import re
import io
import requests
import time
from urllib.parse import quote
from urllib.request import Request, urlopen
from streamlit_gsheets import GSheetsConnection
from thefuzz import process, fuzz
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Import the Brain
from knowledge_base import GLOBAL_RULES_TEXT, SUPPLIER_RULEBOOK

st.set_page_config(layout="wide", page_title="Brewery Invoice Parser")

# ==========================================
# 0. AUTHENTICATION
# ==========================================
def check_password():
    if "APP_PASSWORD" not in st.secrets: return True
    if "password_correct" not in st.session_state: st.session_state.password_correct = False
    if st.session_state.password_correct: return True
    st.title("🔒 Login Required")
    pwd_input = st.text_input("Enter Password", type="password")
    if st.button("Log In"):
        if pwd_input == st.secrets["APP_PASSWORD"]:
            st.session_state.password_correct = True
            st.rerun()
        else: st.error("Incorrect Password")
    return False

if not check_password(): st.stop()

st.title("Brewery Invoice Parser ⚡")

# ==========================================
# 1A. CIN7 CORE ENGINE
# ==========================================

def get_cin7_headers():
    if "cin7" not in st.secrets: return None
    creds = st.secrets["cin7"]
    return {
        "api-auth-accountid": creds.get("account_id"),
        "api-auth-applicationkey": creds.get("api_key"),
        "Content-Type": "application/json"
    }

def get_cin7_base_url():
    if "cin7" not in st.secrets: return None
    return st.secrets["cin7"].get("base_url", "https://inventory.dearsystems.com/ExternalApi/v2")

@st.cache_data(ttl=3600) 
def fetch_all_cin7_suppliers_cached():
    if "cin7" not in st.secrets: return []
    creds = st.secrets["cin7"]
    headers = {
        'Content-Type': 'application/json',
        'api-auth-accountid': creds.get("account_id"),
        'api-auth-applicationkey': creds.get("api_key")
    }
    base_url = creds.get("base_url", "https://inventory.dearsystems.com/ExternalApi/v2")
    all_suppliers = []
    page = 1
    try:
        while True:
            url = f"{base_url}/supplier?Page={page}&Limit=100"
            req = Request(url, headers=headers)
            with urlopen(req) as response:
                if response.getcode() == 200:
                    data = json.loads(response.read())
                    key = "SupplierList" if "SupplierList" in data else "Suppliers"
                    if key in data and data[key]:
                        for s in data[key]:
                            all_suppliers.append({"Name": s["Name"], "ID": s["ID"]})
                        if len(data[key]) < 100: break
                        page += 1
                    else: break
                else: break
    except: pass
    return sorted(all_suppliers, key=lambda x: x['Name'].lower())

def get_cin7_product_id(sku):
    headers = get_cin7_headers()
    if not headers: return None
    url = f"{get_cin7_base_url()}/product"
    params = {"Sku": sku}
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            if "Products" in data and len(data["Products"]) > 0:
                return data["Products"][0]["ID"]
    except: pass
    return None

def get_cin7_supplier(name):
    headers = get_cin7_headers()
    if not headers: return None
    safe_name = quote(name)
    url = f"{get_cin7_base_url()}/supplier?Name={safe_name}"
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            if "Suppliers" in data and len(data["Suppliers"]) > 0:
                return data["Suppliers"][0]
    except: pass
    if "&" in name: return get_cin7_supplier(name.replace("&", "and"))
    return None

def create_cin7_purchase_order(header_df, lines_df, location_choice):
    headers = get_cin7_headers()
    if not headers: return False, "Cin7 Secrets missing.", []
    logs = []
    
    supplier_id = None
    if 'Cin7_Supplier_ID' in header_df.columns and header_df.iloc[0]['Cin7_Supplier_ID']:
        supplier_id = header_df.iloc[0]['Cin7_Supplier_ID']
    else:
        supplier_name = header_df.iloc[0]['Payable_To']
        supplier_data = get_cin7_supplier(supplier_name)
        if supplier_data: supplier_id = supplier_data['ID']

    if not supplier_id: return False, "Supplier not linked.", logs

    order_lines = []
    id_col = 'Cin7_London_ID' if location_choice == 'London' else 'Cin7_Glou_ID'
    
    for _, row in lines_df.iterrows():
        prod_id = row.get(id_col)
        if row.get('Shopify_Status') == "✅ Matched" and pd.notna(prod_id) and str(prod_id).strip():
            qty = float(row.get('Quantity', 0))
            price = float(row.get('Item_Price', 0))
            
            # ROUNDING FIX
            total = round(qty * price, 2)
            
            order_lines.append({
                "ProductID": prod_id, 
                "Quantity": qty, 
                "Price": price, 
                "Total": total,
                "TaxRule": "20% (VAT on Expenses)",
                "Discount": 0,
                "Tax": 0
            })

    if not order_lines: return False, "No valid lines found.", logs

    # Header
    url_create = f"{get_cin7_base_url()}/advanced-purchase"
    payload_header = {
        "SupplierID": supplier_id,
        "Location": location_choice,
        "Date": pd.to_datetime('today').strftime('%Y-%m-%d'),
        "TaxRule": "20% (VAT on Expenses)",
        "Approach": "Stock",
        "BlindReceipt": False,
        "PurchaseType": "Advanced",
        "Status": "ORDERING",
        "SupplierInvoiceNumber": str(header_df.iloc[0].get('Invoice_Number', ''))
    }
    
    task_id = None
    try:
        r1 = requests.post(url_create, headers=headers, json=payload_header)
        if r1.status_code == 200:
            task_id = r1.json().get('ID')
        else: return False, f"Header Error: {r1.text}", logs
    except Exception as e: return False, f"Header Ex: {e}", logs

    # Lines
    if task_id:
        url_lines = f"{get_cin7_base_url()}/purchase/order"
        payload_lines = {
            "TaskID": task_id,
            "CombineAdditionalCharges": False,
            "Memo": "Streamlit Import",
            "Status": "DRAFT", 
            "Lines": order_lines,
            "AdditionalCharges": []
        }
        try:
            r2 = requests.post(url_lines, headers=headers, json=payload_lines)
            if r2.status_code == 200:
                return True, f"✅ PO Created! ID: {task_id}", logs
            else: return False, f"Line Error: {r2.text}", logs
        except Exception as e: return False, f"Lines Ex: {e}", logs
            
    return False, "Unknown Error", logs

# ==========================================
# 1B. SHOPIFY ENGINE (STRICT FORMAT MATCH)
# ==========================================

def fetch_shopify_products_by_vendor(vendor):
    if "shopify" not in st.secrets: return []
    creds = st.secrets["shopify"]
    shop_url = creds.get("shop_url")
    token = creds.get("access_token")
    version = creds.get("api_version", "2024-04")
    endpoint = f"https://{shop_url}/admin/api/{version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    
    # ADDED: keg_type metafield
    query = """
    query ($query: String!, $cursor: String) {
      products(first: 50, query: $query, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        edges {
          node {
            id title status
            featuredImage { url } 
            format_meta: metafield(namespace: "custom", key: "Format") { value }
            keg_meta: metafield(namespace: "custom", key: "keg_type") { value }
            abv_meta: metafield(namespace: "custom", key: "ABV") { value }
            variants(first: 20) {
              edges { node { id title sku inventoryQuantity } }
            }
          }
        }
      }
    }
    """
    search_vendor = vendor.replace("'", "\\'") 
    variables = {"query": f"vendor:'{search_vendor}'"} 
    
    all_products = []
    cursor = None
    has_next = True
    
    while has_next:
        vars_curr = variables.copy()
        if cursor: vars_curr['cursor'] = cursor
        try:
            response = requests.post(endpoint, json={"query": query, "variables": vars_curr}, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if "data" in data and "products" in data["data"]:
                    p_data = data["data"]["products"]
                    all_products.extend(p_data["edges"])
                    has_next = p_data["pageInfo"]["hasNextPage"]
                    cursor = p_data["pageInfo"]["endCursor"]
                else: has_next = False
            else: has_next = False
        except: has_next = False
            
    return all_products

def normalize_vol_string(v_str):
    if not v_str: return "0"
    v_str = str(v_str).lower().strip()
    nums = re.findall(r'\d+\.?\d*', v_str)
    if not nums: return "0"
    val = float(nums[0])
    if "ml" in v_str: val = val / 10
    if val.is_integer(): return str(int(val))
    return str(val)

def run_reconciliation_check(lines_df):
    if lines_df.empty: return lines_df, ["No Lines to check."]
    logs = []
    df = lines_df.copy()
    
    df['Shopify_Status'] = "Pending"
    df['Matched_Product'] = ""
    df['Matched_Variant'] = "" 
    df['Image'] = ""
    df['London_SKU'] = ""     
    df['Cin7_London_ID'] = "" 
    df['Gloucester_SKU'] = "" 
    df['Cin7_Glou_ID'] = ""   
    
    suppliers = df['Supplier_Name'].unique()
    shopify_cache = {}
    
    progress_bar = st.progress(0)
    for i, supplier in enumerate(suppliers):
        progress_bar.progress((i)/len(suppliers))
        logs.append(f"🔎 **Fetching Shopify Data:** `{supplier}`")
        products = fetch_shopify_products_by_vendor(supplier)
        shopify_cache[supplier] = products
        logs.append(f"   -> Found {len(products)} products.")
    progress_bar.progress(1.0)

    results = []
    for _, row in df.iterrows():
        status = "❓ Vendor Not Found"
        london_sku, glou_sku, cin7_l_id, cin7_g_id, img_url = "", "", "", "", ""
        matched_prod_name, matched_var_name = "", ""
        
        supplier = row['Supplier_Name']
        inv_prod_name = row['Product_Name']
        inv_fmt = str(row.get('Format', '')).lower()
        
        raw_pack = str(row.get('Pack_Size', '')).strip()
        inv_pack = "1" if raw_pack.lower() in ['none', 'nan', '', '0'] else raw_pack.replace('.0', '')
        inv_vol = normalize_vol_string(row.get('Volume', ''))
        
        logs.append(f"Checking: **{inv_prod_name}** ({inv_fmt} / {inv_pack}x {inv_vol})")

        if supplier in shopify_cache and shopify_cache[supplier]:
            candidates = shopify_cache[supplier]
            scored_candidates = []
            
            for edge in candidates:
                prod = edge['node']
                shop_title_full = prod['title']
                
                # Logic: Parse L-Supplier / Product Name
                shop_prod_name_clean = shop_title_full
                if "/" in shop_title_full:
                    parts = [p.strip() for p in shop_title_full.split("/")]
                    if len(parts) >= 2: shop_prod_name_clean = parts[1]
                
                score = fuzz.token_sort_ratio(inv_prod_name, shop_prod_name_clean)
                if inv_prod_name.lower() in shop_prod_name_clean.lower(): score += 10
                if score > 40: scored_candidates.append((score, prod, shop_prod_name_clean))
            
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            match_found = False
            
            for score, prod, clean_name in scored_candidates:
                if score < 75: continue 
                
                # --- STRICT FORMAT CHECK ---
                shop_fmt_meta = prod.get('format_meta', {}).get('value', '') or ""
                shop_keg_meta = prod.get('keg_meta', {}).get('value', '') or ""
                shop_title_lower = prod['title'].lower()
                
                # Combine all format info from Shopify
                shop_format_str = f"{shop_fmt_meta} {shop_keg_meta} {shop_title_lower}".lower()
                
                is_compatible = True
                
                # Rule 1: Steel Keg vs KeyKeg/PolyKeg
                if "steel" in inv_fmt:
                    if "keykeg" in shop_format_str or "poly" in shop_format_str or "dolium" in shop_format_str:
                        is_compatible = False
                
                # Rule 2: KeyKeg vs Steel
                elif "keykeg" in inv_fmt:
                    if "steel" in shop_format_str or "stainless" in shop_format_str:
                        is_compatible = False
                        
                # Rule 3: Cask vs Keg
                elif "cask" in inv_fmt or "firkin" in inv_fmt:
                    if "keg" in shop_format_str and "cask" not in shop_format_str:
                        is_compatible = False

                if not is_compatible:
                    # logs.append(f"   Skipping `{prod['title']}` - Format Conflict.")
                    continue
                
                # --- VARIANT CHECK ---
                for v_edge in prod['variants']['edges']:
                    variant = v_edge['node']
                    v_title = variant['title'].lower()
                    v_sku = str(variant.get('sku',
