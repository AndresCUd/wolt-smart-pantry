---
name: wolt-smart-pantry
description: >-
  Analyzes kitchen/pantry/fridge inventory images, calculates weekly meal plans and grocery lists
  adhering to nutritional protein sizing and freshness shelf life rules, and automates cart creation
  in Wolt (e.g. Wolt Market Maakri / Selver Tallinn). Use whenever the user shares fridge/pantry
  photos, asks to plan weekly meals, or wants to order groceries on Wolt.
---

# Wolt Smart Pantry & Grocery Assistant

This skill guides the agent in transforming kitchen inventory photos (fridge, freezer, pantry) into an optimized 7-day meal plan and executing automated cart creation on Wolt (Tallinn, Estonia).

---

## 🛠️ System Overview & Architecture

The workflow consists of three automated stages:

1. **Visual Inventory & Stock Audit**: Inspect user-provided kitchen photos to identify in-stock proteins, carbohydrates, pantry staples, and frozen goods.
2. **Nutritional & Shelf-Life Calculation**: Apply the rules in `GROCERY_PLANNING_RULES.md` to compute raw protein requirements (accounting for cooking shrinkage) and schedule consumption based on the freshness hierarchy.
3. **Automated Wolt Cart Assembly**: Use the dedicated local Playwright script (`wolt_manager.py`) with persistent browser profiles to search and add the exact items and quantities without cloud IP blocking.

---

## 📋 Step-by-Step Workflow

### Step 1: Inventory & Stock Audit
When the user uploads kitchen photos:
* **Identify in-stock staples**: Flour, dry pasta, rice, oats, oil, spices, sauces, yeast, garlic. *(Rule: Do not repurchase staples that already exist).*
* **Identify in-stock proteins**: Canned tuna, eggs, Greek yogurt, frozen seafood/meat.
* **Identify missing meal slots**: A 7-day week requires 14 main meals (7 lunches + 7 dinners) and 7 breakfasts.

### Step 2: Meal Planning & Protein Sizing
Apply the mathematical standards from `GROCERY_PLANNING_RULES.md`:
* **Raw meat shrinkage factor**: Raw meat reduces by ~25–35% when cooked.
  $$\text{Raw Meat Needed} = \frac{\text{Target Cooked Portion}}{0.70}$$
* **Portion targets**:
  - ~250g–300g raw protein per single meal.
  - ~500g–600g raw protein per double-portion meal prep (lunch + dinner).
  - Total weekly raw protein needed: **~2.0 kg – 2.4 kg**.
* **Freshness Matrix (Consumption Schedule)**:
  - **Days 1–3 (High Perishability)**: Fresh minced meat (*Hakkliha*), fresh chicken breast (*Broilerifilee*), baby greens/arugula (*Rukola*).
  - **Days 4–5 (Moderate Perishability)**: Bananas, avocados (fridge-paused ripening), toast bread, corn-coated chicken.
  - **Days 6–7 (Long Shelf Life)**: Cherry plum tomatoes, red bell peppers, onions, pantry/freezer backup meals.

### Step 3: Shopping List Mapping (Estonia / Wolt)
Map ingredients to real Estonian store packaging and terms (e.g. Wolt Market Maakri / Selver):
* **Real Minced Meat (NO Cold Cuts)**: `Rakvere kodune hakkliha` (400g) $\times 2$ (800g total).
* **Corn-Coated Chicken**: `Tallegg maisikattega kanafilee` (280g) $\times 2$ (560g total).
* **Fresh Chicken Breast**: `Tallegg broileririnnafilee` (400g–500g) $\times 1$.
* **Produce by Weight vs Units**:
  - `Banaan` $\times 6$ (~1.1 kg bananas for the week).
  - `Sibul 1kg` / `Mugulsibul 1kg` (1 kg mesh bag).
  - `Paprika punane` $\times 2$ (~400g red peppers).
  - `Avokaado karbis` $\times 1$ (2-pack ready to eat).
  - `Kirssploomtomat` $\times 1$ (punnet 250g–500g).
  - `Rukola` $\times 1$ (box/bag 100g–125g).
  - `Riivjuust mozzarella` $\times 1$ (150g–200g).
  - `Eesti Pagar Tosta` $\times 1$ (500g).

### Step 4: Executing Wolt Automation (`wolt_manager.py`)
Run the local automation script:

```bash
# Full weekly grocery order
./.venv/Scripts/python wolt_manager.py add --store wolt-market-maakri --full

# Single item test
./.venv/Scripts/python wolt_manager.py add --store wolt-market-maakri

# Custom store / items
./.venv/Scripts/python wolt_manager.py add --store selver-abc-liivalaia --items "Banaan" "Rukola"
```

#### Key Automation Guarantees in `wolt_manager.py`:
1. **Persistent Session**: Uses local profile `.wolt_profile` to bypass Google OAuth and bot-detection.
2. **Auto-Dismiss Interceptors**:
   - Rejects "Continue previous order" prompts to start from clean 0.00 €.
   - Selects first address in "Where?" modal and closes dialogs cleanly.
   - Auto-closes accidental "Edit address" modals.
3. **Modal Stepper Quantity Adjustment**: Opens product modals directly and clicks the `+` stepper to set exact quantities (e.g., 2 packs, 6 bananas) before submitting.
4. **Strict Meat Filtering**: Excludes cold cuts / sausages (*doktorivorst, keeduvorst, sink, viiner*) when searching for fresh meat.
5. **Real-time Price Increment Verification**: Verifies cart total price increases in euros after every item.
6. **Safe Checkout Policy**: Never clicks checkout/payment; leaves the cart open on screen for the user to review and finalize.
