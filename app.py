import streamlit as st
from streamlit_gsheets import GSheetsConnection  # New Library
import pandas as pd
# ... (rest of imports)

# --- REPLACEMENT FUNCTION FOR LOADING RULES ---
def load_rules_from_sheet():
    # Establish connection
    conn = st.connection("gsheets", type=GSheetsConnection)
    
    # Read the data
    df = conn.read(worksheet="Rules", ttl=0) # ttl=0 means no caching (always fresh)
    
    # Convert to Dictionary {Supplier: Rule}
    rules_dict = pd.Series(df.Rules.values, index=df.Supplier).to_dict()
    
    # Ensure Generic exists
    if "Generic / Unknown" not in rules_dict:
        rules_dict["Generic / Unknown"] = "Use standard global logic."
        
    return rules_dict, df

def save_rule_to_sheet(supplier, new_rule_text):
    conn = st.connection("gsheets", type=GSheetsConnection)
    df = conn.read(worksheet="Rules", ttl=0)
    
    # Check if supplier exists
    if supplier in df['Supplier'].values:
        # Update existing
        df.loc[df['Supplier'] == supplier, 'Rules'] = new_rule_text
    else:
        # Add new row
        new_row = pd.DataFrame([{"Supplier": supplier, "Rules": new_rule_text}])
        df = pd.concat([df, new_row], ignore_index=True)
        
    # Write back to Sheets
    conn.update(worksheet="Rules", data=df)
    st.cache_data.clear() # Clear cache to force reload

# --- UPDATED APP LOGIC ---

# 1. Load Rules (This replaces the JSON load)
if 'rules' not in st.session_state:
    try:
        rules_dict, _ = load_rules_from_sheet()
        st.session_state.rules = rules_dict
    except Exception:
        # Fallback if connection fails
        st.session_state.rules = DEFAULT_SUPPLIER_RULES

# ... (Sidebar Logic) ...

    # Edit Rules Button
    if st.button("💾 Save Rules (To Cloud)"):
        try:
            with st.spinner("Saving to Google Sheets..."):
                save_rule_to_sheet(selected_supplier, updated_text)
                # Update local state immediately
                st.session_state.rules[selected_supplier] = updated_text
                st.success("Saved to the Cloud! All users will see this now.")
        except Exception as e:
            st.error(f"Save failed: {e}")
