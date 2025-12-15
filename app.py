def fetch_shopify_products_by_vendor(vendor):
    """
    Queries Shopify GraphQL, fetching ALL products for a vendor using pagination.
    """
    if "shopify" not in st.secrets: return []
    creds = st.secrets["shopify"]
    shop_url = creds.get("shop_url")
    token = creds.get("access_token")
    version = creds.get("api_version", "2024-04")
    
    endpoint = f"https://{shop_url}/admin/api/{version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    
    all_products = []
    cursor = None
    
    while True:
        # Build the GraphQL Query (Including Pagination)
        query = """
        query ($query: String!, $cursor: String) {
          products(first: 50, query: $query, after: $cursor) {
            edges {
              node {
                id
                title
                status
                format_meta: metafield(namespace: "custom", key: "Format") { value }
                abv_meta: metafield(namespace: "custom", key: "ABV") { value }
                variants(first: 20) {
                  edges {
                    node {
                      id
                      title
                      sku
                      inventoryQuantity
                    }
                  }
                }
              }
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
        }
        """
        
        # Build the query (including cursor, if present)
        search_vendor = vendor.replace("'", "\\'")
        variables = {"query": f"vendor:'{search_vendor}' AND (status:ACTIVE OR status:DRAFT)"}
        if cursor:
            variables["cursor"] = cursor
        
        try:
            response = requests.post(endpoint, json={"query": query, "variables": variables}, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if "data" in data and "products" in data["data"]:
                    products = data["data"]["products"]
                    all_products.extend(products["edges"])
                    
                    # Check for pagination
                    if products["pageInfo"]["hasNextPage"]:
                        cursor = products["pageInfo"]["endCursor"]
                        time.sleep(0.25) # Be nice to the API
                    else:
                        break # No more pages
                else:
                    # Error handling
                    if "errors" in data:
                        st.error(f"GraphQL Error: {data['errors'][0]['message']}")
                    break # Stop if no products returned
            else:
                st.error(f"HTTP Error {response.status_code}: {response.text}")
                break
                
        except Exception as e:
            st.error(f"Shopify Connection Error: {e}")
            break # Stop if any error occurs
    
    return all_products
