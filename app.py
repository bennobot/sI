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
    t1, t2, t3 = st.tabs(["📝 Line Items (Work Area)", "📊 Missing Products Report", "📄 Invoice Header"])
    
    # --- TAB 1: LINE ITEMS ---
    with t1:
        st.subheader("1. Review & Edit Lines")
        
        # EDIT FIRST
        edited_lines = st.data_editor(
            st.session_state.line_items, 
            num_rows="dynamic", 
            width=1000,
            key="line_editor"
        )
        st.session_state.line_items = edited_lines # Sync state

        # ACTIONS
        col1, col2 = st.columns([1, 4])
        with col1:
            if "shopify" in st.secrets:
                if st.button("🛒 Check Inventory & Generate Report"):
                    with st.spinner("Checking..."):
                        updated_lines, logs = run_reconciliation_check(st.session_state.line_items)
                        st.session_state.line_items = updated_lines
                        st.session_state.shopify_logs = logs
                        
                        # Generate Matrix (with Checkboxes)
                        st.session_state.matrix_data = create_product_matrix(updated_lines)
                        
                        st.success("Check Complete!")
                        st.rerun()
        
        with col2:
             st.download_button("📥 Download Lines CSV", edited_lines.to_csv(index=False), "lines.csv")
        
        if st.session_state.shopify_logs:
            with st.expander("🕵️ Debug Logs", expanded=False):
                st.markdown("\n".join(st.session_state.shopify_logs))

    # --- TAB 2: MISSING PRODUCTS REPORT ---
    with t2:
        st.subheader("2. Products to Create in Shopify")
        st.info("Check the boxes as you create these products.")
        
        if st.session_state.matrix_data is not None and not st.session_state.matrix_data.empty:
            # We configure the 'Create' columns to use checkbox column types explicitly
            column_config = {}
            for i in range(1, 4):
                column_config[f"Create{i}"] = st.column_config.CheckboxColumn(
                    f"Create {i}?",
                    help="Check this box when you have created the product in Shopify",
                    default=False,
                )

            edited_matrix = st.data_editor(
                st.session_state.matrix_data, 
                num_rows="dynamic", 
                width=1000,
                column_config=column_config
            )
            st.download_button("📥 Download To-Do List CSV", edited_matrix.to_csv(index=False), "missing_products.csv")
        
        elif st.session_state.matrix_data is not None:
            st.success("🎉 All products matched! Nothing to create.")
        else:
            st.warning("Run 'Check Inventory' in Tab 1 to generate this report.")

    # --- TAB 3: HEADER ---
    with t3:
        edited_header = st.data_editor(st.session_state.header_data, num_rows="fixed", width=1000)
        st.download_button("📥 Download Header CSV", edited_header.to_csv(index=False), "header.csv")
