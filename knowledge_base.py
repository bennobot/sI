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
KeyKeg | 12 Litre
KeyKeg | 50 Litre
Steel Keg | 20 Litre
Steel Keg | 30 Litre
Steel Keg | 50 Litre
Steel Keg | 12 Litre
Bag in Box | 10 Litre
Bag in Box | 20 Litre
Bag in Box | 5 Litre
Bottles | 33cl
Bottles | 50cl
Bottles | 75cl
Bottles | 66cl
Bottles | 35cl
Bottles | 56.8cl
Bottles | 70cl
Bottles | 20cl
Bottles | 25cl
Bottles | 24cl
Bottles | 27.5cl
Bottles | 35.5cl
Bottles | 37.5cl
Bottles | 10cl
Bottles | 150cl
Bottles | 34cl
Bottles | 30cl
Cans | 33cl
Cans | 44cl
Cans | 25cl
Cans | 56.8cl
Cans | 50cl
Cans | 35cl
Cans | 47.3cl
Cans | 18.7cl
Cans | 10cl
Cans | 40.3cl
Cans | 35.5cl
Cans | 12.5cl
Cans | 47cl
Cans | 14cl
PolyKeg | 10 Litre
PolyKeg | 20 Litre
PolyKeg | 30 Litre
PolyKeg | 12 Litre
PolyKeg | 50 Litre
UniKeg | 10 Litre
UniKeg | 20 Litre
UniKeg | 30 Litre
UniKeg | 12 Litre
UniKeg | 50 Litre
Dolium Keg | 10 Litre
Dolium Keg | 20 Litre
Dolium Keg | 30 Litre
Dolium Keg | 12 Litre
Dolium Keg | 50 Litre
EcoKeg | 10 Litre
EcoKeg | 20 Litre
EcoKeg | 30 Litre
EcoKeg | 12 Litre
EcoKeg | 50 Litre
US Dolium Keg | 20 Litre
Cellar Equipment | 250 Pack
"""

# ==========================================
# 2. GLOBAL RULES (Applies to everyone)
# ==========================================
GLOBAL_RULES_TEXT = f"""
1. **PRODUCT NAMES (STRICT CLEANING)**:
   - **NO SIZE/FORMAT**: The Product Name must NOT contain info like "20L", "Keg", "Cans", "24x33cl".
   - **NO DELIMITERS**: Remove characters like "|", "-", ":" from the name.
   - **Remove Prefixes**: Strip codes like "SRM-", "NRB", "30EK", "9G".
   - **Collaborator**: Extract partner names (e.g. "STF/Croft" -> Collab: Croft).
   - **Title Case**: Convert Product Name to Title Case.

2. **FORMAT MAPPING (The Dictionary)**:
   - "LSS" -> Steel Keg.
   - "Kegstar" (41L) -> Cask 9 Gallon.
   - "Kegstar" (Other) -> Steel Keg.
   - "E-Kegr" / "eKeg" / "Keg" -> Steel Keg.
   - "Firkin" -> Cask 9 Gallon.
   - "Pin" -> Cask 4.5 Gallon.
   - "Poly" -> PolyKeg.
   - **Conversions**: ml->cl, L->Litre.

3. **VALID LIST HANDLING (IMPORTANT)**:
   - The "VALID FORMATS LIST" below uses the syntax: `Format | Volume`.
   - **YOU MUST SPLIT THIS**. 
     - **Format Column**: Use the text *before* the pipe (e.g. "Steel Keg").
     - **Volume Column**: Use the text *after* the pipe (e.g. "20 Litre").
   - **DO NOT** put the pipe (`|`) or the volume into the Format column.

4. **PACK SIZE vs QUANTITY**:
   - **Pack_Size**: 
     - For **Bottles/Cans**: The count inside the case (e.g. 12, 24).
     - For **Kegs/Casks**: Must be **NULL** (Empty). Do not put '1'.
   - **Quantity**: The number of units ordered.

5. **FINANCIALS**: 
   - **Item_Price**: Price per PURCHASE UNIT (Case/Keg). DO NOT divide by pack size.
   - **Landed Cost**: IF delivery charge exists: (Total Delivery Charge / Total Units) + Item Price.
   - **Discount**: Apply line item discounts.

6. **FILTERING**:
   - Exclude "pump clip", "badge", "foamex" ONLY IF price is 0.00.
   - Exclude line items with 0.00 price unless it is free stock/samples.

7. **HEADER EXTRACTION**:
   - **Payable_To**: The Supplier Name. 
   - **NEVER** select "Pig's Ears" or "Pig's Ears Beer" as the Payable_To.

VALID FORMATS LIST:
{VALID_FORMATS}
"""

# ==========================================
# 3. SUPPLIER SPECIFIC RULEBOOK
# ==========================================
SUPPLIER_RULEBOOK = {
   "Trenchmore LLP": """
   - Supplier Name: "Silly Moo Cider".
   - Product Name: Remove "Silly Moo" from description.
   """,
   
   "Pilton Cider Ltd": """
   - BOTTLES: If size is 33cl -> Pack_Size: 12.
   - BOTTLES: If size is 75cl -> Pack_Size: 1.
   """,
   
    "DEYA Brewing Company": """
    - FORMAT: "LSS" -> Steel Keg.
    - CANS: 500mL -> Volume: 50cl.
    """,

    "Simple Things Fermentations": """
    - COLLAB: "STF/Croft 3" -> Collaborator is "Croft 3".
    - PREFIX: Remove "30EK", "9G".
    - DISCOUNT: Apply 15% discount.
    """,
    
    "James Clay and Sons": """
    - STRATEGY: Split Description into Supplier/Pr
