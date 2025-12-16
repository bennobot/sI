# ==========================================
# 5. DISPLAY
# ==========================================

if st.session_state.header_data is not None:
    if custom_rule:
        st.success("✅ Used Custom Rules")
        try: sup = st.session_state.header_data.iloc[0]['Payable_To']
        except: sup = "Unknown"
        with st.expander("📩 Developer Snippet"):
            st.code(f'"{sup}": """\n{custom_rule}\n""",', language="python")

    st.divider()
    t1, t2, t3, t4 = st.tabs(["📊 **Product Matrix (Edit Here)**", "📄 Header", "📝 Line Items", "🔍 Checker"])
    
    with t1:
        st.info("💡 Edit product details here. Click 'Sync' to update the other files.")
        edited_matrix = st.data_editor(st.session_state.matrix_data, num_rows="dynamic", width=1000)
        colA, colB = st.columns([1, 4])
        with colA:
            if st.button("🔄 Sync & Regenerate"):
                st.session_state.matrix_data = edited_matrix
                st.session_state.line_items = reconstruct_lines_from_matrix(edited_matrix)
                st.session_state.checker_data = create_product_checker(st.session_state.line_items)
                st.success("Synced!")
                st.rerun()
        with colB:
            st.download_button("📥 Download CSV", edited_matrix.to_csv(index=False), "matrix.csv")

    with t2:
        edited_header = st.data_editor(st.session_state.header_data, num_rows="fixed", width=1000)
        st.download_button("📥 Download CSV", edited_header.to_csv(index=False), "header.csv")

    with t3:
        st.subheader("Line Items")
        # INVENTORY BUTTONS
        col_act1, col_act2, _ = st.columns([1, 1, 2])
        
        with col_act1:
            if "shopify" in st.secrets:
                if st.button("🛒 Check Inventory"):
                    with st.spinner("Reconciling..."):
                        updated_lines, logs = run_reconciliation_check(st.session_state.line_items)
                        st.session_state.line_items = updated_lines
                        st.session_state.shopify_logs = logs
                        st.success("Complete!")
                        st.rerun()
                        
        with col_act2:
            if "cin7" in st.secrets:
                loc = st.selectbox("PO Location:", ["London", "Gloucester"], key="po_loc")
                if st.button("📤 Export PO"):
                    with st.spinner("Creating..."):
                        ok, msg = create_cin7_purchase_order(st.session_state.header_data, st.session_state.line_items, loc)
                        if ok: st.success(msg)
                        else: st.error(msg)
        
        # --- PERSISTENT DEBUGGERS ---
        
        # Shopify Logs
        if st.session_state.shopify_logs:
            with st.expander("🕵️ Shopify Debug Logs", expanded=False):
                st.markdown("\n".join(st.session_state.shopify_logs))
        
        # Cin7 Logs (Show if populated)
        if 'cin7_supplier_list' in st.session_state and st.session_state.cin7_supplier_list:
            with st.expander("🐞 Cin7 Supplier Debugger (Data Found)", expanded=True):
                st.warning("Supplier not found via Exact Match. Here is what Cin7 returned:")
                st.write(st.session_state.cin7_supplier_list)
                    
        edited_lines = st.data_editor(st.session_state.line_items, num_rows="dynamic", width=1000)
        st.download_button("📥 Download CSV", edited_lines.to_csv(index=False), "lines.csv")
        
    with t4:
        if st.session_state.checker_data is not None:
            st.dataframe(st.session_state.checker_data, width=1000)
            st.download_button("📥 Download CSV", st.session_state.checker_data.to_csv(index=False), "checker.csv")
