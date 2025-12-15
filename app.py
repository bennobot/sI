def fetch_shopify_products_by_vendor(vendor):
    if "shopify" not in st.secrets: return []
    creds = st.secrets["shopify"]
    shop_url = creds.get("shop_url")
    token = creds.get("access_token")
    version = creds.get("api_version", "2024-04")
    
    endpoint = f"https://{shop_url}/admin/api/{version}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    
    # Query with Pagination support (cursor)
    query = """
    query ($query: String!, $cursor: String) {
      products(first: 50, query: $query, after: $cursor) {
        pageInfo {
          hasNextPage
        }
        edges {
          cursor
          node {
            id
            title
            status
            variants(first: 20) {
              edges {
                node {
                  id
                  title
                  sku
                }
              }
            }
          }
        }
      }
    }
    """
    
    search_vendor = vendor.replace("'", "\\'") 
    search_query = f"vendor:'{search_vendor}' AND (status:ACTIVE OR status:DRAFT)"
    
    all_products = []
    has_next_page = True
    cursor = None
    
    try:
        while has_next_page:
            variables = {"query": search_query, "cursor": cursor}
            response = requests.post(endpoint, json={"query": query, "variables": variables}, headers=headers)
            
            if response.status_code == 200:
                data = response.json()
                if "data" in data and "products" in data["data"]:
                    products_data = data["data"]["products"]
                    all_products.extend(products_data["edges"])
                    
                    has_next_page = products_data["pageInfo"]["hasNextPage"]
                    if has_next_page:
                        cursor = products_data["edges"][-1]["cursor"]
                else:
                    break
            else:
                break
    except Exception as e:
        st.sidebar.error(f"Shopify Pagination Error: {e}")
        
    return all_products
