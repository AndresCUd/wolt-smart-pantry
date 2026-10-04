---
name: wolt-smart-pantry
description: >-
  Analyzes kitchen, fridge, freezer, and pantry inventory photos or lists, maintains persistent virtual
  pantry memory across weekly purchases, inspects live Wolt store catalogs and deals, calculates balanced
  weekly meal plans with cooking shrinkage math and freshness scheduling, and automates cart creation on Wolt.
---

# Wolt Smart Pantry & Grocery Assistant

This skill enables the agent to audit any user's kitchen inventory, maintain persistent virtual pantry memory across weekly purchases, explore live store catalogs on Wolt to discover active promotions and in-stock items, dynamically compute required ingredients based on missing meal slots and thermal cooking shrinkage, and automatically assemble an exact grocery cart on Wolt.

---

## 🛠️ System Overview & Core Workflow

```mermaid
flowchart TD
    A1["📸 Photos Uploaded (Recalibrate)"] --> B["1. Dynamic Stock & Memory Audit\n(Check pantry_memory.json or scan new photos)"]
    A2["💬 Text Prompt: 'Plan Week 2'\n(No photos needed)"] --> B
    
    B --> C["🔍 2. Live Store Inspection & Deals\n(Explore Wolt catalog, active discounts & real packaging)"]
    C --> D["📐 3. Nutritional Sizing & Freshness Matrix\n(Apply thermal shrinkage math & schedule meals Days 1-7)"]
    
    D --> E{"Autonomy Mode?"}
    
    E -->|Supervised Mode (Default)| F["✋ Checkpoint 1: User Meal Plan Approval\n(Present plan & shopping list for confirmation/swaps)"]
    F -->|Approved| G["🛒 4. Automated Cart Assembly\n(Execute wolt_manager.py add)"]
    G --> H["👀 5. Open Order Review in Browser"]
    H --> I["✋ Checkpoint 2: Purchase Confirmation\n(Ask user if order placed -> Record pantry_memory.json)"]
    
    E -->|Autonomous Cart Mode (--auto)| J["⚡ 4. Direct Cart Assembly & Memory Update\n(Execute wolt_manager.py add --auto)"]
    J --> H
    
    H --> K{"Payment Execution?"}
    K -->|Safe Default (No --auto-pay)| L["🛡️ Safe Manual Checkout\n(User clicks 'Order and pay' in browser)"]
    K -->|Explicit User Opt-In (--auto-pay)| M["💳 Automated Payment Submission\n(Execute wolt_manager.py add --auto-pay)"]
```

---

## 🚦 Autonomy Levels & Execution Modes

| Mode | Trigger / Flag | Checkpoint 1 (Meal Plan Review) | Checkpoint 2 (Memory Commit) | Payment Submission | Best Used When |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **Supervised Mode (Default)** | Default prompt | **Yes** (waits for user approval) | **Yes** (asks if purchase finished) | **Manual (Safe)** | User wants to review meals, customize ingredients, and manually pay. |
| **Autonomous Cart Mode** | `--auto`, `-y`, *"full auto cart"*, *"build cart directly"* | **Bypassed** (proceeds immediately) | **Auto-Committed** (auto-records to memory) | **Manual (Safe)** | User wants instant 1-click cart ready on screen without auto-charging card. |
| **Automated Payment Mode** | `--auto-pay`, *"order and pay for me"* | **Bypassed / Approved** | **Auto-Committed** | **Automated (Explicit Opt-In)** | User explicitly wants the agent to complete checkout and submit payment. *(Never enabled by `--auto`).* |

---

## 📋 Step-by-Step Instructions

### Step 1: Dynamic Inventory & Persistent Pantry Memory Audit
Before generating a meal plan, check if previous pantry state exists:

```bash
# Check tracked long-term staples, proteins, and purchase history:
python wolt_manager.py pantry --action status
```

* **Scenario A: User uploads new photos**: Audit photos for staples, proteins, and produce; recalibrate virtual memory.
* **Scenario B: User prompts without photos (e.g., *"Plan my meals for this week"*):** Read active staples from `pantry_memory.json` (Zero Repurchase of onions, oils, rice, seasonings); only buy missing weekly proteins and Tier 1–2 perishables.

### Step 2: Live Store Catalog & Deals Exploration
Inspect the target store venue in real time using `wolt_manager.py search` or `wolt_manager.py deals`:

```bash
# Discover live products, exact packaging weights, and current prices:
python wolt_manager.py search --store wolt-market-maakri --queries "hakkliha" "kanafilee" "banaan" "paprika" "rukola"

# Or scan for active store discounts and promotional campaigns:
python wolt_manager.py deals --store wolt-market-maakri
```

### Step 3: Nutritional Sizing & Freshness Matrix Calculation
Using the live store data and formulas from `GROCERY_PLANNING_RULES.md`:
* **Calculate remaining meal slots**:
  $$\text{Missing Main Meals} = (\text{Days} \times \text{Main Meals per Day}) - \text{Meals Covered by In-Stock / Memory Goods}$$
* **Thermal Shrinkage Factor**: Raw meat and fish lose 20–35% mass during cooking ($W_{\text{raw}} = \frac{W_{\text{cooked}}}{1 - \text{Shrinkage}}$).
  - Poultry / Red Meat: ~250g–300g raw per meal (~500g–600g for a 2-portion meal prep).
  - Fish & Seafood: ~220g–260g raw per meal.
  - Vegetarian: 150g–200g tofu or 2–3 eggs per meal.
* **Select Best Live In-Stock Items**: Prioritize items on promotion or with the best price-to-weight ratio discovered in Step 2.
* **Freshness Hierarchy (7-Day Schedule)**:
  - Days 1–3: High perishables (fresh minced meat, delicate greens, fresh fish).
  - Days 4–5: Moderate shelf-life (coated poultry, bananas, refrigerated avocados, bread).
  - Days 6–7: Hardy produce (bell peppers, cherry tomatoes, root veggies) & pantry backup meals.

### Step 4: Decision & Cart Execution

#### In Supervised Mode (Default):
1. **Present Checkpoint 1**: Display proposed 7-day meal plan and itemized grocery list.
2. Ask the user: *"Would you like to make any changes to meals, swap any ingredients, or should I proceed to build this cart on Wolt?"*
3. Once approved, execute:
   ```bash
   python wolt_manager.py add --store wolt-market-maakri --items "Rakvere homemade minced meat, 400g:2" "Banaan:6" "Rukola:1" "Paprika punane:2"
   ```
4. **Checkpoint 2 (Post-Order)**: Once review is opened, ask user if order was completed, then commit memory:
   ```bash
   python wolt_manager.py pantry --action record --store wolt-market-maakri --items "Rakvere homemade minced meat, 400g:2" "Banaan:6" "Rukola:1" "Paprika punane:2"
   ```

#### In Autonomous Cart Mode (`--auto`):
Directly execute cart assembly without pre-approval, updating memory, but leaving payment for manual review:
```bash
python wolt_manager.py add --store wolt-market-maakri --auto --items "Rakvere homemade minced meat, 400g:2" "Banaan:6" "Rukola:1" "Paprika punane:2"
```

#### In Automated Payment Mode (`--auto-pay` - Explicit Opt-In Only):
If and only if the user explicitly instructs to complete payment automatically:
```bash
python wolt_manager.py add --store wolt-market-maakri --auto-pay --items "Rakvere homemade minced meat, 400g:2" "Banaan:6" "Rukola:1" "Paprika punane:2"
```

---

## 🛡️ Automation Guarantees & Safety Heuristics

1. **Safe Checkout Default**: `--auto` builds the cart but **NEVER** submits payment. Automatic payment requires explicit `--auto-pay`.
2. **User Confirmation Checkpoints**: Full decision power with checkpoints, or instant cart assembly when requested.
3. **Persistent Pantry Memory (`pantry_memory.json`)**: Eliminates the need to take repetitive kitchen photos every single week.
4. **Persistent Browser Session (`.wolt_profile`)**: Retains authentication and address settings without cloud IP blocking or captcha walls.
5. **Live Catalog Verification**: Inspects live store availability, eliminating guesswork or outdated hardcoded product names.
6. **Modal Stepper Automation**: Adjusts quantities inside product modals using the `+` stepper button.
7. **Cold-Cut Safety Filter**: Excludes processed sausages, bologna, and cold cuts when looking for real raw meats.
8. **Real-Time Price Verification**: Confirms that the cart total in euros updates after each item.
