"""
wolt_core.planner
Handles 7-day meal schedule construction, portion calculations with 30% thermal shrinkage math,
freshness tier scheduling, recipe delivery, grocery candidate calculation, and meal consumption tracking.
"""

import os
import json
from datetime import datetime

from .config import get_meal_plan_file, load_user_preferences
from .pantry import load_pantry_memory, save_pantry_memory

# Default sample grocery list for quick testing
DEFAULT_GROCERY_LIST = [
    ("Tallegg broilerifilee 500g", 2),
    ("Maks & Moorits kodune hakkliha 500g", 2),
    ("Banaan", 6),
    ("Punane paprika", 2),
    ("Rukola 100g", 1)
]

# Baseline 7-Day Balanced Grocery Basket for Wolt Market
SAMPLE_WEEKLY_GROCERY_LIST = [
    ("Muna L 10tk", 2),                     # 20 eggs (breakfast daily scrambles)
    ("Eesti Pagar Tosta röstsai", 1),       # Breakfast toast
    ("Avokaado 2tk", 1),                    # Fresh avocado
    ("Kollane sibul 1kg", 1),               # Yellow onion (staple base)
    ("Rannarootsi Veisehakkliha 500g", 2),  # Fresh ground beef (Days 1-3)
    ("Tallegg Broilerifilee 500g", 2),      # Fresh chicken breast fillets (Days 3-6)
    ("Rukola 100g", 1),                     # Ultra-fresh peppery salad greens
    ("Kirsstomat 250g", 2),                 # Sweet fresh cherry tomatoes
    ("Punane paprika", 2),                  # Sweet red bell peppers
    ("Valio Riivitud Mozzarella 200g", 2),  # Cheeses / pasta bakes
    ("Banaan", 7)                           # Daily fresh fruit snack
]

def get_candidate_grocery_list(prefs: dict, user_id=None) -> list[tuple[str, int]]:
    """Constructs a personalized candidate grocery shopping list tailored by diet, allergies, household size, and breakfast needs."""
    h_size = max(1, prefs.get("household_size", 1))
    diet = prefs.get("diet_type", "omnivore").lower()
    allergies = [a.lower() for a in prefs.get("allergies", [])]
    avoided = [av.lower() for av in prefs.get("avoided_ingredients", [])]
    
    egg_packs = max(1, int(round((14 * h_size) / 10)))
    bread_packs = max(1, int(round((7 * h_size) / 7)))
    avocado_packs = max(1, int(round((4 * h_size) / 2)))
    
    # 1. Base Essentials
    items = [
        ("Muna L 10tk", egg_packs),
        ("Eesti Pagar Tosta röstsai", bread_packs),
        ("Avokaado 2tk", avocado_packs),
        ("Kollane sibul 1kg", 1),
    ]

    # 2. Proteins by Diet Type
    prot_packs = max(1, int(2 * h_size))
    if diet == "pescatarian":
        items.append(("Lõhefilee 400g", prot_packs))
        items.append(("Tursk või valge kala 400g", prot_packs))
    elif diet in ["vegetarian", "vegan"]:
        items.append(("Tofu 300g", prot_packs))
        items.append(("Kikerherned konserv 400g", prot_packs))
    else:
        if not any(a in "ground beef hakkliha veis" for a in allergies + avoided):
            items.append(("Rannarootsi Veisehakkliha 500g", prot_packs))
        if not any(a in "chicken broiler kana" for a in allergies + avoided):
            items.append(("Tallegg Broilerifilee 500g", prot_packs))

    # 3. Produce & Dairy
    items.extend([
        ("Rukola 100g", max(1, int(1 * h_size))),
        ("Kirsstomat 250g", max(1, int(2 * h_size))),
        ("Punane paprika", max(1, int(2 * h_size))),
        ("Valio Riivitud Mozzarella 200g", max(1, int(2 * h_size))),
        ("Banaan", max(1, int(7 * h_size)))
    ])

    return items

def generate_weekly_meal_plan(inventory_items=None, prefs=None, user_id=None, user_suggestions: str = None):
    """Generates an inventory-aligned, 7-day meal plan with breakfast, lunch, dinner, and snacks.
    Dynamically uses Gemini when configured (incorporating custom suggestions), with robust deterministic fallback.
    Respects cooking thermal shrinkage (W_raw = W_cooked / 0.70), user allergies, and freshness tiers."""
    if prefs is None:
        prefs = load_user_preferences(user_id=user_id)
        
    # 0. Attempt Dynamic AI Meal Plan Generation with Gemini
    from .gemini import generate_ai_weekly_meal_plan
    ai_plan = generate_ai_weekly_meal_plan(inventory_items=inventory_items, prefs=prefs, user_id=user_id, user_suggestions=user_suggestions)
    if ai_plan:
        return ai_plan
    
    diet = prefs.get("diet_type", "omnivore").lower()
    h_size = max(1, prefs.get("household_size", 1))
    
    raw_p_g = int(215 * h_size)
    cooked_p_g = int(150 * h_size)
    
    known_items = []
    if inventory_items:
        for it in inventory_items:
            name = it[0] if isinstance(it, (tuple, list)) else (it.get("name") or it.get("query") if isinstance(it, dict) else str(it))
            known_items.append(name.lower())
    else:
        pantry = load_pantry_memory(user_id=user_id)
        for p in pantry.get("proteins", []):
            known_items.append(p.get("name", "").lower())
        for pr in pantry.get("produce", []):
            known_items.append(pr.get("name", "").lower())
        for s in pantry.get("staples", []):
            known_items.append(s.get("name", "").lower())
        if not known_items:
            candidate = get_candidate_grocery_list(prefs, user_id=user_id)
            for it in candidate:
                known_items.append(it[0].lower())

    has_beef = any(any(k in it for k in ["hakkliha", "veis", "beef"]) for it in known_items)
    has_chicken = any(any(k in it for k in ["kana", "broiler", "chicken", "filee"]) for it in known_items)
    has_salmon = any(any(k in it for k in ["lõhe", "kala", "salmon", "trout", "fish"]) for it in known_items)
    has_tofu = any(any(k in it for k in ["tofu", "soija", "plant"]) for it in known_items)
    
    if diet == "pescatarian":
        has_salmon = True
        has_chicken = False
        has_beef = False
    elif diet in ["vegetarian", "vegan"]:
        has_tofu = True
        has_chicken = False
        has_beef = False
        has_salmon = False

    day_configs = [
        ("Monday", "Tier 1: Ultra-Fresh (48h max)", "🌱 Consume fresh leafy greens, fresh ground meat & ripe avocados first!"),
        ("Tuesday", "Tier 1: Ultra-Fresh (72h max)", "🥬 Use remaining fresh herbs, delicate salad greens & fresh mince."),
        ("Wednesday", "Tier 2: Resilient Poultry & Veg", "🥦 Prime condition for chicken breast fillets, bell peppers & broccoli."),
        ("Thursday", "Tier 2: Resilient Poultry & Veg", "🥕 Chicken, sweet peppers, carrots & eggs are in peak flavor."),
        ("Friday", "Tier 3: Hearty Proteins & Cheeses", "🧀 Great day for melted mozzarella, pasta bakes & burger bowls."),
        ("Saturday", "Tier 4: Freezer & Pantry Reserves", "🧊 Tap into pantry grains, canned tomato passata & frozen cuts."),
        ("Sunday", "Tier 4: Fridge Clearing & Reset", "📦 Cook a big vegetable omelette / frittata to clear stock before next delivery.")
    ]

    safe_snacks = [
        {"title": "Fresh Banana with Dark Chocolate", "ingredients": ["Fresh banana", "2 squares dark chocolate (70%+ kakao)"]},
        {"title": "Crisp Apple Slices with Cinnamon", "ingredients": ["Crisp red/green apple", "Ground Ceylon cinnamon"]},
        {"title": "Greek Yogurt & Honey Bowl", "ingredients": [f"Greek yogurt ({150 * h_size}g)", "Raw honey (1 tbsp)"]},
        {"title": "Toasted Sourdough with Butter & Flaky Salt", "ingredients": ["Sourdough toast slice", "Farm butter", "Sea salt flakes"]},
        {"title": "Fresh Banana with Honey & Pumpkin Seeds", "ingredients": ["Fresh banana", "Pumpkin seeds (1 tbsp)", "Honey"]},
        {"title": "Hard-Boiled Farm Eggs with Sea Salt", "ingredients": [f"{2 * h_size} Eggs", "Flaky sea salt", "Black pepper"]},
        {"title": "Warm Herbal Tea with Cinnamon Toast", "ingredients": ["Chamomile tea", "Toasted bread", "Butter & cinnamon"]}
    ]

    days_data = []
    for idx, (day_name, tier, alert) in enumerate(day_configs):
        if idx == 0:
            breakfast = {
                "title": "3-Egg Scramble with Smashed Avocado & Warm Toast",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Eesti Pagar Tosta bread", "Fresh avocado", "Butter / Olive oil", "Sea salt"],
                "tip": "🔥 **Heat:** Medium-low with 10g cold butter.\n👨‍🍳 **Technique:** Whisk eggs with a pinch of salt. Pour into foaming butter and gently sweep with a silicone spatula from the edges to the center for 90 seconds. Remove from heat while still slightly glossy and creamy. Spread mashed avocado seasoned with lemon & flaky sea salt over hot crisp toast."
            }
        elif idx == 1:
            breakfast = {
                "title": "Fluffy Mozzarella & Cherry Tomato Omelette",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Grated mozzarella cheese", "Cherry tomatoes", "Tosta bread", "Fresh arugula"],
                "tip": "🔥 **Heat:** Medium heat with a non-stick skillet.\n👨‍🍳 **Technique:** Halve cherry tomatoes and blister them in the dry pan for 60s first, then set aside. Pour whisked eggs in, gently swirling to coat. When the base sets (2 mins), scatter mozzarella and warm tomatoes over one half. Fold the omelette over, turn off heat, and let the cheese melt in the trapped steam for 1 minute."
            }
        elif idx == 2:
            breakfast = {
                "title": "Greek Yogurt Power Bowl with Fresh Banana & Honey",
                "protein_raw": f"{200 * h_size}g Greek yogurt (18g protein)",
                "ingredients": ["Greek yogurt", "Fresh banana", "Honey", "Pumpkin seeds / Chia"],
                "tip": "🍯 **Technique:** Spoon thick chilled Greek yogurt into a wide shallow bowl. Slice fresh bananas on a 45° angle. Lightly toast seeds in a dry pan for 2 minutes until fragrant and nutty, then scatter over the bowl. Finish with a warm swirl of raw honey and a pinch of cinnamon."
            }
        elif idx == 3:
            breakfast = {
                "title": "Sweet Bell Pepper Shakshuka with Toasted Sourdough",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Red bell pepper", "Onion", "Crushed tomatoes", "Tosta bread"],
                "tip": "🔥 **Heat:** Gentle medium simmer.\n👨‍🍳 **Technique:** Sauté diced bell peppers and onions in olive oil for 6 mins until soft and sweet. Stir in crushed tomatoes and minced garlic; simmer 5 mins until thick. Make small wells with a spoon, crack eggs directly into the sauce, cover with a tight lid for 4-5 mins until the whites are opaque but the yolks stay runny."
            }
        elif idx == 4:
            breakfast = {
                "title": "Smashed Avocado & Fried Egg Toast with Melted Mozzarella",
                "protein_raw": f"{2 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Eesti Pagar Tosta", "Avocado", "Mozzarella", "Black pepper"],
                "tip": "🍳 **Lace-Edge Method:** Toast bread with a layer of grated mozzarella until bubbling. In a separate skillet, heat 1 tbsp olive oil or butter on medium-high until sizzling. Crack eggs in and baste hot fat over the whites with a spoon for crispy lace edges."
            }
        elif idx == 5:
            breakfast = {
                "title": "Weekend Loaded Scramble with Fresh Arugula & Blistered Tomatoes",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Fresh arugula", "Cherry tomatoes", "Butter", "Toasted bread"],
                "tip": "🍅 **Technique:** Blister whole cherry tomatoes in a hot skillet until their skins burst and caramelize. Softly scramble eggs in cold butter on low heat. Fold in the fresh peppery arugula and warm cherry tomatoes during the final 10 seconds off-heat."
            }
        else:
            breakfast = {
                "title": "Big Sunday Brunch Frittata with Caramelized Onions & Cheese",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Onions", "Mozzarella", "Bell peppers", "Herbs"],
                "tip": "🍳 **Pan-to-Broiler Method:** Slowly sweat thinly sliced onions with a pinch of salt for 8 minutes until golden and sweet. Pour whisked eggs over, top generously with mozzarella. Cook on low heat until edges set (4 mins), then place the skillet under the oven broiler (220°C) for 3 minutes."
            }

        # Lunch
        if has_salmon and idx == 0:
            lunch = {
                "title": "Pan-Seared Salmon & Herb Basmati Rice",
                "protein_raw": f"{raw_p_g}g fresh salmon (yields ~{cooked_p_g}g cooked)",
                "ingredients": ["Fresh salmon fillet", "Basmati rice", "Arugula salad", "Lemon & olive oil"],
                "tip": "🐟 **Crispy Skin Method:** Pat salmon skin dry with paper towels and season with salt. Place skin-side down in a hot pan with 1 tbsp olive oil. Press gently for 30s. Cook 4 mins on skin until 80% opaque, flip for 1-2 mins with lemon juice & 10g butter."
            }
        elif has_beef and idx in [0, 1, 4]:
            if idx == 0:
                lunch = {
                    "title": "Lean Beef & Vegetable Rice Skillet",
                    "protein_raw": f"{raw_p_g}g ground beef / veisehakkliha (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Minced beef", "Red bell pepper", "Yellow onion", "Basmati rice", "Soy sauce & garlic"],
                    "tip": "🥩 **High-Heat Sear:** Heat skillet until smoking hot with 1 tbsp oil. Add ground beef in chunks without stirring for 2 mins to build a deep savory crust. Break apart, toss in diced onions and bell peppers for 3 mins. Deglaze with 1 tbsp soy sauce and garlic before serving over fluffy basmati rice."
                }
            elif idx == 1:
                lunch = {
                    "title": "Classic Italian Beef Bolognese with Pasta",
                    "protein_raw": f"{raw_p_g}g ground beef (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Minced beef", "Crushed tomatoes", "Onion & garlic", "Pasta", "Grated mozzarella / Parmesan"],
                    "tip": "🍝 **Sauce Emulsification:** Brown beef with finely chopped onions and garlic until caramelized. Pour in tomato passata, season with oregano and black pepper, simmer 15 mins. Cook pasta al dente and toss directly in the sauce with 2 tbsp pasta water."
                }
            else:
                lunch = {
                    "title": "Gourmet Beef Smash Burger Bowl with Potatoes",
                    "protein_raw": f"{raw_p_g}g ground beef (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Ground beef patties", "Mozzarella / Cheddar", "Crispy oven potatoes", "Cherry tomatoes", "Arugula"],
                    "tip": "🍔 **Smash Sear & Crispy Potatoes:** Cube potatoes and roast at 210°C with olive oil & paprika for 25 mins. Shape ground beef into balls, place in smoking hot skillet, press firmly flat with spatula. Sear 2 mins, flip, top with mozzarella, cover 1 min to melt."
                }
        elif has_chicken:
            lunch = {
                "title": "Crispy Pan-Seared Chicken & Roasted Veggies",
                "protein_raw": f"{raw_p_g}g chicken fillet (yields ~{cooked_p_g}g cooked)",
                "ingredients": ["Chicken breast fillet", "Red bell pepper", "Baby potatoes / Rice", "Garlic butter"],
                "tip": "🍗 **Juicy Sear & Rest Rule:** Pat chicken fillet dry with paper towels and slice horizontally into cutlets. Season with salt, black pepper, and garlic. Sear in foaming butter for 4 mins on medium-high without moving. Flip for 3 mins. Rest 3 mins before slicing."
            }
        else:
            lunch = {
                "title": "Mediterranean Mozzarella & Tomato Basil Penne",
                "protein_raw": f"{raw_p_g}g protein / cheese",
                "ingredients": ["Penne pasta", "Cherry tomatoes", "Mozzarella", "Garlic & olive oil", "Fresh arugula"],
                "tip": "🍅 **Warm Blister Sauce:** Cook penne in salted boiling water. Warm olive oil with minced garlic and halved cherry tomatoes over medium heat for 4 mins until tomatoes burst. Toss drained pasta into the pan off-heat with mozzarella cubes."
            }

        # Dinner
        if has_chicken and idx in [0, 2, 3, 6]:
            if idx == 0:
                dinner = {
                    "title": "Garlic Butter Chicken Fillet with Arugula & Tomatoes",
                    "protein_raw": f"{raw_p_g}g chicken breast (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Chicken breast (Tallegg)", "Fresh arugula", "Cherry tomatoes", "Mozzarella", "Olive oil & lemon"],
                    "tip": "🧄 **Butter Basting Method:** Sear chicken fillets for 4 mins per side. In the last 2 minutes, toss in 2 crushed garlic cloves and 15g butter; tilt the pan and spoon foaming butter continuously over the chicken breast."
                }
            elif idx == 2:
                dinner = {
                    "title": "Crispy Sheet-Pan Chicken & Sweet Peppers",
                    "protein_raw": f"{raw_p_g}g chicken fillet (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Chicken breast / cuts", "Red bell peppers", "Yellow onions", "Olive oil & paprika"],
                    "tip": "🥕 **High-Heat Caramelization:** Cut chicken and bell peppers into bite-sized strips. Toss on baking sheet with olive oil, smoked paprika, salt, and black pepper. Roast at 200°C for 18-20 mins until lightly charred and tender."
                }
            elif idx == 3:
                dinner = {
                    "title": "Creamy Garlic Chicken & Mozzarella Pasta",
                    "protein_raw": f"{raw_p_g}g chicken fillet (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Chicken breast", "Pasta", "Garlic", "Mozzarella", "Cherry tomatoes"],
                    "tip": "🍝 **One-Skillet Sauce:** Sauté garlic and sliced chicken until golden. Add crushed tomatoes, cream/butter, and simmer 5 mins. Stir in hot al dente pasta and grated mozzarella on low heat until rich and glossy."
                }
            else:
                dinner = {
                    "title": "Slow-Simmered Chicken & Vegetable Curry with Rice",
                    "protein_raw": f"{raw_p_g}g chicken (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Chicken fillet", "Onions", "Bell peppers", "Curry spices", "Basmati rice"],
                    "tip": "🍛 **Spice Blooming Secret:** Fry diced onions in oil with curry powder, turmeric, and garlic for 90s until fragrant. Add diced chicken to brown, simmer with peppers and a splash of water for 15 mins on low heat."
                }
        elif has_beef:
            dinner = {
                "title": "Hearty Beef Skillet with Bell Peppers & Rice",
                "protein_raw": f"{raw_p_g}g ground beef (yields ~{cooked_p_g}g cooked)",
                "ingredients": ["Ground beef", "Bell peppers", "Onions", "Rice", "Soy sauce"],
                "tip": "🥩 **Crispy Mince Technique:** Cook minced beef on high heat undisturbed for 3 mins to get deep browning. Stir in sliced onions and peppers for 3 mins. Season with soy sauce and black pepper, fold into hot basmati rice."
            }
        else:
            dinner = {
                "title": "Rustic Shakshuka Dinner with Sourdough Toast",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Crushed tomatoes", "Bell peppers", "Mozzarella", "Toast"],
                "tip": "🍳 **Jammy Egg Control:** Simmer peppers and onions in tomato sauce until thick. Create small pockets, drop in fresh eggs, sprinkle mozzarella around whites. Cover tightly with lid on low heat for exactly 4 minutes."
            }

        days_data.append({
            "day_index": idx,
            "day_name": day_name,
            "freshness_tier": tier,
            "freshness_alert": alert,
            "breakfast": breakfast,
            "lunch": lunch,
            "dinner": dinner,
            "snack": safe_snacks[idx % len(safe_snacks)]
        })

    res_plan = {
        "last_generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "household_size": h_size,
        "diet_type": diet,
        "days": days_data
    }
    if user_suggestions and user_suggestions.strip():
        res_plan["user_suggestions"] = user_suggestions.strip()
    return res_plan

def generate_default_weekly_plan(prefs=None, user_id=None):
    """Fallback alias for generating default weekly plan."""
    return generate_weekly_meal_plan(inventory_items=None, prefs=prefs, user_id=user_id)

def load_meal_plan(user_id=None):
    """Loads active weekly meal plan from disk or initializes plan for specific user."""
    target_file = get_meal_plan_file(user_id)
    if os.path.exists(target_file):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    plan = generate_weekly_meal_plan(user_id=user_id)
    save_meal_plan(plan, user_id=user_id)
    return plan

def save_meal_plan(plan, user_id=None):
    """Saves updated weekly meal plan to disk for specific user."""
    target_file = get_meal_plan_file(user_id)
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    print(f"[📋] Active meal plan saved: {target_file}")

def get_day_menu_formatted(day_index=None, user_id=None):
    """Returns a rich formatted text message for a specific day's menu with Breakfast, Lunch, Dinner, Snack."""
    plan = load_meal_plan(user_id=user_id)
    days = plan.get("days", [])
    if not days:
        plan = generate_weekly_meal_plan(user_id=user_id)
        days = plan.get("days", [])
        save_meal_plan(plan, user_id=user_id)
        
    now = datetime.now()
    if day_index is None:
        day_index = now.weekday()
        
    day_index = max(0, min(6, day_index))
    day_data = days[day_index] if day_index < len(days) else days[0]
    
    date_str = now.strftime("%A, %b %d")
    h_size = plan.get("household_size", 1)
    
    breakfast = day_data.get("breakfast", {})
    lunch = day_data.get("lunch", {})
    dinner = day_data.get("dinner", {})
    snack = day_data.get("snack", {})

    pantry_data = load_pantry_memory(user_id=user_id)
    staples = pantry_data.get("staples", [])
    proteins = pantry_data.get("proteins", [])
    produce = pantry_data.get("produce", [])
    history = pantry_data.get("purchase_history", [])
    is_pantry_empty = not (staples or proteins or produce or history)
    
    pantry_notice = ""
    if is_pantry_empty:
        pantry_notice = (
            "⚠️ *Despensa Vacía / Empty Pantry Notice:*\n"
            "• _Tu memoria virtual de despensa aún no tiene productos registrados._\n"
            "• _Este menú es tu plan recomendado semanal. Usa `/plan` para generar tu lista de compras en Wolt o envía una foto con `/stock` para añadir tus ingredientes._\n"
            "━━━━━━━━━━━━━━━━━━━━━\n\n"
        )

    text = (
        f"{pantry_notice}"
        f"🌅 *TODAY'S MENU — {date_str}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 *Portions:* {h_size} person(s) | 📅 *Schedule:* Day {day_index + 1}/7 ({day_data.get('day_name', 'Today')})\n"
        f"🛡️ *Freshness:* `{day_data.get('freshness_tier', 'Standard')}`\n\n"
        
        f"🍳 *BREAKFAST: {breakfast.get('title', 'High-Protein Breakfast')}*\n"
        f"• *Ingredients:* {', '.join(breakfast.get('ingredients', []))}\n"
        f"👨‍🍳 *Chef Tip:*\n{breakfast.get('tip', '')}\n\n"
        
        f"🥗 *LUNCH: {lunch.get('title', 'Healthy Lunch')}*\n"
        f"• *Protein:* {lunch.get('protein_raw', 'Standard portion')}\n"
        f"• *Ingredients:* {', '.join(lunch.get('ingredients', []))}\n"
        f"👨‍🍳 *Chef Tip:*\n{lunch.get('tip', '')}\n\n"
        
        f"🍲 *DINNER: {dinner.get('title', 'Chef Dinner')}*\n"
        f"• *Protein:* {dinner.get('protein_raw', 'Standard portion')}\n"
        f"• *Ingredients:* {', '.join(dinner.get('ingredients', []))}\n"
        f"👨‍🍳 *Chef Tip:*\n{dinner.get('tip', '')}\n\n"
        
        f"🍎 *SNACK: {snack.get('title', 'Healthy Snack')}*\n"
        f"• {', '.join(snack.get('ingredients', []))}\n\n"
        
        f"💡 *Freshness Reminder:*\n"
        f"{day_data.get('freshness_alert', 'Keep fresh produce stored properly.')}\n"
    )
    return text, day_data

def get_single_meal_formatted(meal_type="lunch", day_index=None, user_id=None):
    """Returns a rich formatted text message focusing on a single meal with exact food breakdown and weights."""
    plan = load_meal_plan(user_id=user_id)
    days = plan.get("days", [])
    if not days:
        plan = generate_weekly_meal_plan(user_id=user_id)
        days = plan.get("days", [])
        save_meal_plan(plan, user_id=user_id)
        
    now = datetime.now()
    if day_index is None:
        day_index = now.weekday()
        
    day_index = max(0, min(6, day_index))
    day_data = days[day_index] if day_index < len(days) else days[0]
    
    date_str = now.strftime("%A, %b %d")
    h_size = plan.get("household_size", 1)
    meal_type = meal_type.lower()
    
    emoji_map = {
        "breakfast": "🍳",
        "lunch": "🥗",
        "dinner": "🍲",
        "snack": "🍎"
    }
    emoji = emoji_map.get(meal_type, "🍽️")
    meal = day_data.get(meal_type, day_data.get("lunch", {}))
    
    food_breakdown = []
    if meal_type == "breakfast":
        food_breakdown = [
            f"• 🥚 *Farm Eggs:* {3 * h_size} eggs (~{180 * h_size}g raw)",
            f"• 🍞 *Toast Bread:* {2 * h_size} slices (~{70 * h_size}g, Eesti Pagar Tosta)",
            f"• 🥑 *Fresh Avocado:* ~{0.5 * h_size:.1f} avocado (~{75 * h_size}g)",
            f"• 🧈 *Butter / Olive Oil:* ~{10 * h_size}g"
        ]
    elif meal_type == "lunch":
        raw_g = int(215 * h_size)
        cooked_g = int(150 * h_size)
        protein_raw = meal.get("protein_raw", f"{raw_g}g protein")
        food_breakdown = [
            f"• 🥩 *Protein (Raw):* {protein_raw}",
            f"• 🍳 *Cooked Yield Target:* ~{cooked_g}g cooked",
            f"• 🍚 *Grains / Carbs:* ~{70 * h_size}g dry Basmati rice (yields ~{200 * h_size}g cooked)",
            f"• 🫑 *Fresh Produce:* 1 Red bell pepper (~150g) + 1/2 Yellow onion (~60g)",
            f"• 🧄 *Seasoning:* 1 clove garlic, 15ml low-sodium soy sauce"
        ]
    elif meal_type == "dinner":
        raw_g = int(215 * h_size)
        cooked_g = int(150 * h_size)
        protein_raw = meal.get("protein_raw", f"{raw_g}g protein")
        food_breakdown = [
            f"• 🍗 *Protein (Raw):* {protein_raw}",
            f"• 🍳 *Cooked Yield Target:* ~{cooked_g}g cooked",
            f"• 🥬 *Delicate Greens:* ~{40 * h_size}g Fresh Arugula / Rocket",
            f"• 🍅 *Cherry Tomatoes:* ~{80 * h_size}g (5-6 ripe cherry tomatoes)",
            f"• 🧀 *Cheese:* ~{30 * h_size}g Grated Mozzarella / Parmigiano",
            f"• 🫒 *Fats & Dressing:* 10ml Extra Virgin Olive Oil + Fresh Lemon Juice"
        ]
    else:
        food_breakdown = [
            f"• 🍌 *Fruit:* {1 * h_size} Fresh Banana (~120g)",
            f"• 🍫 *Dark Chocolate:* {2 * h_size} squares 70%+ dark chocolate (~20g)"
        ]

    pantry_data = load_pantry_memory(user_id=user_id)
    is_pantry_empty = not (pantry_data.get("staples") or pantry_data.get("proteins") or pantry_data.get("produce") or pantry_data.get("purchase_history"))
    p_note = "\n⚠️ _Nota: Despensa vacía. Usa `/plan` para pedir en Wolt o `/stock` para añadir stock._" if is_pantry_empty else ""

    text = (
        f"{emoji} *{meal_type.upper()}: {meal.get('title', 'Meal')}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"📅 *Date:* {date_str} (Day {day_index + 1}/7)\n"
        f"👥 *Portions:* {h_size} person(s)\n\n"
        
        f"⚖️ *Exact Food & Quantity Used:*\n" +
        "\n".join(food_breakdown) + "\n\n"
        
        f"👨‍🍳 *Chef Preparation & Cooking Technique:*\n"
        f"{meal.get('tip', 'Cook with care and season to taste.')}\n\n"
        
        f"🧊 *Freshness Tier:* `{day_data.get('freshness_tier', 'Standard')}`\n"
        f"💡 _{day_data.get('freshness_alert', '')}_{p_note}"
    )
    return text, meal

def log_meal_consumption(meal_type="lunch", day_index=None, user_id=None):
    """Records meal consumption, deducts used ingredients from virtual pantry memory,
    and returns a breakdown of food used and remaining inventory."""
    plan = load_meal_plan(user_id=user_id)
    days = plan.get("days", [])
    now = datetime.now()
    if day_index is None:
        day_index = now.weekday()
    day_index = max(0, min(6, day_index))
    day_data = days[day_index] if day_index < len(days) else (days[0] if days else {})
    
    h_size = plan.get("household_size", 1)
    meal = day_data.get(meal_type, {})
    meal_title = meal.get("title", f"{meal_type.capitalize()}")
    
    pantry = load_pantry_memory(user_id=user_id)
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
    
    used_summary = []
    low_stock_alerts = []
    
    if meal_type == "breakfast":
        eggs_used = 3 * h_size
        used_summary = [f"{eggs_used} Farm Eggs", f"{2 * h_size} slices Toast Bread", f"1/2 Avocado", "10g Butter"]
        egg_entry = next((p for p in pantry.get("proteins", []) if "egg" in p.get("name", "").lower() or "muna" in p.get("name", "").lower()), None)
        if egg_entry:
            current_qty = egg_entry.get("qty", 10)
            remaining_eggs = max(0, current_qty - eggs_used)
            egg_entry["qty"] = remaining_eggs
            if remaining_eggs <= 2:
                low_stock_alerts.append(f"⚠️ *Eggs Low:* Only {remaining_eggs} egg(s) left in fridge!")
    elif meal_type in ["lunch", "dinner"]:
        raw_meat_g = int(215 * h_size)
        cooked_meat_g = int(150 * h_size)
        used_summary = [
            f"{raw_meat_g}g raw protein (~{cooked_meat_g}g cooked yield)",
            f"{150 * h_size}g fresh vegetables",
            f"{70 * h_size}g carbs / grains",
            f"10ml olive oil / seasoning"
        ]
        if pantry.get("proteins"):
            p_entry = pantry["proteins"][0]
            curr_q = p_entry.get("qty", 1)
            p_entry["qty"] = max(0, curr_q - 1)
            if p_entry["qty"] == 0:
                low_stock_alerts.append(f"⚠️ *Protein Used Up:* `{p_entry.get('name')}` is now finished!")
    else:
        used_summary = [f"{1 * h_size} Fresh Banana", f"20g Dark Chocolate"]
        
    pantry.setdefault("consumption_history", []).append({
        "timestamp": now_str,
        "meal_type": meal_type,
        "title": meal_title,
        "food_used": used_summary
    })
    
    save_pantry_memory(pantry, user_id=user_id)
    
    text = (
        f"✅ *Meal Logged: {meal_type.upper()}*\n"
        f"🍽️ *Dish:* {meal_title}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ *Food Quantities Deducted:*\n"
    )
    for u in used_summary:
        text += f"• `{u}`\n"
        
    if low_stock_alerts:
        text += "\n🚨 *Inventory Stock Alerts:*\n"
        for alert in low_stock_alerts:
            text += f"{alert}\n"
            
    text += f"\n📦 *Pantry Memory Updated:* View remaining items with `/pantry`."
    return text, used_summary
