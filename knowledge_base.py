# ==========================================
# 1. MASTER DATA (The Valid List)
# ==========================================
VALID_FORMATS = """
Cask | 9 Gallon
Cask | 4.5 Gallon
Cask | 5 Litre
KeyKeg | 10 Litre
KeyKeg | 20 Litre
KeyKeg | 30 Litre
# ... (Paste your full list here) ...
Cans | 44cl
Cans | 50cl
"""

# ==========================================
# 2. GLOBAL RULES (Applies to everyone)
# ==========================================
GLOBAL_RULES_TEXT = f"""
1. **PRODUCT NAMES & CLEANING**:
   - **Remove Prefixes**: Strip codes like "SRM-", "NRB", "30EK", "9G" from the start.
   - **Remove Sizes**: Remove "12x440ml" or "20L" from the name.
   - **Collaborator**: Extract partner names (e.g. "STF/Croft" -> Collab: Croft).
   - **Title Case**: Convert Product Name to Title Case.

2. **FORMAT MAPPING (The Dictionary)**:
   - "LSS" -> Steel Keg.
   - "Kegstar" (41L) -> Cask 9 Gallon.
   - "Kegstar" (Other) -> Steel Keg.
   - "Firkin" -> Cask 9 Gallon.
   - "Pin" -> Cask 4.5 Gallon.
   - "Poly" -> PolyKeg.

3. **FINANCIALS**: 
   - **Item_Price**: Price per PURCHASE UNIT (Case/Keg). DO NOT divide by pack size.
   - **Landed Cost**: (Total Delivery Charge / Total Units) + Item Price.

4. **FILTERING**:
   - Exclude "pump clip", "badge", "foamex" ONLY IF price is 0.00.

VALID FORMATS LIST:
{VALID_FORMATS}
"""

# ==========================================
# 3. SUPPLIER SPECIFIC RULEBOOK
# ==========================================
SUPPLIER_RULEBOOK = {
    "DEYA Brewing Company": """
    - FORMAT: "LSS" means "Litre Stainless Steel" -> Map to Steel Keg.
    - CANS: 500mL cans should be mapped to Volume: 50cl.
    """,

    "Simple Things Fermentations": """
    - COLLAB: "STF/Croft 3" -> Collaborator is "Croft 3".
    - PREFIX: Remove "30EK", "9G" from name.
    """,
    
    "James Clay and Sons": """
    - STRATEGY: Split Description into Supplier/Product.
    - PATTERN: "NxVol" (e.g. 20x50cl) indicates Pack/Volume.
    """,
    
    "Polly's Brew Co.": "PRODUCT NAME: Stop at first hyphen. Watch for 18-packs.",
    
    "North Riding Brewery": "DISCOUNT: Handle '(discount)' line item (negative total). Divide by units.",
}
