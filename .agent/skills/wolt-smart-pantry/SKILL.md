---
name: wolt-smart-pantry
description: >-
  Analyzes kitchen, fridge, freezer, and pantry inventory photos or lists, maintains persistent virtual
  pantry memory across weekly purchases, inspects live Wolt store catalogs and deals, calculates balanced
  weekly meal plans with cooking shrinkage math and freshness scheduling, and automates cart creation on Wolt.
---

# Wolt Smart Pantry & Grocery Assistant

This skill enables the agent to audit any user's kitchen inventory, maintain persistent virtual pantry memory across weekly purchases (so users don't need to re-upload photos every week), explore live store catalogs on Wolt to discover active promotions and in-stock items, dynamically compute required ingredients based on missing meal slots and thermal cooking shrinkage, and automatically assemble an exact grocery cart on Wolt.

---

## 🛠️ System Overview & Core Workflow

```mermaid
flowchart TD
    A1["📸 Photos Uploaded (Recalibrate)"] --> B["1. Dynamic Stock & Memory Audit\n(Check pantry_memory.json or scan new photos)"]
    A2["💬 Text Prompt: 'Plan Week 2'\n(No photos needed)"] --> B
    
    B --> C["🔍 2. Live Store Inspection & Deals\n(Explore Wolt catalog, active discounts & real packaging)"]
    C --> D["📐 3. Nutritional Sizing & Freshness Matrix\n(Apply thermal shrinkage math & schedule meals Days 1-7)"]
    D --> E["🛒 4. Automated Cart Assembly\n(Execute wolt_manager.py add with exact quantities)"]
    E --> F["💾 5. Automatic Pantry Memory Update\n(Save purchased staples & decay weekly perishables)"]
    F --> G["✅ 6. Safe 1-Click User Checkout\n(Browser left open with order review ready)"]
```

---

## 📋 Step-by-Step Instructions

### Step 1: Dynamic Inventory & Persistent Pantry Memory Audit
Before generating a meal plan, check if previous pantry state exists:

```bash
# Check tracked long-term staples, proteins, and purchase history:
python wolt_manager.py pantry --action status
```

* **Scenario A: User uploads new photos**:
  - Audit the photos for staples, proteins, and produce.
  - Recalibrate and update the virtual memory state.
* **Scenario B: User prompts without photos (e.g., *"Plan my meals for this week"* or *"Let's order groceries"*):**
  - Read active long-term staples from `pantry_memory.json` (e.g. 1kg onions, cooking oils, rice, pasta, seasonings).
  - Treat all multi-week staples as already covered (Zero Repurchase).
  - Only calculate and purchase missing fresh weekly proteins and Tier 1–2 perishables.

### Step 2: Live Store Catalog & Deals Exploration
Before finalizing product recommendations, inspect the target store venue in real time using `wolt_manager.py search` or `wolt_manager.py deals`:

```bash
# Discover live products, exact packaging weights, and current prices in the store:
python wolt_manager.py search --store wolt-market-maakri --queries "hakkliha" "kanafilee" "banaan" "paprika" "rukola"

# Or scan for active store discounts and promotional campaigns:
python wolt_manager.py deals --store wolt-market-maakri
```

The script returns structured JSON detailing live in-stock products, exact packaging weights (e.g. 300g vs 400g vs 500g), prices (€), and price per kg.

### Step 3: Nutritional Sizing, Deals Matching & Freshness Matrix
Using the live store data and formulas from `GROCERY_PLANNING_RULES.md`:
* **Calculate remaining meal slots**:
  $$\text{Missing Main Meals} = (\text{Days} \times \text{Main Meals per Day}) - \text{Meals Covered by In-Stock / Memory Goods}$$
* **Thermal Shrinkage Factor**: Raw meat and fish lose 20–35% mass during cooking:
  $$\text{Raw Weight Needed} = \frac{\text{Target Cooked Portion}}{1 - \text{Shrinkage Rate}}$$
  - **Poultry / Red Meat**: ~250g–300g raw per standard meal (~500g–600g for a 2-portion meal prep).
  - **Fish & Seafood**: ~220g–260g raw per standard meal.
  - **Vegetarian (Tofu / Eggs / Beans)**: 150g–200g tofu or 2–3 eggs per meal.
* **Select Best Live In-Stock Items**: Prioritize items on promotion or with the best price-to-weight ratio discovered in Step 2.
* **Freshness Hierarchy (7-Day Consumption Schedule)**:
  - **Days 1–3 (Tier 1: High Perishability)**: Fresh raw minced meat, delicate leafy greens (arugula, spinach), fresh fish.
  - **Days 4–5 (Tier 2: Moderate Shelf-Life)**: Coated/breaded poultry, dense fruit (bananas, refrigerated avocados), sandwich bread.
  - **Days 6–7 (Tier 3 & 4: Hardy Produce & Pantry Backup)**: Thick-skinned vegetables (bell peppers, cherry plum tomatoes, onions, carrots) and pantry/freezer backup meals.

### Step 4: Automated Wolt Cart Assembly & Memory Retention
Execute the cart creation automation with exact items and quantities:

```bash
# Add calculated items with explicit quantities:
python wolt_manager.py add --store wolt-market-maakri --items "Rakvere homemade minced meat, 400g:2" "Banaan:6" "Rukola:1" "Paprika punane:2"

# Or pass JSON format:
python wolt_manager.py add --store wolt-market-maakri --json-items "[{\"query\": \"Banaan\", \"qty\": 6}, {\"query\": \"Rakvere homemade minced meat, 400g\", \"qty\": 2}]"
```

*Note: `wolt_manager.py add` automatically saves all purchased items into `pantry_memory.json` upon cart completion, preserving your active stock for future weeks.*

---

## 🛡️ Automation Guarantees & Safety Heuristics

1. **Persistent Pantry Memory (`pantry_memory.json`)**: Eliminates the need to take repetitive kitchen photos every single week.
2. **Persistent Browser Session (`.wolt_profile`)**: Retains authentication and address settings without cloud IP blocking or captcha walls.
3. **Live Catalog Verification**: Inspects live store availability, eliminating guesswork or outdated hardcoded product names.
4. **Modal Stepper Automation**: Adjusts quantities inside product modals using the `+` stepper button.
5. **Cold-Cut Safety Filter**: Excludes processed sausages, bologna, and cold cuts when looking for real raw meats.
6. **Real-Time Price Verification**: Confirms that the cart total in euros updates after each item.
7. **Safe Checkout**: Leaves the browser open with the cart ready for the user to review. Never submits payment automatically.
