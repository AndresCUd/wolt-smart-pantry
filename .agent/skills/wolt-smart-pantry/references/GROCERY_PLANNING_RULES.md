# 📋 Weekly Grocery & Meal Planning Rules (Wolt / E-Commerce)

This document defines the mathematical, nutritional, and culinary framework for calculating a weekly (7-day) grocery shopping list from existing kitchen inventory images (fridge, freezer, pantry), guaranteeing:

1. **Adequate protein and caloric intake** (preventing under-portioning from cooking shrinkage).
2. **Zero food waste** (following a strict freshness and shelf-life hierarchy).
3. **Maximum utilization of existing stock** (dry staples, spices, canned goods, frozen backups).
4. **Accurate packaging and quantity mapping** for e-commerce grocery stores (Wolt Market, Selver, Rimi, etc.).

---

## 1. Protein Sizing & Nutritional Math

### 📉 Thermal Shrinkage Factor (Raw to Cooked)
* Fresh raw meat and poultry lose between **25% and 35% of their weight in moisture and fat** during cooking.
* **Calculation Formula:**
  $$\text{Required Raw Weight} = \frac{\text{Target Cooked Portion}}{0.70}$$
* **Portion Standards:**
  - Standard cooked protein portion per meal: **~150g – 200g**.
  - Required raw protein per meal: **~250g – 300g**.
  - Double-portion batch cooking (Lunch + Dinner / Meal Prep): **~500g – 600g raw minimum**.

### 📊 Weekly Protein Goal (7 Days / 14 Main Meals)
* Total weekly requirement: **~2.0 kg – 2.4 kg equivalent raw protein**, distributed across:
  1. **Fresh Minced Meat (*Hakkliha*)**: 2 packs (800g total) $\rightarrow$ 3 to 4 meals (Bolognese pasta, smash burgers, meatballs).
  2. **Corn-Coated Chicken Fillet (*Maisikattega kanafilee*)**: 2 packs (560g total) $\rightarrow$ 2 quick dinners.
  3. **Fresh Chicken Breast (*Broilerifilee*)**: 1 pack (400g – 500g) $\rightarrow$ 2 meals (Stir-fries, fajitas, pan-seared chicken).
  4. **Pantry Canned Tuna / Frozen Fish**: 2 to 3 meals (Tuna pasta, frozen seafood stir-fry).
  5. **Eggs & Greek Yogurt (In Stock)**: Daily high-protein breakfasts and snacks.

---

## 2. Freshness Hierarchy & Shelf-Life Matrix

To ensure zero spoilage across the 7-day cycle, all fresh ingredients follow a strict consumption schedule:

```mermaid
flowchart LR
    A["Tier 1: Days 1 to 3 (Highly Perishable)"] --> B["Tier 2: Days 4 to 5 (Moderate Shelf-Life)"] --> C["Tier 3: Days 6 to 7 (Hardy & Long-Lasting)"]
```

| Tier | Category | Shelf Life | Examples | Handling Strategy |
| :---: | :--- | :---: | :--- | :--- |
| **1** | **Baby Greens & Raw Minced Meat** | 2 – 4 days | Arugula (*Rukola*), spinach, fresh minced meat. | **Consume within the first 3 days.** Cook raw minced meat on Days 1–2; once cooked into sauce/stew, it keeps safely in the fridge for up to 5 days. Store greens with a paper towel to absorb excess moisture. |
| **2** | **Fresh Fruit & Sandwich Bread** | 4 – 6 days | Bananas (*Banaan*), avocados, toast bread. | **Temperature staggering:** Leave 1 avocado on the counter to ripen for Days 1–3, and place the 2nd in the fridge to pause ripening until Days 4–7. Freeze half the bread loaf and toast directly from frozen. |
| **3** | **Hard Vegetables** | 7 – 10 days | Cherry plum tomatoes (*Kirssploomtomat*), red bell peppers (*Paprika*). | Thick-skinned vegetables resist moisture loss and easily remain fresh throughout the full 7-day week. |
| **4** | **Pantry Staples** | 2 – 8 weeks | Onions (*Sibul 1kg*), garlic, dry pasta, flour, canned goods. | Purchase 1kg net bags for cost efficiency; zero risk of weekly spoilage. |

---

## 3. Store Unit Mapping (Estonian E-Commerce / Wolt Market)

Grocery platforms sell items either as **loose single units**, **pre-packaged net bags**, or **sealed trays**.

| Ingredient | Estonian Search Query | Store Selling Format | Conversion & Target Quantity |
| :--- | :--- | :--- | :--- |
| **Fresh Minced Meat** | `Rakvere kodune hakkliha` | Sealed tray (400g) | **Target: 2 trays** (800g total pure beef/pork meat). |
| **Corn Chicken Fillet** | `Tallegg maisikattega` | Sealed tray (280g) | **Target: 2 trays** (560g total chicken). |
| **Fresh Chicken Breast**| `Tallegg broileririnnafilee`| Sealed tray (400g–500g)| **Target: 1 tray** (400g–500g fresh breast). |
| **Bananas** | `Banaan` | Loose unit (~180g each)| **Target: 6 units** (~1.1 kg total, 1 per day). |
| **Onions** | `Sibul 1kg` / `Mugulsibul 1kg` | Packaged net (1 kg) | **Target: 1 net bag** (economical 1 kg). |
| **Red Bell Pepper** | `Paprika punane` | Loose unit (~200g each)| **Target: 2 units** (~400g total). |
| **Avocados** | `Avokaado karbis` | 2-pack box | **Target: 1 box** (2 ready-to-eat avocados). |
| **Cherry Plum Tomatoes**| `Kirssploomtomat` | Plastic punnet (250g–500g)| **Target: 1 punnet**. |
| **Arugula / Rocket** | `Rukola` | Box / bag (100g–125g) | **Target: 1 pack**. |
| **Grated Mozzarella** | `Riivjuust mozzarella` | Bag (150g–200g) | **Target: 1 bag** (for pizza/pasta bakes). |
| **Toast Bread** | `Eesti Pagar Tosta` | Packaged loaf (500g) | **Target: 1 loaf**. |

---

## 4. Step-by-Step Shopping List Algorithm

For any future weekly meal plan:

1. **Inventory Audit:**
   - Scan kitchen photos for carbohydrates (pasta, flour, rice, oats).
   - Identify existing pantry proteins (canned fish, eggs, dairy, frozen goods).
   - Note seasonings, oils, yeast, and sauces in stock.
2. **Meal Slot Gap Calculation (14 main meals + 7 breakfasts):**
   - Deduct meals covered by existing stock (e.g. 2 tuna pasta meals, 2 homemade pizza dinners).
   - Calculate remaining required meals (e.g. 10 meals).
3. **Protein Allocation:**
   - Remaining meals $\times$ 250g raw protein = Total fresh protein needed.
   - Allocate into 2–3 distinct protein categories (e.g. minced meat, chicken breast, fish).
4. **Produce Allocation by Freshness Hierarchy:**
   - 1 Tier 1 item (quick greens for Days 1–3).
   - 2 Tier 2 items (fruits & staggered avocados for Days 1–5).
   - 2 Tier 3 items (tomatoes & bell peppers for Days 1–7).
   - 1 Tier 4 staple (1kg onion bag).
5. **Automation Execution:**
   - Map to Estonian store queries and execute via `wolt_manager.py`.
