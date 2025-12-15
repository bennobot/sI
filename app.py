def run_shopify_check(lines_df):
    if lines_df.empty: return lines_df
    
    # Create logs container
    log_container = st.expander("🕵️ Shopify Debug Logs", expanded=True)
    logs = []
    
    lines_df['Shopify_Status'] = "Pending"
    lines_df['Shopify_Variant_ID'] = ""
    
    suppliers = lines_df['Supplier_Name'].unique()
    shopify_cache = {}
    
    progress_bar = st.progress(0)
    for i, supplier in enumerate(suppliers):
        progress_bar.progress((i)/len(suppliers))
        # Log the search
        logs.append(f"**Searching Shopify for Vendor:** `{supplier}`")
        products = fetch_shopify_products_by_vendor(supplier)
        shopify_cache[supplier] = products
        logs.append(f" -> Found {len(products)} products.")
        
    progress_bar.progress(1.0)
    
    results = []
    for _, row in lines_df.iterrows():
        status = "❓ Vendor Not Found"
        found_id = ""
        debug_note = ""
        
        supplier = row['Supplier_Name']
        inv_prod_name = row['Product_Name']
        inv_fmt = str(row.get('Format', '')).lower()
        inv_pack = str(row.get('Pack_Size', '1')).replace('.0', '')
        if inv_pack in ["", "nan", "0"]: inv_pack = "1"
        inv_vol = normalize_vol_string(row.get('Volume', ''))

        if supplier in shopify_cache and shopify_cache[supplier]:
            candidates = shopify_cache[supplier]
            best_score = 0
            best_prod = None
            
            # Fuzzy Match
            for edge in candidates:
                prod = edge['node']
                score = fuzz.token_sort_ratio(inv_prod_name, prod['title'])
                if score > best_score:
                    best_score = score
                    best_prod = prod
            
            if best_prod and best_score > 75:
                logs.append(f"MATCHED PRODUCT: Invoice=`{inv_prod_name}` vs Shopify=`{best_prod['title']}` ({best_score}%)")
                
                # Check Metafield
                shop_fmt = best_prod.get('format_meta', {})
                shop_fmt_val = shop_fmt.get('value', '').lower() if shop_fmt else ""
                
                # Check Variants
                variant_found = False
                for v_edge in best_prod['variants']['edges']:
                    variant = v_edge['node']
                    v_title = variant['title'].lower()
                    
                    # Debug logic for size
                    pack_ok = False
                    if inv_pack == "1":
                        if " x " not in v_title: pack_ok = True
                    else:
                        if f"{inv_pack} x" in v_title or f"{inv_pack}x" in v_title: pack_ok = True
                    
                    vol_ok = False
                    if inv_vol in v_title: vol_ok = True
                    if len(inv_vol) == 2 and f"{inv_vol}0" in v_title: vol_ok = True 
                    
                    if pack_ok and vol_ok:
                        variant_found = True
                        found_id = variant['id']
                        break
                    else:
                        # Log why variant failed (only if product matched)
                        debug_note = f"Variant `{v_title}` failed. Needed Pack `{inv_pack}` & Vol `{inv_vol}`."

                if variant_found:
                    status = "✅ Matched"
                else:
                    status = "❌ Size Missing"
                    logs.append(f" -> {debug_note}")
            else:
                status = "🆕 New Product"
                logs.append(f" -> No product matched `{inv_prod_name}`. Best score was {best_score}")
        
        row['Shopify_Status'] = status
        row['Shopify_Variant_ID'] = found_id
        results.append(row)
    
    # Print logs
    log_container.markdown("\n".join(logs))
    return pd.DataFrame(results)
