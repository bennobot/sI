# ==========================================
# 5. DISPLAY & WORKFLOW LOGIC
# ==========================================

if st.session_state.header_data is not None:
    if custom_rule:
        st.success("✅ Used Custom Rules")
        try: sup = st.session_state.header_data.iloc[0]['Payable_To']
        except: sup = "Unknown"
        with st.expander("📩 Developer Snippet"):
            st.code(f'"{sup}": """\n{custom_rule}\n""",', language="python")

    st.divider()
    
    # 1. CALCULATE STATUS
    df = st.session_state.line_items
    if 'Shopify_Status' in df.columns:
        unmatched_count = len(df[df['Shopify_Status'] != "✅ Matched"])
    else:
        unmatched_count = len(df) # Assume dirty start

    all_matched = (unmatched_count == 0) and ('Shopify_Status' in df.columns)

    # 2. STATIC TABS (ALWAYS 3)
    # This prevents NameError by ensuring t1, t2, t3 always exist
    t1, t2, t3 = st.tabs(["📝 1. Line Items", "⚠️ 2. Resolve Missing", "🚀 3. Finalize PO"])
    
    # --- TAB 1: LINE ITEMS ---
    with t1:
        st.subheader("1. Review & Edit Lines")
        
        display_df = st.session_state.line_items.copy()
        if 'Shopify_Status' in display_df.columns:
            display_df.rename(columns={'Shopify_Status': 'Product_Status'}, inplace=True)

        ideal_order = [
            'Product_Status', 'Matched_Product', 'Matched_Variant', 'Image', 
            'Supplier_Name', 'Product_Name', 'ABV', 'Format', 'Pack_Size', 
            'Volume', 'Quantity', 'Item_Price', 'Collaborator', 
            'Shopify_Variant_ID', 'London_SKU', 'Gloucester_SKU'
        ]
        final_cols = [c for c in ideal_order if c in display_df.columns]
        rem = [c for c in display_df.columns if c not in final_cols]
        final_cols.extend(rem)
        display_df = display_df[final_cols]
        
        column_config = {
            "Image": st.column_config.ImageColumn("Img"),
            "Product_Status": st.column_config.TextColumn("Status", disabled=True),
            "Matched_Product": st.column_config.TextColumn("Shopify Match", disabled=True),
            "Matched_Variant": st.column_config.TextColumn("Variant Match", disabled=True),
        }

        edited_lines = st.data_editor(
            display_df, 
            num_rows="dynamic", 
            width=1000,
            key="line_editor",
            column_config=column_config
        )
        
        if edited_lines is not None:
            saved_df = edited_lines.copy()
            if 'Product_Status' in saved_df.columns:
                saved_df.rename(columns={'Product_Status': 'Shopify_Status'}, inplace=True)
            st.session_state.line_items = saved_df

        col1, col2 = st.columns([1, 4])
        with col1:
            if "shopify" in st.secrets:
                if st.button("🛒 Check Inventory", type="primary"):
                    with st.spinner("Checking..."):
                        updated_lines, logs = run_reconciliation_check(st.session_state.line_items)
                        st.session_state.line_items = updated_lines
                        st.session_state.shopify_logs = logs
                        st.session_state.matrix_data = create_product_matrix(updated_lines)
                        st.success("Check Complete!")
                        st.rerun()
        
        with col2:
             st.download_button("📥 Download Lines CSV", st.session_state.line_items.to_csv(index=False), "lines.csv")
        
        if st.session_state.shopify_logs:
            with st.expander("🕵️ Debug Logs", expanded=False):
                st.markdown("\n".join(st.session_state.shopify_logs))

    # --- TAB 2: MISSING PRODUCTS ---
    with t2:
        st.subheader("2. Products to Create in Shopify")
        
        if all_matched:
            st.success("🎉 All products matched! No action needed here.")
        else:
            col_u1, col_u2 = st.columns([3, 1])
            with col_u1:
                st.warning(f"⚠️ {unmatched_count} unmatched items found. Please create them in Shopify.")
            
            with col_u2:
                if st.button("🍺 Search Untappd Details"):
                    if "untappd" in st.secrets:
                        with st.spinner("Searching Untappd..."):
                             st.session_state.matrix_data = batch_untappd_lookup(st.session_state.matrix_data)
                             st.success("Search Complete!")
                             st.rerun()
                    else:
                        st.error("Untappd Secrets Missing")

            if st.session_state.matrix_data is not None and not st.session_state.matrix_data.empty:
                column_config = {}
                for i in range(1, 4):
                    column_config[f"Create{i}"] = st.column_config.CheckboxColumn(f"Create?", default=False)

                edited_matrix = st.data_editor(
                    st.session_state.matrix_data, 
                    num_rows="dynamic", 
                    width=1000,
                    column_config=column_config
                )
                st.download_button("📥 Download To-Do List", edited_matrix.to_csv(index=False), "missing_products.csv")

    # --- TAB 3: HEADER / EXPORT ---
    with t3:
        st.subheader("3. Finalize & Export")
        
        if not all_matched:
            st.warning("⚠️ You have unmatched products. You can still export, but check Tab 2 first.")
        else:
            st.success("✅ Ready for Export")
            
        current_payee = "Unknown"
        if not st.session_state.header_data.empty:
             current_payee = st.session_state.header_data.iloc[0]['Payable_To']
        
        cin7_list_names = [s['Name'] for s in st.session_state.cin7_all_suppliers]
        default_index = 0
        if cin7_list_names and current_payee:
            match, score = process.extractOne(current_payee, cin7_list_names)
            if score > 60:
                try: default_index = cin7_list_names.index(match)
                except ValueError: default_index = 0

        col_h1, col_h2 = st.columns([1, 2])
        with col_h1:
            selected_supplier = st.selectbox(
                "Cin7 Supplier Link:", 
                options=cin7_list_names,
                index=default_index,
                key="header_supplier_select",
                help="Click 'Fetch Cin7 Suppliers' in sidebar if empty."
            )
            
            if selected_supplier and not st.session_state.header_data.empty:
                supp_data = next((s for s in st.session_state.cin7_all_suppliers if s['Name'] == selected_supplier), None)
                if supp_data:
                    st.session_state.header_data.at[0, 'Cin7_Supplier_ID'] = supp_data['ID']
                    st.session_state.header_data.at[0, 'Cin7_Supplier_Name'] = supp_data['Name']
        
        with col_h2:
            st.write("") 
            if not st.session_state.header_data.empty:
                st.caption(f"ID: {st.session_state.header_data.iloc[0].get('Cin7_Supplier_ID', 'N/A')}")

        edited_header = st.data_editor(st.session_state.header_data, num_rows="fixed", width=1000)
        st.download_button("📥 Download Header CSV", edited_header.to_csv(index=False), "header.csv")
        
        st.divider()
        
        po_location = st.selectbox("Select Delivery Location:", ["London", "Gloucester"], key="final_po_loc")
        
        if st.button(f"📤 Export PO to Cin7 ({po_location})", type="primary"):
            if "cin7" in st.secrets:
                with st.spinner("Creating Purchase Order..."):
                    success, msg, logs = create_cin7_purchase_order(
                        st.session_state.header_data, 
                        st.session_state.line_items, 
                        po_location
                    )
                    st.session_state.cin7_logs = logs
                    
                    if success:
                        task_id = None
                        match = re.search(r'ID: ([a-f0-9\-]+)', msg)
                        if match: task_id = match.group(1)
                        
                        st.success(msg)
                        if task_id:
                            st.link_button("🔗 Open PO in Cin7", f"https://inventory.dearsystems.com/PurchaseAdvanced#{task_id}")
                        st.balloons()
                    else:
                        st.error(msg)
                        with st.expander("Error Details"):
                            for log in logs: st.write(log)
            else:
                st.error("Cin7 Secrets missing.")
