def run_shopify_check(lines_df):
    if lines_df.empty: return lines_df, ["No Lines to check."]
    
    logs = []
    df = lines_df.copy()
    df['Shopify_Status'] = "Pending"
    df['Shopify_Variant_ID'] = ""
    
    suppliers = df['Supplier_Name'].unique()
    shopify_cache = {}
    
    progress_bar = st.progress(0)
    for i, supplier in enumerate(suppliers):
        progress_bar.progress((i)/len(suppliers))
        logs.append(f"🔎 **Searching Shopify (Active/Draft) for:** `{supplier}`")
        products = fetch_shopify_products_by_vendor(supplier)
        shopify_cache[supplier] = products
        logs.append(f"   -> Found {len(products)} products.")
        
    progress_bar.progress(1.0)
    
    results = []
    for _, row in df.iterrows():
        status = "❓ Vendor Not Found"
        found_id = ""
        
        supplier = row['Supplier_Name']
        inv_prod_name = row['Product_Name']
        inv_pack = str(row.get('Pack_Size', '1')).replace('.0', '')
        if inv_pack in ["", "nan", "0"]: inv_pack = "1"
        inv_vol = normalize_vol_string(row.get('Volume', ''))
        
        logs.append(f"--- Checking Item: **{inv_prod_name}** (Pack:{inv_pack} Vol:{inv_vol}) ---")

        if supplier in shopify_cache and shopify_cache[supplier]:
            candidates = shopify_cache[supplier]
            
            # 1. Score ALL Candidates
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
                
                if score > 40:
                    scored_candidates.append((score, prod))
            
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            
            # --- FIX: Initialize match_found BEFORE the loop ---
            match_found = False
            
            for score, prod in scored_candidates:
                logs.append(f"   Checking Candidate: `{prod['title']}` ({score}%)")
                
                for v_edge in prod['variants']['edges']:
                    variant = v_edge['node']
                    v_title = variant['title'].lower()
                    
                    pack_ok = False
                    if inv_pack == "1":
                        if " x " not in v_title: pack_ok = True
                    else:
                        if f"{inv_pack} x" in v_title or f"{inv_pack}x" in v_title: pack_ok = True
                    
                    vol_ok = False
                    if inv_vol in v_title: vol_ok = True
                    if len(inv_vol) == 2 and f"{inv_vol}0" in v_title: vol_ok = True 
                    
                    if pack_ok and vol_ok:
                        logs.append(f"      ✅ **MATCHED VARIANT**: `{variant['title']}`")
                        found_id = variant['id']
                        status = "✅ Matched"
                        match_found = True
                        break
                    else:
                        logs.append(f"      ❌ Variant `{variant['title']}` failed size check")
                
                if match_found: break
            
            if not match_found:
                if scored_candidates:
                    status = "❌ Size Missing"
                else:
                    status = "🆕 New Product"
                    logs.append(f"  - No match for `{inv_prod_name}`. Best was {scored_candidates[0][0] if scored_candidates else 0}%")
        
        row['Shopify_Status'] = status
        row['Shopify_Variant_ID'] = found_id
        results.append(row)
    
    return pd.DataFrame(results), logs
