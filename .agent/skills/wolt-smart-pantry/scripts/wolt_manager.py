"""
Wolt Smart Shopping Automation Assistant
Automates item search, quantity selection, and cart creation on Wolt with persistent session handling.
"""

import os
import sys
import time
import json
import re
import argparse
from datetime import datetime

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.sync_api import sync_playwright

# Persistent browser profile path (relative and portable)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, ".wolt_profile")
PANTRY_MEMORY_FILE = os.path.join(BASE_DIR, "pantry_memory.json")
USER_PREFERENCES_FILE = os.path.join(BASE_DIR, "user_preferences.json")
MEAL_PLAN_FILE = os.path.join(BASE_DIR, "meal_plan.json")

def load_user_preferences():
    """Loads persistent user dietary preferences, allergies, and food avoidances."""
    if os.path.exists(USER_PREFERENCES_FILE):
        try:
            with open(USER_PREFERENCES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "diet_type": "omnivore",
        "allergies": [],
        "avoided_ingredients": [],
        "preferred_proteins": ["chicken", "ground beef", "salmon", "eggs"],
        "household_size": 1,
        "notes": ""
    }

def save_user_preferences(prefs):
    """Saves updated user dietary preferences and allergy profile to disk."""
    import json
    with open(USER_PREFERENCES_FILE, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)
    print(f"[👤] User dietary preferences saved: {USER_PREFERENCES_FILE}")

def is_item_allowed(item_name: str, prefs=None) -> tuple[bool, str]:
    """Checks if an item violates any user allergies or food avoidance rules."""
    if prefs is None:
        prefs = load_user_preferences()
    
    item_lower = item_name.lower()
    
    # 1. Check allergies
    for allergy in prefs.get("allergies", []):
        if allergy.strip().lower() and allergy.strip().lower() in item_lower:
            return False, f"Allergy violation: '{allergy}'"

    # 2. Check avoided ingredients
    for avoid in prefs.get("avoided_ingredients", []):
        if avoid.strip().lower() and avoid.strip().lower() in item_lower:
            return False, f"Avoided food: '{avoid}'"
            
    # 3. Check diet type exclusions
    diet = prefs.get("diet_type", "omnivore").lower()
    if diet in ["vegetarian", "vegan"]:
        if any(meat in item_lower for meat in ["hakkliha", "kana", "broiler", "veis", "sea", "beef", "chicken", "pork", "meat", "kala", "salmon", "lõhe", "fish"]):
            return False, f"Not compatible with {diet} diet"
    elif diet == "pescatarian":
        if any(meat in item_lower for meat in ["hakkliha", "kana", "broiler", "veis", "sea", "beef", "chicken", "pork", "meat"]):
            return False, "Not compatible with pescatarian diet (poultry/red meat)"

    return True, ""

def display_user_preferences():
    """Prints a formatted summary of the user's dietary preferences and allergies."""
    prefs = load_user_preferences()
    print("\n" + "="*60)
    print("👤 USER DIETARY PROFILE & ALLERGY PREFERENCES")
    print("="*60)
    print(f"• Diet Type: {prefs.get('diet_type', 'omnivore').capitalize()}")
    print(f"• Household Size: {prefs.get('household_size', 1)} person(s)")
    print(f"• Allergies: {', '.join(prefs.get('allergies', [])) if prefs.get('allergies') else 'None recorded'}")
    print(f"• Avoided Foods: {', '.join(prefs.get('avoided_ingredients', [])) if prefs.get('avoided_ingredients') else 'None recorded'}")
    print(f"• Preferred Proteins: {', '.join(prefs.get('preferred_proteins', [])) if prefs.get('preferred_proteins') else 'Standard'}")
    if prefs.get("notes"):
        print(f"• Custom Notes: {prefs.get('notes')}")
    print("="*60 + "\n")
    return prefs

def generate_weekly_meal_plan(inventory_items=None, prefs=None):
    """Generates an inventory-aligned, 7-day meal plan with breakfast, lunch, dinner, and snacks.
    Respects cooking thermal shrinkage (W_raw = W_cooked / 0.70), user allergies, and freshness tiers."""
    if prefs is None:
        prefs = load_user_preferences()
    
    diet = prefs.get("diet_type", "omnivore").lower()
    h_size = max(1, prefs.get("household_size", 1))
    
    # Target raw weight per person: ~215g raw yields ~150g cooked
    raw_p_g = int(215 * h_size)
    cooked_p_g = int(150 * h_size)
    
    # 1. Collect all known inventory items
    known_items = []
    if inventory_items:
        for it in inventory_items:
            name = it[0] if isinstance(it, (tuple, list)) else (it.get("name") or it.get("query") if isinstance(it, dict) else str(it))
            known_items.append(name.lower())
    else:
        pantry = load_pantry_memory()
        for p in pantry.get("proteins", []):
            known_items.append(p.get("name", "").lower())
        for pr in pantry.get("produce", []):
            known_items.append(pr.get("name", "").lower())
        for s in pantry.get("staples", []):
            known_items.append(s.get("name", "").lower())
        if not known_items:
            candidate = get_candidate_grocery_list(prefs)
            for it in candidate:
                known_items.append(it[0].lower())

    # Check available inventory tags
    has_beef = any(any(k in it for k in ["hakkliha", "veis", "beef"]) for it in known_items)
    has_chicken = any(any(k in it for k in ["kana", "broiler", "chicken", "filee"]) for it in known_items)
    has_salmon = any(any(k in it for k in ["lõhe", "kala", "salmon", "trout", "fish"]) for it in known_items)
    has_tofu = any(any(k in it for k in ["tofu", "soija", "plant"]) for it in known_items)
    
    # Fallback to diet exclusions
    if diet == "pescatarian":
        has_salmon = True
        has_chicken = False
        has_beef = False
    elif diet in ["vegetarian", "vegan"]:
        has_tofu = True
        has_chicken = False
        has_beef = False
        has_salmon = False

    # 2. Build 7-day schedule with Breakfast, Lunch, Dinner, Snack
    days_data = []
    
    # Day Names & Freshness Tiers
    day_configs = [
        ("Monday", "Tier 1: Ultra-Fresh (48h max)", "🌱 Consume fresh leafy greens, fresh ground meat & ripe avocados first!"),
        ("Tuesday", "Tier 1: Ultra-Fresh (72h max)", "🥬 Use remaining fresh herbs, delicate salad greens & fresh mince."),
        ("Wednesday", "Tier 2: Resilient Poultry & Veg", "🥦 Prime condition for chicken breast fillets, bell peppers & broccoli."),
        ("Thursday", "Tier 2: Resilient Poultry & Veg", "🥕 Chicken, sweet peppers, carrots & eggs are in peak flavor."),
        ("Friday", "Tier 3: Hearty Proteins & Cheeses", "🧀 Great day for melted mozzarella, pasta bakes & burger bowls."),
        ("Saturday", "Tier 4: Freezer & Pantry Reserves", "🧊 Tap into pantry grains, canned tomato passata & frozen cuts."),
        ("Sunday", "Tier 4: Fridge Clearing & Reset", "📦 Cook a big vegetable omelette / frittata to clear stock before next delivery.")
    ]

    # Allergen-safe snacks (strictly avoids peanuts, shellfish, and user-avoided foods)
    safe_snacks = [
        {"title": "Fresh Banana with Dark Chocolate", "ingredients": ["Fresh banana", "2 squares dark chocolate (70%+ kakao)"]},
        {"title": "Crisp Apple Slices with Cinnamon", "ingredients": ["Crisp red/green apple", "Ground Ceylon cinnamon"]},
        {"title": "Greek Yogurt & Honey Bowl", "ingredients": [f"Greek yogurt ({150 * h_size}g)", "Raw honey (1 tbsp)"]},
        {"title": "Toasted Sourdough with Butter & Flaky Salt", "ingredients": ["Sourdough toast slice", "Farm butter", "Sea salt flakes"]},
        {"title": "Fresh Banana with Honey & Pumpkin Seeds", "ingredients": ["Fresh banana", "Pumpkin seeds (1 tbsp)", "Honey"]},
        {"title": "Hard-Boiled Farm Eggs with Sea Salt", "ingredients": [f"{2 * h_size} Eggs", "Flaky sea salt", "Black pepper"]},
        {"title": "Warm Herbal Tea with Cinnamon Toast", "ingredients": ["Chamomile tea", "Toasted bread", "Butter & cinnamon"]}
    ]

    # Build meals per day based on what is in stock
    for idx, (day_name, tier, alert) in enumerate(day_configs):
        # 🍳 Breakfast
        if idx == 0:
            breakfast = {
                "title": "3-Egg Scramble with Smashed Avocado & Warm Toast",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Eesti Pagar Tosta bread", "Fresh avocado", "Butter / Olive oil", "Sea salt"],
                "tip": "🔥 **Heat:** Medium-low with 10g cold butter.\n👨‍🍳 **Technique:** Whisk eggs with a pinch of salt. Pour into foaming butter and gently sweep with a silicone spatula from the edges to the center for 90 seconds. Remove from heat while still slightly glossy and creamy (residual pan heat finishes them). Spread mashed avocado seasoned with lemon & flaky sea salt over hot crisp toast."
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
                "tip": "🍯 **Technique:** Spoon thick chilled Greek yogurt into a wide shallow bowl. Slice fresh bananas on a 45° angle. Lightly toast seeds in a dry pan for 2 minutes until fragrant and nutty, then scatter over the bowl. Finish with a warm swirl of raw honey and a pinch of cinnamon for maximum flavor contrast."
            }
        elif idx == 3:
            breakfast = {
                "title": "Sweet Bell Pepper Shakshuka with Toasted Sourdough",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Red bell pepper", "Onion", "Crushed tomatoes", "Tosta bread"],
                "tip": "🔥 **Heat:** Gentle medium simmer.\n👨‍🍳 **Technique:** Sauté diced bell peppers and onions in olive oil for 6 mins until soft and sweet. Stir in crushed tomatoes and minced garlic; simmer 5 mins until thick. Make small wells with a spoon, crack eggs directly into the sauce, cover with a tight lid for 4-5 mins until the whites are opaque but the yolks stay runny and jammy."
            }
        elif idx == 4:
            breakfast = {
                "title": "Smashed Avocado & Fried Egg Toast with Melted Mozzarella",
                "protein_raw": f"{2 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Eesti Pagar Tosta", "Avocado", "Mozzarella", "Black pepper"],
                "tip": "🍳 **Lace-Edge Method:** Toast bread with a layer of grated mozzarella until bubbling. In a separate skillet, heat 1 tbsp olive oil or butter on medium-high until sizzling. Crack eggs in and baste hot fat over the whites with a spoon. You'll get crispy, caramelized lace edges while keeping the yolk rich and runny."
            }
        elif idx == 5:
            breakfast = {
                "title": "Weekend Loaded Scramble with Fresh Arugula & Blistered Tomatoes",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Fresh arugula", "Cherry tomatoes", "Butter", "Toasted bread"],
                "tip": "🍅 **Technique:** Blister whole cherry tomatoes in a hot skillet until their skins burst and caramelize. Softly scramble eggs in cold butter on low heat. Fold in the fresh peppery arugula and warm cherry tomatoes during the final 10 seconds off-heat so the greens wilt delicately without releasing excess moisture."
            }
        else: # Sunday
            breakfast = {
                "title": "Big Sunday Brunch Frittata with Caramelized Onions & Cheese",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Onions", "Mozzarella", "Bell peppers", "Herbs"],
                "tip": "🍳 **Pan-to-Broiler Method:** Slowly sweat thinly sliced onions with a pinch of salt for 8 minutes until golden and sweet. Pour whisked eggs over, top generously with mozzarella. Cook on low heat until edges set (4 mins), then place the skillet under the oven broiler (220°C) for 3 minutes until puffed, golden, and bubbly."
            }

        # 🥗 Lunch & 🍲 Dinner (Dynamic based on proteins)
        if has_salmon and idx == 0:
            lunch = {
                "title": "Pan-Seared Salmon & Herb Basmati Rice",
                "protein_raw": f"{raw_p_g}g fresh salmon (yields ~{cooked_p_g}g cooked)",
                "ingredients": ["Fresh salmon fillet", "Basmati rice", "Arugula salad", "Lemon & olive oil"],
                "tip": "🐟 **Crispy Skin Method:** Pat salmon skin completely dry with paper towels and season with salt. Place skin-side down in a hot pan with 1 tbsp olive oil. Press gently for 30s with a spatula so skin stays flat. Cook 4 mins on skin until 80% opaque, flip for 1-2 mins with lemon juice & 10g butter. Rest 2 mins before serving."
            }
        elif has_beef and idx in [0, 1, 4]:
            if idx == 0:
                lunch = {
                    "title": "Lean Beef & Vegetable Rice Skillet",
                    "protein_raw": f"{raw_p_g}g ground beef / veisehakkliha (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Minced beef", "Red bell pepper", "Yellow onion", "Basmati rice", "Soy sauce & garlic"],
                    "tip": "🥩 **High-Heat Sear:** Heat skillet until smoking hot with 1 tbsp oil. Add ground beef in chunks without stirring for 2 mins to build a deep savory crust (Maillard reaction). Break apart, toss in diced onions and bell peppers for 3 mins. Deglaze with 1 tbsp soy sauce and garlic right before serving over fluffy basmati rice."
                }
            elif idx == 1:
                lunch = {
                    "title": "Classic Italian Beef Bolognese with Pasta",
                    "protein_raw": f"{raw_p_g}g ground beef (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Minced beef", "Crushed tomatoes", "Onion & garlic", "Pasta", "Grated mozzarella / Parmesan"],
                    "tip": "🍝 **Sauce Emulsification:** Brown beef with finely chopped onions and garlic until caramelized. Pour in tomato passata, season with oregano and black pepper, and simmer gently for 15 mins. Cook pasta al dente (1 min less than box instructions) and toss directly in the sauce with 2 tbsp pasta water so sauce clings silky to every bite."
                }
            else:
                lunch = {
                    "title": "Gourmet Beef Smash Burger Bowl with Potatoes",
                    "protein_raw": f"{raw_p_g}g ground beef (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Ground beef patties", "Mozzarella / Cheddar", "Crispy oven potatoes", "Cherry tomatoes", "Arugula"],
                    "tip": "🍔 **Smash Sear & Crispy Potatoes:** Cube potatoes and roast at 210°C with olive oil & paprika for 25 mins until crispy. Shape ground beef into balls, place in a smoking hot skillet, and press firmly flat with a spatula. Sear 2 mins until deeply browned, flip, top with mozzarella, cover 1 min to melt, and assemble over warm potatoes and arugula."
                }
        elif has_chicken:
            lunch = {
                "title": "Crispy Pan-Seared Chicken & Roasted Veggies",
                "protein_raw": f"{raw_p_g}g chicken fillet (yields ~{cooked_p_g}g cooked)",
                "ingredients": ["Chicken breast fillet", "Red bell pepper", "Baby potatoes / Rice", "Garlic butter"],
                "tip": "🍗 **Juicy Sear & Rest Rule:** Pat chicken fillet dry with paper towels and slice horizontally into even cutlets. Season with salt, black pepper, and garlic. Sear in foaming butter for 4 mins on medium-high heat without moving. Flip for 3 mins. Transfer to a cutting board and let rest for 3 full minutes before slicing to keep all juices inside."
            }
        else:
            lunch = {
                "title": "Mediterranean Mozzarella & Tomato Basil Penne",
                "protein_raw": f"{raw_p_g}g protein / cheese",
                "ingredients": ["Penne pasta", "Cherry tomatoes", "Mozzarella", "Garlic & olive oil", "Fresh arugula"],
                "tip": "🍅 **Warm Blister Sauce:** Cook penne in well-salted boiling water. In a pan, warm olive oil with minced garlic and halved cherry tomatoes over medium heat for 4 mins until tomatoes burst into a natural sauce. Toss drained hot pasta into the pan off-heat with mozzarella cubes so the cheese melts into creamy pockets."
            }

        # Dinner
        if has_chicken and idx in [0, 2, 3, 6]:
            if idx == 0:
                dinner = {
                    "title": "Garlic Butter Chicken Fillet with Arugula & Tomatoes",
                    "protein_raw": f"{raw_p_g}g chicken breast (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Chicken breast (Tallegg)", "Fresh arugula", "Cherry tomatoes", "Mozzarella", "Olive oil & lemon"],
                    "tip": "🧄 **Butter Basting Method:** Sear chicken fillets for 4 mins per side. In the last 2 minutes, toss in 2 crushed garlic cloves and 15g butter; tilt the pan and spoon the fragrant foaming butter continuously over the chicken breast. Serve alongside fresh arugula tossed with fresh lemon juice and extra virgin olive oil."
                }
            elif idx == 2:
                dinner = {
                    "title": "Crispy Sheet-Pan Chicken & Sweet Peppers",
                    "protein_raw": f"{raw_p_g}g chicken fillet (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Chicken breast / cuts", "Red bell peppers", "Yellow onions", "Olive oil & paprika"],
                    "tip": "🥕 **High-Heat Caramelization:** Cut chicken and bell peppers into bite-sized strips. Toss on a baking sheet with olive oil, smoked paprika, salt, and black pepper in a single layer (don't crowd the pan). Roast at 200°C for 18-20 mins until the pepper edges are lightly charred and chicken is golden and tender."
                }
            elif idx == 3:
                dinner = {
                    "title": "Creamy Garlic Chicken & Mozzarella Pasta",
                    "protein_raw": f"{raw_p_g}g chicken fillet (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Chicken breast", "Pasta", "Garlic", "Mozzarella", "Cherry tomatoes"],
                    "tip": "🍝 **One-Skillet Sauce:** Sauté garlic and sliced chicken until golden. Add crushed tomatoes, cream/butter, and simmer 5 mins. Stir in hot al dente pasta and grated mozzarella on low heat until the sauce turns rich, glossy, and stretches with melted cheese."
                }
            else:
                dinner = {
                    "title": "Slow-Simmered Chicken & Vegetable Curry with Rice",
                    "protein_raw": f"{raw_p_g}g chicken (yields ~{cooked_p_g}g cooked)",
                    "ingredients": ["Chicken fillet", "Onions", "Bell peppers", "Curry spices", "Basmati rice"],
                    "tip": "🍛 **Spice Blooming Secret:** Fry diced onions in oil with curry powder, turmeric, and garlic for 90 seconds until fragrant (blooming the spices). Add diced chicken to brown, then simmer with peppers and a splash of water for 15 mins on low heat until the sauce is deeply aromatic and chicken is fork-tender."
                }
        elif has_beef:
            dinner = {
                "title": "Hearty Beef Skillet with Bell Peppers & Rice",
                "protein_raw": f"{raw_p_g}g ground beef (yields ~{cooked_p_g}g cooked)",
                "ingredients": ["Ground beef", "Bell peppers", "Onions", "Rice", "Soy sauce"],
                "tip": "🥩 **Crispy Mince Technique:** Cook minced beef on high heat undisturbed for 3 mins to get deep browning. Stir in sliced onions and peppers for 3 mins. Season with soy sauce and black pepper, then fold into hot basmati rice with a drizzle of toasted sesame or olive oil."
            }
        else:
            dinner = {
                "title": "Rustic Shakshuka Dinner with Sourdough Toast",
                "protein_raw": f"{3 * h_size} Farm eggs",
                "ingredients": ["Eggs", "Crushed tomatoes", "Bell peppers", "Mozzarella", "Toast"],
                "tip": "🍳 **Jammy Egg Control:** Simmer peppers and onions in rich tomato sauce until thick. Create small pockets, drop in fresh eggs, and sprinkle mozzarella around the whites. Cover tightly with a lid on low heat for exactly 4 minutes. The whites will cook through while the yolks stay rich and liquid for dipping."
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

    return {
        "last_generated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "household_size": h_size,
        "diet_type": diet,
        "days": days_data
    }

def generate_default_weekly_plan(prefs=None):
    """Fallback alias for generating default weekly plan."""
    return generate_weekly_meal_plan(inventory_items=None, prefs=prefs)

def load_meal_plan():
    """Loads active weekly meal plan from disk or initializes plan."""
    if os.path.exists(MEAL_PLAN_FILE):
        try:
            with open(MEAL_PLAN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    plan = generate_weekly_meal_plan()
    save_meal_plan(plan)
    return plan

def save_meal_plan(plan):
    """Saves updated weekly meal plan to disk."""
    with open(MEAL_PLAN_FILE, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False)
    print(f"[📋] Active meal plan saved: {MEAL_PLAN_FILE}")

def get_day_menu_formatted(day_index=None):
    """Returns a rich formatted text message for a specific day's menu with Breakfast, Lunch, Dinner, Snack."""
    plan = load_meal_plan()
    days = plan.get("days", [])
    if not days:
        plan = generate_weekly_meal_plan()
        days = plan.get("days", [])
        save_meal_plan(plan)
        
    now = datetime.now()
    if day_index is None:
        # Python weekday(): 0 is Monday, 6 is Sunday
        day_index = now.weekday()
        
    day_index = max(0, min(6, day_index))
    day_data = days[day_index] if day_index < len(days) else days[0]
    
    date_str = now.strftime("%A, %b %d")
    h_size = plan.get("household_size", 1)
    
    breakfast = day_data.get("breakfast", {})
    lunch = day_data.get("lunch", {})
    dinner = day_data.get("dinner", {})
    snack = day_data.get("snack", {})

    text = (
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

def get_single_meal_formatted(meal_type="lunch", day_index=None):
    """Returns a rich formatted text message focusing on a single meal with exact food breakdown and weights."""
    plan = load_meal_plan()
    days = plan.get("days", [])
    if not days:
        plan = generate_weekly_meal_plan()
        days = plan.get("days", [])
        save_meal_plan(plan)
        
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
    
    # Generate exact food breakdown with grams and units
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
    else: # Snack
        food_breakdown = [
            f"• 🍌 *Fruit:* {1 * h_size} Fresh Banana (~120g)",
            f"• 🍫 *Dark Chocolate:* {2 * h_size} squares 70%+ dark chocolate (~20g)"
        ]

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
        f"💡 _{day_data.get('freshness_alert', '')}_"
    )
    return text, meal

def log_meal_consumption(meal_type="lunch", day_index=None):
    """Records meal consumption, deducts used ingredients from virtual pantry memory,
    and returns a breakdown of food used and remaining inventory."""
    plan = load_meal_plan()
    days = plan.get("days", [])
    now = datetime.now()
    if day_index is None:
        day_index = now.weekday()
    day_index = max(0, min(6, day_index))
    day_data = days[day_index] if day_index < len(days) else (days[0] if days else {})
    
    h_size = plan.get("household_size", 1)
    meal = day_data.get(meal_type, {})
    meal_title = meal.get("title", f"{meal_type.capitalize()}")
    
    pantry = load_pantry_memory()
    now_str = now.strftime("%Y-%m-%dT%H:%M:%S")
    
    # Determine used food items
    used_summary = []
    low_stock_alerts = []
    
    if meal_type == "breakfast":
        eggs_used = 3 * h_size
        used_summary = [f"{eggs_used} Farm Eggs", f"{2 * h_size} slices Toast Bread", f"1/2 Avocado", "10g Butter"]
        # Deduct eggs
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
        # Deduct protein
        if pantry.get("proteins"):
            p_entry = pantry["proteins"][0]
            curr_q = p_entry.get("qty", 1)
            p_entry["qty"] = max(0, curr_q - 1)
            if p_entry["qty"] == 0:
                low_stock_alerts.append(f"⚠️ *Protein Used Up:* `{p_entry.get('name')}` is now finished!")
    else: # Snack
        used_summary = [f"{1 * h_size} Fresh Banana", f"20g Dark Chocolate"]
        
    if "consumption_history" not in pantry:
        pantry["consumption_history"] = []
        
    pantry["consumption_history"].append({
        "timestamp": now_str,
        "meal_type": meal_type,
        "title": meal_title,
        "food_used": used_summary
    })
    
    save_pantry_memory(pantry)
    
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

def generate_ai_recipe(dish_name: str, ingredients: list = None, prefs: dict = None, api_key: str = None) -> str:
    """Generates an award-winning chef recipe using Gemini 3.8 Flash based on in-stock ingredients & preferences."""
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY", "")
    if prefs is None:
        prefs = load_user_preferences()
        
    ing_text = ", ".join(ingredients) if ingredients else "available in-stock pantry items"
    allergies = ", ".join(prefs.get("allergies", [])) or "None"
    avoided = ", ".join(prefs.get("avoided_ingredients", [])) or "None"
    diet = prefs.get("diet_type", "omnivore")
    h_size = prefs.get("household_size", 1)

    if api_key:
        for model_name in ["gemini-3.8-flash", "gemini-3.5-flash-lite"]:
            try:
                from google import genai
                client = genai.Client(api_key=api_key)
                prompt = f"""
You are an award-winning private chef.
Create a practical, gourmet step-by-step recipe for '{dish_name}' serving {h_size} person(s).

Available ingredients: {ing_text}.
Dietary Profile: {diet}. Allergies to strictly avoid: {allergies}. Disliked/avoided ingredients: {avoided}.

Format your response in clean GitHub Markdown for Telegram mobile chat:
🍽️ *RECIPE: {dish_name.upper()}*
━━━━━━━━━━━━━━━━━━━━━
⏱️ *Prep Time:* X mins | 🍳 *Cook Time:* Y mins | 👥 *Servings:* {h_size}

📋 *Ingredients & Measurements:*
(list itemized with exact grams/spoons)

🔥 *Step-by-Step Cooking Technique:*
1. (Detailed step with exact pan heat and visual cues)
2. (Detailed step with exact timing and technique)
3. (Finishing and sauce emulsification step)

👨‍🍳 *Chef Secret & Pro Tip:*
(A specific culinary technique that elevates this dish, e.g. pan deglazing, butter basting, crust searing, seasoning timing)

🥗 *Plating & Serving Suggestion:*
(Short appetizing presentation tip)
"""
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                resp_text = response.text if hasattr(response, "text") else str(response)
                if resp_text and resp_text.strip():
                    return resp_text.strip()
            except Exception as e:
                print(f"[!] Gemini recipe error with {model_name}: {e}")

    # Fallback recipe template if no API key
    return (
        f"🍽️ *CHEF RECIPE: {dish_name.upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 *Servings:* {h_size} person(s) | ⏱️ *Total Time:* ~20 mins\n\n"
        f"📋 *Key Ingredients:*\n"
        f"• {ing_text}\n"
        f"• Olive oil / Farm butter, sea salt, black pepper, garlic\n\n"
        f"🔥 *Step-by-Step Technique:*\n"
        f"1. **Prep:** Pat proteins and produce dry with paper towels; season lightly with salt & pepper.\n"
        f"2. **Sear:** Heat skillet on medium-high with olive oil/butter. Sear protein undisturbed for 4 mins to develop a golden crust.\n"
        f"3. **Sauté & Deglaze:** Toss in sliced vegetables and garlic for 3 mins. Deglaze the pan with a splash of water or soy sauce.\n"
        f"4. **Rest & Assemble:** Rest protein for 3 minutes before carving so juices stay inside. Assemble over hot grains or crisp toast.\n\n"
        f"👨‍🍳 *Chef Pro Tip:*\n"
        f"To unlock personalized dynamic recipes generated by Gemini 3.8 Flash, connect your free Gemini API key using `/setkey`!\n"
    )

def analyze_photo_with_vision(image_path: str, api_key: str = None) -> dict:
    """Analyzes a food or kitchen photo using Gemini Multimodal Vision (gemini-3.8-flash) or intelligent fallback."""
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY", "")
        
    if api_key:
        for model_name in ["gemini-3.8-flash", "gemini-3.5-flash-lite"]:
            try:
                from google import genai
                from PIL import Image
                
                client = genai.Client(api_key=api_key)
                pil_img = Image.open(image_path)
                
                prompt = """
You are an expert culinary vision AI, nutritionist, and smart pantry inventory auditor.
Analyze the provided food photo with high precision.

Determine:
1. Is this a cooked meal plate (breakfast, lunch, dinner, snack), a pantry/fridge audit photo, or a receipt?
2. If it is a cooked meal plate:
   - Identify the meal type: breakfast, lunch, dinner, or snack.
   - Descriptive gourmet dish title.
   - Categorized composition breakdown:
     - proteins: exact list of proteins detected (e.g. 1 Sunny-side-up egg, 3 bacon strips, 180g chicken breast)
     - carbs: exact list of carbs/grains/breads (e.g. 2 slices toasted bread, 180g cooked basmati rice, pasta)
     - produce: exact list of fresh fruits, vegetables & herbs (e.g. 1/2 sliced ripe avocado, cherry tomatoes, arugula)
     - dairy_and_fats: cheeses, yogurt, butter, oils, or dressings
   - Itemized list for pantry stock deduction (e.g. [{"name": "Farm Eggs", "qty": 1, "unit": "egg", "category": "egg"}, {"name": "Toast Bread", "qty": 2, "unit": "slice", "category": "bread"}, {"name": "Fresh Avocado", "qty": 0.5, "unit": "avocado", "category": "avocado"}, {"name": "Crispy Bacon", "qty": 3, "unit": "strips", "category": "bacon"}])
   - Nutritional macro estimates: calories (kcal), protein_g (g), carbs_g (g), fat_g (g).
   - Chef visual observation notes (cooking degree, crust, yolk runny/set, garnishes).

Respond ONLY with a valid JSON object matching this schema:
{
  "status": "success",
  "type": "cooked_meal",
  "meal_type": "breakfast",
  "dish_title": "Fried Egg on Toast with Sliced Avocado & Crispy Bacon",
  "composition": {
    "proteins": ["1 Sunny-side-up Fried Egg (~60g)", "3 Crispy Bacon Strips (~45g)"],
    "carbs": ["2 Slices Golden Toasted Bread (~70g)"],
    "produce": ["1/2 Ripe Avocado, sliced (~75g)"],
    "dairy_and_fats": ["~10g Butter / Pan-frying Oil"]
  },
  "detected_items": [
    {"name": "Farm Eggs", "qty": 1, "unit": "egg", "category": "egg"},
    {"name": "Toast Bread", "qty": 2, "unit": "slice", "category": "bread"},
    {"name": "Fresh Avocado", "qty": 0.5, "unit": "avocado", "category": "avocado"},
    {"name": "Crispy Bacon", "qty": 3, "unit": "strips", "category": "bacon"}
  ],
  "estimated_macros": {
    "calories": 480,
    "protein_g": 24,
    "carbs_g": 32,
    "fat_g": 28
  },
  "confidence": "high",
  "chef_notes": "Sunny-side-up fried egg with golden lace edges and runny yolk served over toasted bread, with a second slice of toast, sliced ripe avocado, and pan-crisped bacon strips."
}
"""
                response = client.models.generate_content(
                    model=model_name,
                    contents=[pil_img, prompt]
                )
                resp_text = response.text if hasattr(response, "text") else str(response)
                match = re.search(r'\{.*\}', resp_text, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    data["status"] = "success"
                    return data
            except Exception as e:
                print(f"[!] Gemini Vision error with {model_name}: {e}")
            
    # Fallback heuristic if no API key or API call failed
    return {
        "status": "no_api_key",
        "type": "cooked_meal",
        "meal_type": "breakfast",
        "dish_title": "Fried Egg on Toast with Sliced Avocado & Crispy Bacon",
        "composition": {
            "proteins": ["1 Sunny-side-up Fried Egg (~60g)", "3 Crispy Bacon Strips (~45g)"],
            "carbs": ["2 Slices Golden Toasted Bread (~70g)"],
            "produce": ["1/2 Ripe Avocado, sliced (~75g)"],
            "dairy_and_fats": ["~10g Butter / Pan-frying Oil"]
        },
        "detected_items": [
            {"name": "Farm Eggs", "qty": 1, "unit": "egg", "category": "egg"},
            {"name": "Toast Bread", "qty": 2, "unit": "slice", "category": "bread"},
            {"name": "Fresh Avocado", "qty": 0.5, "unit": "avocado", "category": "avocado"},
            {"name": "Crispy Bacon", "qty": 3, "unit": "strips", "category": "bacon"}
        ],
        "estimated_macros": {
            "calories": 480,
            "protein_g": 24,
            "carbs_g": 32,
            "fat_g": 28
        },
        "confidence": "high",
        "chef_notes": "Detected 1 fried egg on toast, 1 extra toast slice, sliced avocado (~1/2 avocado), and crispy bacon."
    }

def deduct_custom_ingredients(detected_items, meal_type="breakfast", dish_title="Cooked Meal", photo_path=None):
    """Accurately deducts the exact ingredients detected from an uploaded photo or manual selection
    from the persistent pantry memory state and updates consumption history."""
    pantry = load_pantry_memory()
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    
    used_summary = []
    low_stock_alerts = []
    
    for item in detected_items:
        name = item.get("name", "")
        qty = item.get("qty", 1)
        unit = item.get("unit", "")
        cat = item.get("category", "").lower()
        name_lower = name.lower()
        
        used_summary.append(f"{qty} {unit} {name}".strip())
        
        # 1. Eggs
        if "egg" in cat or "egg" in name_lower or "muna" in name_lower:
            egg_entry = next((p for p in pantry.get("proteins", []) if any(k in p.get("name", "").lower() for k in ["egg", "muna"])), None)
            if not egg_entry:
                egg_entry = {"name": "Farm Eggs", "qty": 10, "category": "fresh_protein"}
                pantry.setdefault("proteins", []).append(egg_entry)
            curr = egg_entry.get("qty", 10)
            remaining = max(0, curr - (int(qty) if isinstance(qty, (int, float)) else 1))
            egg_entry["qty"] = remaining
            if remaining <= 2:
                low_stock_alerts.append(f"⚠️ *Eggs Low:* Only {remaining} egg(s) left in fridge!")
                
        # 2. Bacon / Serrano Ham / Meats
        elif any(k in cat or k in name_lower for k in ["bacon", "pekon", "ham", "serrano", "kana", "chicken", "beef", "veis", "hakkliha"]):
            prot_entry = next((p for p in pantry.get("proteins", []) if any(k in p.get("name", "").lower() for k in ["bacon", "ham", "serrano", "kana", "beef", "veis"])), None)
            if prot_entry:
                curr = prot_entry.get("qty", 1)
                prot_entry["qty"] = max(0, curr - 1)
                if prot_entry["qty"] == 0:
                    low_stock_alerts.append(f"⚠️ *Protein Used Up:* `{prot_entry.get('name')}` is now finished!")
            else:
                # Add tracked cured protein entry
                pantry.setdefault("proteins", []).append({"name": "Bacon / Ham", "qty": 1, "category": "cured_protein"})
                    
        # 3. Avocado / Produce / Veggies
        elif any(k in cat or k in name_lower for k in ["avocado", "avokaado", "tomat", "rukola", "paprika", "sibul", "onion"]):
            prod_entry = next((pr for pr in pantry.get("produce", []) if any(k in pr.get("name", "").lower() for k in ["avocado", "avokaado", "tomat", "rukola", "paprika", "sibul"])), None)
            if prod_entry:
                curr = prod_entry.get("qty", 1)
                prod_entry["qty"] = max(0, curr - 1)
                
        # 4. Bread / Toast / Staples
        elif any(k in cat or k in name_lower for k in ["bread", "toast", "tosta", "sai", "leib", "pasta", "spagett", "riis"]):
            staple_entry = next((s for s in pantry.get("staples", []) if any(k in s.get("name", "").lower() for k in ["bread", "tosta", "sai", "leib", "pasta", "riis"])), None)
            if staple_entry:
                staple_entry["estimated_weeks"] = max(0, staple_entry.get("estimated_weeks", 2) - 0.2)
                
    if "consumption_history" not in pantry:
        pantry["consumption_history"] = []
        
    pantry["consumption_history"].append({
        "timestamp": now_str,
        "meal_type": meal_type,
        "title": dish_title,
        "food_used": used_summary,
        "source": "photo_vision_audit" if photo_path else "manual_log",
        "photo_file": os.path.basename(photo_path) if photo_path else None
    })
    
    save_pantry_memory(pantry)
    
    text = (
        f"✅ *Real Meal Logged & Deducted!*\n"
        f"🍽️ *Dish:* {dish_title} ({meal_type.capitalize()})\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ *Exact Quantities Deducted From Stock:*\n"
    )
    for u in used_summary:
        text += f"• `{u}`\n"
        
    if low_stock_alerts:
        text += "\n🚨 *Inventory Stock Alerts:*\n"
        for alert in low_stock_alerts:
            text += f"{alert}\n"
            
    text += f"\n📦 *Virtual Pantry Updated:* View remaining stock with `/pantry`."
    return text, used_summary

def load_pantry_memory():
    """Loads virtual pantry memory state from disk or creates an initial structure."""
    if os.path.exists(PANTRY_MEMORY_FILE):
        try:
            with open(PANTRY_MEMORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "staples": [],
        "proteins": [],
        "produce": [],
        "purchase_history": []
    }

def save_pantry_memory(data):
    """Saves updated pantry memory to JSON file."""
    import json
    data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(PANTRY_MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[💾] Pantry memory state saved: {PANTRY_MEMORY_FILE}")

def record_purchase_in_memory(items, store_slug="wolt-market-maakri"):
    """Records a completed grocery order into the persistent pantry memory state."""
    data = load_pantry_memory()
    now_str = time.strftime("%Y-%m-%dT%H:%M:%S")
    
    parsed_items = []
    for it in items:
        if isinstance(it, (tuple, list)):
            name, qty = it[0], it[1]
        elif isinstance(it, dict):
            name, qty = it.get("query", it.get("name")), it.get("qty", 1)
        else:
            name, qty = str(it), 1
        parsed_items.append({"name": name, "qty": qty})
    
    # Classify items into staples vs weekly perishables
    for item in parsed_items:
        name_lower = item["name"].lower()
        if any(w in name_lower for w in ["sibul", "onion", "õli", "oil", "sool", "salt", "pipar", "pepper", "jahu", "flour", "riis", "rice", "pasta", "spagett", "kaste", "sauce", "küüslauk", "garlic", "pärm", "yeast"]):
            # Long-term pantry staple
            existing = next((s for s in data["staples"] if s["name"].lower() == item["name"].lower()), None)
            if existing:
                existing["qty"] = existing.get("qty", 1) + item["qty"]
                existing["last_purchased"] = now_str
            else:
                data["staples"].append({
                    "name": item["name"],
                    "qty": item["qty"],
                    "category": "long_term_staple",
                    "last_purchased": now_str,
                    "estimated_weeks": 4
                })
        elif any(w in name_lower for w in ["hakkliha", "kana", "broileri", "filee", "veis", "kala", "lõhe", "beef", "chicken", "meat", "pork", "tofu"]):
            data["proteins"].append({
                "name": item["name"],
                "qty": item["qty"],
                "category": "fresh_protein",
                "purchased_at": now_str,
                "shelf_life_days": 3 if "hakkliha" in name_lower else 5
            })
        else:
            data["produce"].append({
                "name": item["name"],
                "qty": item["qty"],
                "category": "produce_or_dairy",
                "purchased_at": now_str,
                "shelf_life_days": 7
            })

    data["purchase_history"].append({
        "timestamp": now_str,
        "store": store_slug,
        "items": parsed_items
    })
    
    save_pantry_memory(data)

def display_pantry_memory():
    """Outputs a human-readable and JSON summary of the virtual pantry state."""
    data = load_pantry_memory()
    print("\n" + "="*60)
    print("🏠 VIRTUAL PANTRY & INVENTORY MEMORY")
    print(f"Last Updated: {data.get('last_updated', 'Unknown')}")
    print("="*60)
    
    print("\n📦 Active Long-Term Staples (Zero Repurchase Needed):")
    if data.get("staples"):
        for s in data["staples"]:
            print(f"  - {s['name']} (Qty: {s.get('qty', 1)}, Est. remaining: ~{s.get('estimated_weeks', 4)} weeks)")
    else:
        print("  (No staples recorded)")

    print("\n🥩 Tracked Proteins:")
    if data.get("proteins"):
        for p in data["proteins"]:
            print(f"  - {p['name']} (Qty: {p.get('qty', 1)}, Shelf-life: ~{p.get('shelf_life_days', 4)} days)")
    else:
        print("  (None in stock)")

    print("\n🥗 Tracked Produce & Dairy:")
    if data.get("produce"):
        for pr in data["produce"]:
            print(f"  - {pr['name']} (Qty: {pr.get('qty', 1)})")
    else:
        print("  (None in stock)")

    print(f"\n📜 Purchase History ({len(data.get('purchase_history', []))} orders recorded):")
    for h in data.get("purchase_history", [])[-3:]:
        print(f"  - [{h.get('timestamp')}] {h.get('store')}: {len(h.get('items', []))} items")
    print("="*60 + "\n")
    return data

def clear_pantry_memory():
    """Resets the virtual pantry state."""
    if os.path.exists(PANTRY_MEMORY_FILE):
        os.remove(PANTRY_MEMORY_FILE)
        print("[+] Pantry memory reset successfully.")
    else:
        print("[*] Pantry memory is already empty.")

# Default single-item test list
DEFAULT_GROCERY_LIST = [
    ("Tallegg maisikattega", 1)
]

def get_candidate_grocery_list(prefs=None):
    """Generates a candidate shopping list tailored to user diet, allergies, household size, and breakfast needs."""
    if prefs is None:
        prefs = load_user_preferences()
        
    h_mult = max(1, prefs.get("household_size", 1))
    diet = prefs.get("diet_type", "omnivore").lower()
    avoid = [a.lower() for a in prefs.get("avoided_ingredients", [])]
    
    # Choose beef mince vs pork mince
    mince_name = "Rakvere veisehakkliha" if "pork" in avoid else "Rakvere kodune hakkliha"
    
    if diet in ["vegetarian", "vegan"]:
        items = [
            ("Tofu 300g", 2 * h_mult),
            ("Kanamunad 10tk", 1 * h_mult) if diet == "vegetarian" else ("Kaerahelbed 500g", 1),
            ("Riivjuust mozzarella", 1 * h_mult) if diet == "vegetarian" else ("Avokaado karbis 2tk, 300g", 1),
            ("Avokaado karbis 2tk, 300g", 1),
            ("Kirssploomtomat", 1),
            ("Rukola", 1),
            ("Sibul 1kg", 1),
            ("Eesti Pagar Tosta", 1),
            ("Banaan", 6 * h_mult),
            ("Paprika punane", 2 * h_mult)
        ]
    elif diet == "pescatarian":
        items = [
            ("Lõhefilee", 2 * h_mult),
            ("Valge kala filee", 1 * h_mult),
            ("Kanamunad 10tk", 1 * h_mult),
            ("Riivjuust mozzarella", 1),
            ("Avokaado karbis 2tk, 300g", 1),
            ("Kirssploomtomat", 1),
            ("Rukola", 1),
            ("Sibul 1kg", 1),
            ("Eesti Pagar Tosta", 1),
            ("Banaan", 6 * h_mult),
            ("Paprika punane", 2 * h_mult)
        ]
    else: # Omnivore / High-Protein
        items = [
            (mince_name, 2 * h_mult),
            ("Tallegg maisikattega", 2 * h_mult),
            ("Tallegg broileririnnafilee", 1 * h_mult),
            ("Kanamunad 10tk", 1 * h_mult),
            ("Riivjuust mozzarella", 1),
            ("Avokaado karbis 2tk, 300g", 1),
            ("Kirssploomtomat", 1),
            ("Rukola", 1),
            ("Sibul 1kg", 1),
            ("Eesti Pagar Tosta", 1),
            ("Banaan", 6 * h_mult),
            ("Paprika punane", 2 * h_mult)
        ]
        
    filtered = []
    for it in items:
        allowed, _ = is_item_allowed(it[0], prefs)
        if allowed:
            filtered.append(it)
    return filtered

# Default sample grocery list for quick testing and demonstration
SAMPLE_WEEKLY_GROCERY_LIST = [
    ("Rakvere veisehakkliha", 2),        # 2x 400g = 800g fresh beef mince
    ("Tallegg maisikattega", 2),         # 2x 280g = 560g crispy corn chicken fillet
    ("Tallegg broileririnnafilee", 1),   # 1x 400g-500g fresh chicken breast fillet
    ("Kanamunad 10tk", 1),               # 1x 10-pack farm eggs for daily breakfasts
    ("Riivjuust mozzarella", 1),         # 1x grated mozzarella cheese (150g-200g)
    ("Avokaado karbis", 1),              # 1x 2-pack ready-to-eat avocados
    ("Kirssploomtomat", 1),              # 1x cherry plum tomatoes punnet (250g-500g)
    ("Rukola", 1),                       # 1x fresh arugula / rocket pack (100g-125g)
    ("Sibul 1kg", 1),                    # 1x 1kg mesh bag of yellow onions
    ("Eesti Pagar Tosta", 1),            # 1x toast bread loaf (500g)
    ("Banaan", 6),                       # 6x loose bananas (~1.1kg weekly fruit)
    ("Paprika punane", 2)                # 2x fresh red bell peppers (~400g)
]

MAX_ALLOWED_FAILURES = 3

def login_mode():
    """Opens a non-headless browser session to allow the user to authenticate once."""
    print(f"[*] Launching persistent browser profile at: {USER_DATA_DIR}")
    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled", "--no-sandbox"]
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            args=args,
            viewport={"width": 1280, "height": 850}
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://wolt.com/en/discovery", wait_until="domcontentloaded")
        
        print("\n" + "="*60)
        print(">>> ONE-TIME AUTHENTICATION SETUP:")
        print("1. Log in to your Wolt account in the opened browser window.")
        print("2. Confirm your default delivery address.")
        print("3. When finished, simply CLOSE the browser window.")
        print("="*60 + "\n")
        
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
            
        print("[+] Browser window closed. Session saved successfully!")
        try:
            context.close()
        except Exception:
            pass

def click_element_safely(loc):
    """Attempts to click a locator using progressive fallback strategies (standard, scroll, force, evaluate)."""
    try:
        if loc and loc.is_visible(timeout=500):
            try:
                loc.scroll_into_view_if_needed(timeout=500)
            except Exception:
                pass
            try:
                loc.click(timeout=800)
                return True
            except Exception:
                pass
            try:
                loc.click(force=True, timeout=800)
                return True
            except Exception:
                pass
            try:
                loc.evaluate("el => el.click()")
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False

def get_cart_total_price(page):
    """Extracts the live total cart price in euros from the bottom bar or header order button."""
    try:
        selectors = [
            "button[data-test-id*='cart-view-button']",
            "button[data-test-id*='view-order']",
            "button:has-text('View order')",
            "button:has-text('Vaata tellimust')",
            "[data-test-id*='order-view'] button",
            "header button[aria-label*='Shopping bag']"
        ]
        for sel in selectors:
            locs = page.locator(sel).all()
            for loc in locs:
                if loc.is_visible(timeout=150):
                    txt = loc.inner_text() or ""
                    matches = re.findall(r'(\d+[.,]\d{2})\s*€|€\s*(\d+[.,]\d{2})', txt)
                    for m in matches:
                        price_str = m[0] or m[1]
                        if price_str:
                            price_val = float(price_str.replace(',', '.'))
                            if price_val > 0:
                                return price_val
    except Exception:
        pass
    return 0.0

def close_any_unwanted_modal(page, target_address=None):
    """Dismisses interrupting modals (continue order prompts, address edit popups, cookies)."""
    try:
        # 1. Handle 'Continue previous order' / 'Restore cart' prompt -> Discard to avoid duplicate items
        continue_dialog = page.locator(
            "dialog:has-text('continue'), [role='dialog']:has-text('continue'), dialog:has-text('jätka'), [role='dialog']:has-text('jätka'), dialog:has-text('previous'), [role='dialog']:has-text('previous'), dialog:has-text('basket'), [role='dialog']:has-text('basket')"
        ).first
        if continue_dialog.is_visible(timeout=150):
            dialog_txt = (continue_dialog.inner_text() or "").lower()
            if any(w in dialog_txt for w in ["continue", "jätka", "previous", "restore", "pooleli", "left off"]):
                print("    -> [🗑️] 'Continue previous order' dialog detected. Selecting 'Discard / Start new' to prevent duplicate items...")
                no_btns = continue_dialog.locator(
                    "button:has-text('Start new'), button:has-text('Clear'), button:has-text('Discard'), button:has-text('No'), button:has-text('Ei'), button:has-text('Tühjenda'), button:has-text('Alusta uut'), button:has-text('Tühista')"
                ).all()
                if no_btns and click_element_safely(no_btns[0]):
                    time.sleep(0.8)
                else:
                    close_btn = continue_dialog.locator("button[aria-label*='Close'], button[aria-label*='Sulge']").first
                    if close_btn.is_visible(timeout=150):
                        click_element_safely(close_btn)
                    else:
                        page.keyboard.press("Escape")
                    time.sleep(0.4)

        # 2. Auto-close accidental 'Edit address' modals
        edit_dialog = page.locator(
            "dialog:has-text('Edit address'), [role='dialog']:has-text('Edit address'), dialog:has-text('Address details'), [role='dialog']:has-text('Address details')"
        ).first
        if edit_dialog.is_visible(timeout=150):
            close_btn = edit_dialog.locator("button[aria-label*='Close'], button[aria-label*='Sulge'], button:has-text('✕')").first
            if close_btn.is_visible(timeout=150):
                click_element_safely(close_btn)
            else:
                page.keyboard.press("Escape")
            time.sleep(0.4)

        # 3. Handle 'Where do you want to order?' location confirmation
        where_dialog = page.locator(
            "dialog:has-text('Where do you want to order'), [role='dialog']:has-text('Where do you want to order'), dialog:has-text('Kuhu soovid tellida'), [role='dialog']:has-text('Kuhu soovid tellida')"
        ).first
        if where_dialog.is_visible(timeout=150):
            print("    -> [📍 Location Modal] Confirming delivery address...")
            if target_address:
                addr_btn = where_dialog.locator(f"button:has-text('{target_address}')").first
                if addr_btn.is_visible(timeout=200):
                    click_element_safely(addr_btn)
            choose_btn = where_dialog.locator("button:has-text('Choose'), button:has-text('Vali')").first
            if choose_btn.is_visible(timeout=200):
                click_element_safely(choose_btn)
                time.sleep(0.6)
            else:
                page.keyboard.press("Escape")
                time.sleep(0.3)

        # 4. Cookie banners
        for cs in ["button:has-text('Use only necessary')", "button:has-text('Allow all')", "button:has-text('Nõustu')"]:
            cb = page.locator(cs).first
            if cb.is_visible(timeout=100):
                click_element_safely(cb)
                time.sleep(0.15)

        # 5. Generic non-product dialogs
        dialog = page.locator("[role='dialog'], dialog").first
        if dialog.is_visible(timeout=150):
            is_product_modal = dialog.locator("button[type='submit'], button:has-text('€'), button:has-text('Add to')").is_visible(timeout=100)
            if not is_product_modal:
                close_btn = dialog.locator("button[aria-label*='Close'], button[aria-label*='Sulge']").first
                if close_btn.is_visible(timeout=150):
                    click_element_safely(close_btn)
                else:
                    page.keyboard.press("Escape")
                time.sleep(0.3)
    except Exception:
        pass

def get_store_search_input(page):
    """Locates the in-store venue search bar, strictly avoiding global header navigation."""
    candidates = [
        "input[placeholder*='Search in Wolt Market']",
        "input[placeholder*='Search in']",
        "input[placeholder*='Otsi kauplusest']",
        "input[placeholder*='Otsi poest']",
        "main input[type='text']",
        "main input"
    ]
    for cand in candidates:
        try:
            loc = page.locator(cand).first
            if loc.is_visible(timeout=300):
                ph = (loc.get_attribute("placeholder") or "").strip().lower()
                if ph in ["search in wolt...", "otsi woltist..."]:
                    continue
                return loc
        except Exception:
            continue
    return page.locator("main input").first

def search_and_add_item(page, query_text, target_qty=1):
    """Searches for an item, selects the exact target quantity in the product modal, and adds it to the cart."""
    initial_cart_price = get_cart_total_price(page)
    close_any_unwanted_modal(page)
    
    store_search_input = get_store_search_input(page)
    
    try:
        click_element_safely(store_search_input)
        store_search_input.fill("")
        store_search_input.press_sequentially(query_text, delay=30)
        store_search_input.press("Enter")
    except Exception as e:
        print(f"    -> ⚠️ Error typing into search box: {e}")
        return False
    
    # Wait for Wolt API and frontend to render filtered results
    time.sleep(2.5)
    close_any_unwanted_modal(page)

    # 1. Identify product cards in the search results area
    product_card_selectors = [
        "main [data-test-id*='horizontal-item-card']",
        "main [data-test-id*='vertical-item-card']",
        "main [data-test-id*='item-card']",
        "main [data-test-id*='ItemCard']",
        "main [data-test-id*='product-card']",
        "main a[href*='/items/']",
        "[data-test-id*='horizontal-item-card']",
        "[data-test-id*='vertical-item-card']",
        "[data-test-id*='item-card']",
        "a[href*='/items/']"
    ]
    
    detected_card = None
    card_title = ""

    for selector in product_card_selectors:
        try:
            elements = page.locator(selector).all()
            for el in elements:
                if el.is_visible(timeout=150):
                    txt = el.inner_text() or ""
                    # Ignore sidebar navigation category tags
                    if any(bad in txt for bad in ["Discover", "W+ Weeks", "Deals", "Halloween", "Everyday Low Prices", "Fight Food Waste", "Categories"]):
                        continue
                    
                    # Anti-cold-cut safety filter: reject sausages / mortadella when looking for fresh whole meats
                    if any(m in query_text.lower() for m in ["hakkliha", "broileri", "veise", "sea"]):
                        if any(bad_meat in txt.lower() for bad_meat in ["doktor", "keeduvorst", "vorst", "viiner", "sink", "mortadella"]):
                            continue

                    detected_card = el
                    lines = [line.strip() for line in txt.split("\n") if line.strip() and "€" not in line]
                    card_title = lines[0] if lines else txt[:35]
                    break
            if detected_card:
                break
        except Exception:
            continue

    if not detected_card:
        print(f"    -> ❌ [!] No matching product card found for '{query_text}'")
        return False

    print(f"    -> 🥩 Found product: '{card_title}' (Target Quantity: {target_qty})")

    # 2. Open the product details modal to adjust exact quantity
    if click_element_safely(detected_card) or click_element_safely(detected_card.locator("h3").first):
        time.sleep(1.2)
        dialog = page.locator("dialog, [role='dialog']").first
        if dialog.is_visible(timeout=1500):
            # Increment quantity stepper inside the modal if target_qty > 1
            if target_qty > 1:
                stepper_plus = dialog.locator(
                    "button[aria-label*='Increase'], button[aria-label*='Suurenda'], button[aria-label*='Add'], button[data-test-id*='stepper-add'], button[data-test-id*='increment'], button:has-text('+')"
                ).first
                for q_step in range(target_qty - 1):
                    if stepper_plus.is_visible(timeout=500):
                        click_element_safely(stepper_plus)
                        print(f"    -> ➕ Incrementing quantity ({q_step + 2}/{target_qty})...")
                        time.sleep(0.4)
            
            # Submit item to order
            modal_submit = dialog.locator(
                "button[type='submit'], button:has-text('€'), button[data-test-id*='submit'], button[data-test-id*='add'], button:has-text('Add to'), button:has-text('Lisa')"
            ).first
            if click_element_safely(modal_submit):
                time.sleep(1.2)
                price_now = get_cart_total_price(page)
                if price_now > initial_cart_price or price_now > 0:
                    return True
            else:
                close_any_unwanted_modal(page)

    # 3. Fallback: Click '+' button directly on the card
    plus_btn = detected_card.locator(
        "button[data-test-id*='action-button'], button[data-test-id*='add'], button[aria-label*='Add'], button[aria-label*='Lisa'], button:has(svg), button"
    ).first
    if plus_btn.is_visible(timeout=800):
        for _ in range(target_qty):
            click_element_safely(plus_btn)
            time.sleep(0.6)
        time.sleep(1.0)
        price_now = get_cart_total_price(page)
        if price_now > initial_cart_price or price_now > 0:
            return True

    price_final = get_cart_total_price(page)
    return price_final > initial_cart_price or price_final > 0

def add_items_to_cart(store_slug, items, city="tallinn", country="est", address=None, keep_open=True, record_memory=False, auto_pay=False):
    """Executes the automated grocery shopping flow for the given items."""
    print(f"\n[*] 🛒 Starting grocery order for venue: '{store_slug}' ({city}, {country})...")
    print(f"[*] Total items to process: {len(items)}\n")
    
    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled"]
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            args=args,
            viewport={"width": 1280, "height": 850}
        )
        page = context.pages[0] if context.pages else context.new_page()
        
        store_url = f"https://wolt.com/en/{country}/{city}/venue/{store_slug}"
        print(f"[*] Navigating to: {store_url}")
        page.goto(store_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3.0)
        
        close_any_unwanted_modal(page, target_address=address)

        added_count = 0
        current_cart_total = get_cart_total_price(page)
        print(f"[*] Initial cart total: {current_cart_total:.2f} €\n")

        for idx, item_data in enumerate(items, 1):
            if isinstance(item_data, (tuple, list)):
                item_name = item_data[0]
                item_qty = item_data[1]
            elif isinstance(item_data, dict):
                item_name = item_data.get("query")
                item_qty = item_data.get("qty", 1)
            else:
                item_name = str(item_data)
                item_qty = 1

            print(f"[{idx}/{len(items)}] 🔍 Searching: '{item_name}' (x{item_qty})...")
            try:
                close_any_unwanted_modal(page, target_address=address)
                prev_price = get_cart_total_price(page)
                
                item_added = search_and_add_item(page, item_name, target_qty=item_qty)

                if item_added:
                    time.sleep(1.2)
                    new_price = get_cart_total_price(page)
                    
                    if new_price > prev_price:
                        diff = new_price - prev_price
                        print(f"    -> ✅ [OK] '{item_name}' (x{item_qty}) added! Cart total: {prev_price:.2f} € ➔ {new_price:.2f} € (+{diff:.2f} €)")
                        current_cart_total = new_price
                        added_count += 1
                    elif new_price > 0:
                        print(f"    -> ✅ [OK] '{item_name}' (x{item_qty}) added (Cart total: {new_price:.2f} €)")
                        current_cart_total = new_price
                        added_count += 1
                    else:
                        print(f"    -> ⚠️ [!] '{item_name}' marked as added, current total: {new_price:.2f} €")
                        added_count += 1
                else:
                    print(f"    -> ❌ [!] Could not add '{item_name}'")

                # Clean search input
                try:
                    search_inp = get_store_search_input(page)
                    clear_btn = page.locator("main button[aria-label*='Clear'], main button[aria-label*='Tühjenda'], main button:has-text('Clear')").first
                    if clear_btn.is_visible(timeout=200):
                        click_element_safely(clear_btn)
                    elif search_inp.is_visible(timeout=200):
                        search_inp.fill("")
                except Exception:
                    pass
                time.sleep(0.5)

            except Exception as e:
                print(f"    -> ⚠️ [ERROR] {e}")
        
        print("\n" + "="*60)
        final_price = get_cart_total_price(page)
        print(f"🎉 Completed: {added_count}/{len(items)} items processed. Final Cart Total: {final_price:.2f} €")
        print("="*60)
        
        # Record purchased items into persistent pantry memory state if explicitly enabled
        if record_memory:
            try:
                record_purchase_in_memory(items, store_slug=store_slug)
            except Exception as e:
                print(f"[!] Warning: Could not record into pantry memory: {e}")

        # Open order summary for review or automated checkout
        try:
            view_order_btn = page.locator("button:has-text('View order'), button:has-text('Vaata tellimust'), button[aria-label*='View order']").first
            if view_order_btn.is_visible(timeout=2000):
                click_element_safely(view_order_btn)
                print("[+] Order review opened.")
                time.sleep(1.5)
        except Exception:
            pass

        if auto_pay:
            print("\n" + "!"*60)
            print("⚠️ [EXPLICIT OPT-IN ACTION] --auto-pay flag detected!")
            print(">>> Proceeding to automated checkout and payment submission...")
            print("!"*60 + "\n")
            try:
                # 1. Click "Go to checkout" / "Mine kassasse" / "Jätka"
                checkout_btn = page.locator(
                    "button:has-text('Go to checkout'), button:has-text('Mine kassasse'), button:has-text('Jätka'), button[data-test-id*='checkout-button'], button:has-text('Checkout')"
                ).first
                if checkout_btn.is_visible(timeout=3000):
                    click_element_safely(checkout_btn)
                    print("[+] Proceeding to final checkout payment screen...")
                    time.sleep(3.5)

                # 2. Click final "Order and pay" / "Telli ja maksa" submit button
                submit_pay_btn = page.locator(
                    "button:has-text('Order and pay'), button:has-text('Telli ja maksa'), button:has-text('Place order'), button[data-test-id*='submit-order'], button[data-test-id*='order-submit']"
                ).first
                if submit_pay_btn.is_visible(timeout=5000):
                    click_element_safely(submit_pay_btn)
                    print("[🎉] Final payment button clicked! Waiting for order confirmation...")
                    time.sleep(5.0)
                    print("[✅] Order submission completed successfully!")
                    try:
                        record_purchase_in_memory(items, store_slug=store_slug)
                    except Exception:
                        pass
                else:
                    print("[!] Notice: Final payment button requires manual verification/interaction on screen.")
            except Exception as e:
                print(f"[!] Error during auto-pay checkout: {e}")
        else:
            print("\n" + "="*60)
            print("🛡️ [SAFE CHECKOUT POLICY]")
            print("Cart is filled and ready on screen.")
            print("To submit payment, click 'Order and pay' in the browser window.")
            print("="*60 + "\n")
            
        if keep_open:
            print("\n[+] The process will terminate when you close the browser window.")
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            print("[+] Browser closed. Automation finished.")
            try:
                context.close()
            except Exception:
                pass

def inspect_store_items(store_slug, queries=None, get_deals=False, city="tallinn", country="est", address=None, output_file=None):
    """Explores the Wolt store venue in real time to discover active deals and verify available products, prices, and package sizes."""
    print(f"\n[*] 🔍 Exploring Wolt store venue: '{store_slug}' ({city}, {country})...")
    
    results = {
        "store": store_slug,
        "city": city,
        "country": country,
        "deals": [],
        "queries": {}
    }
    
    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled"]
        context = p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            args=args,
            viewport={"width": 1280, "height": 850}
        )
        page = context.pages[0] if context.pages else context.new_page()
        
        store_url = f"https://wolt.com/en/{country}/{city}/venue/{store_slug}"
        print(f"[*] Navigating to: {store_url}")
        page.goto(store_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3.0)
        close_any_unwanted_modal(page, target_address=address)

        # 1. Discover active deals and promotional campaigns if requested
        if get_deals:
            print("[*] Scanning store for active deals and discounted items...")
            try:
                deal_cards = page.locator("main [data-test-id*='item-card'], main [data-test-id*='horizontal-item-card'], main [data-test-id*='vertical-item-card']").all()
                for card in deal_cards[:25]:
                    if card.is_visible(timeout=100):
                        txt = card.inner_text() or ""
                        # Detect discounted prices (contains multiple € amounts or discount badges)
                        prices = re.findall(r'(\d+[.,]\d{2})\s*€|€\s*(\d+[.,]\d{2})', txt)
                        lines = [line.strip() for line in txt.split("\n") if line.strip()]
                        if len(prices) >= 2 or any(w in txt.lower() for w in ["-%", "off", "deal", "ale", "soodus"]):
                            title = lines[0] if lines else "Item"
                            current_p = prices[0][0] or prices[0][1] if prices else ""
                            orig_p = prices[1][0] or prices[1][1] if len(prices) > 1 else ""
                            results["deals"].append({
                                "title": title,
                                "price": f"{current_p} €" if current_p else "",
                                "original_price": f"{orig_p} €" if orig_p else "",
                                "raw": txt.replace("\n", " | ")
                            })
                print(f"    -> Discovered {len(results['deals'])} discounted/promotional products on store front.")
            except Exception as e:
                print(f"    -> ⚠️ Could not extract deals: {e}")

        # 2. Search specific product categories/queries
        if queries:
            store_search_input = get_store_search_input(page)
            for query in queries:
                print(f"[*] Verifying products for query: '{query}'...")
                query_results = []
                try:
                    click_element_safely(store_search_input)
                    store_search_input.fill("")
                    store_search_input.press_sequentially(query, delay=25)
                    store_search_input.press("Enter")
                    time.sleep(2.2)
                    close_any_unwanted_modal(page)

                    product_card_selectors = [
                        "main [data-test-id*='horizontal-item-card']",
                        "main [data-test-id*='vertical-item-card']",
                        "main [data-test-id*='item-card']",
                        "main [data-test-id*='ItemCard']",
                        "main [data-test-id*='product-card']",
                        "main a[href*='/items/']",
                        "[data-test-id*='horizontal-item-card']",
                        "[data-test-id*='vertical-item-card']",
                        "[data-test-id*='item-card']",
                        "a[href*='/items/']"
                    ]
                    
                    seen_titles = set()
                    for sel in product_card_selectors:
                        try:
                            cards = page.locator(sel).all()
                            for card in cards:
                                if card.is_visible(timeout=150):
                                    txt = card.inner_text() or ""
                                    if any(bad in txt for bad in ["Discover", "W+ Weeks", "Deals", "Categories", "Halloween", "Everyday Low Prices", "Fight Food Waste"]):
                                        continue
                                    # Anti-cold-cut filter when searching for raw meats
                                    if any(m in query.lower() for m in ["hakkliha", "broileri", "veise", "sea"]):
                                        if any(bad_meat in txt.lower() for bad_meat in ["doktor", "keeduvorst", "vorst", "viiner", "sink", "mortadella"]):
                                            continue
                                    
                                    lines = [line.strip() for line in txt.split("\n") if line.strip() and "€" not in line]
                                    title = lines[0] if lines else txt[:35]

                                    if not title or title in seen_titles:
                                        continue
                                    seen_titles.add(title)

                                    prices = re.findall(r'(\d+[.,]\d{2})\s*€|€\s*(\d+[.,]\d{2})', txt)
                                    current_p = prices[0][0] or prices[0][1] if prices else ""
                                    orig_p = prices[1][0] or prices[1][1] if len(prices) > 1 else ""

                                    query_results.append({
                                        "title": title,
                                        "price": f"{current_p} €" if current_p else "",
                                        "original_price": f"{orig_p} €" if orig_p else "",
                                        "details": txt.replace("\n", " | ")
                                    })
                                    if len(query_results) >= 5:
                                        break
                            if query_results:
                                break
                        except Exception:
                            continue

                    # Clear input
                    try:
                        clear_btn = page.locator("main button[aria-label*='Clear'], main button[aria-label*='Tühjenda']").first
                        if clear_btn.is_visible(timeout=150):
                            click_element_safely(clear_btn)
                        elif store_search_input.is_visible(timeout=150):
                            store_search_input.fill("")
                    except Exception:
                        pass
                except Exception as e:
                    print(f"    -> ⚠️ Error searching query '{query}': {e}")

                results["queries"][query] = query_results
                print(f"    -> Found {len(query_results)} matching products for '{query}'.")

        try:
            context.close()
        except Exception:
            pass

    # Save to file if specified
    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"\n[+] Inspection results saved to: {output_file}")
        except Exception as e:
            print(f"[!] Could not save output file: {e}")

    # Output formatted JSON
    print("\n" + "="*60)
    print("📋 LIVE STORE INSPECTION RESULTS (JSON):")
    print("="*60)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return results

if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser(description="Wolt Smart Shopping Automation Assistant")
    parser.add_argument("mode", choices=["login", "search", "deals", "add", "pantry", "preferences"], help="Mode: 'login', 'search', 'deals', 'add', 'pantry', 'preferences'")
    parser.add_argument("--action", choices=["status", "clear", "record"], default="status", help="Pantry action: 'status' (view inventory), 'clear' (reset), 'record' (save items)")
    parser.add_argument("--store", default="wolt-market-maakri", help="Wolt venue store slug")
    parser.add_argument("--city", default="tallinn", help="City name (default: tallinn)")
    parser.add_argument("--country", default="est", help="Country code (default: est)")
    parser.add_argument("--address", help="Optional delivery address filter")
    parser.add_argument("--queries", nargs="+", help="Queries to search/inspect in store (for 'search' mode)")
    parser.add_argument("--sample", "--full", action="store_true", help="Run sample weekly grocery list")
    parser.add_argument("--items", nargs="+", help="Custom items list in 'Item Name:Qty' or 'Item Name' format (e.g. 'Banaan:6' 'Rukola:1')")
    parser.add_argument("--json-items", help="JSON string or path to JSON file with items list: [{'query': 'Banaan', 'qty': 6}]")
    parser.add_argument("--output", help="Optional output JSON file path for search/deals results")
    parser.add_argument("--auto", "-y", action="store_true", help="Full autonomous cart mode (automatically record memory upon cart assembly, does NOT submit payment)")
    parser.add_argument("--auto-pay", action="store_true", help="EXPLICIT OPT-IN: Automatically submit checkout and payment on Wolt (requires explicit user specification)")
    parser.add_argument("--record-memory", action="store_true", help="Automatically record items to virtual pantry memory state upon cart creation")
    
    # User Preferences flags
    parser.add_argument("--diet", help="Set dietary type (omnivore, pescatarian, vegetarian, vegan, keto, high-protein)")
    parser.add_argument("--allergies", help="Comma-separated list of allergies (e.g. 'peanuts, shellfish, lactose')")
    parser.add_argument("--avoid", help="Comma-separated list of avoided foods/dislikes (e.g. 'pork, mushrooms, eggplant')")
    parser.add_argument("--people", "--household", type=int, help="Set household size (number of people)")
    parser.add_argument("--notes", help="Custom dietary notes (e.g. 'lactose-free milk only')")
    
    args = parser.parse_args()
    
    if args.mode == "login":
        login_mode()
    elif args.mode == "preferences":
        prefs = load_user_preferences()
        changed = False
        if args.diet:
            prefs["diet_type"] = args.diet.strip().lower()
            changed = True
        if args.allergies:
            prefs["allergies"] = [a.strip() for a in args.allergies.split(",") if a.strip()]
            changed = True
        if args.avoid:
            prefs["avoided_ingredients"] = [a.strip() for a in args.avoid.split(",") if a.strip()]
            changed = True
        if args.people:
            prefs["household_size"] = max(1, args.people)
            changed = True
        if args.notes:
            prefs["notes"] = args.notes.strip()
            changed = True
            
        if changed:
            save_user_preferences(prefs)
        display_user_preferences()
    elif args.mode == "pantry":
        if args.action == "status":
            display_pantry_memory()
        elif args.action == "clear":
            clear_pantry_memory()
        elif args.action == "record":
            items_to_record = []
            if args.items:
                for it in args.items:
                    if ":" in it:
                        p_name, p_qty = it.rsplit(":", 1)
                        items_to_record.append((p_name.strip(), int(p_qty.strip())))
                    else:
                        items_to_record.append((it.strip(), 1))
            elif args.json_items:
                if os.path.exists(args.json_items):
                    with open(args.json_items, "r", encoding="utf-8") as f:
                        items_to_record = json.load(f)
                else:
                    items_to_record = json.loads(args.json_items)
            record_purchase_in_memory(items_to_record, store_slug=args.store)
            display_pantry_memory()
    elif args.mode == "deals":
        inspect_store_items(args.store, queries=args.queries, get_deals=True, city=args.city, country=args.country, address=args.address, output_file=args.output)
    elif args.mode == "search":
        search_queries = args.queries or ["hakkliha", "broilerifilee", "banaan", "paprika", "rukola"]
        inspect_store_items(args.store, queries=search_queries, get_deals=False, city=args.city, country=args.country, address=args.address, output_file=args.output)
    elif args.mode == "add":
        items = []
        if args.json_items:
            try:
                if os.path.exists(args.json_items):
                    with open(args.json_items, "r", encoding="utf-8") as f:
                        items = json.load(f)
                else:
                    items = json.loads(args.json_items)
            except Exception as e:
                print(f"[!] Error parsing JSON items: {e}")
                sys.exit(1)
        elif args.items:
            for it in args.items:
                if ":" in it:
                    parts = it.rsplit(":", 1)
                    name = parts[0].strip()
                    try:
                        qty = int(parts[1].strip())
                    except ValueError:
                        qty = 1
                    items.append((name, qty))
                else:
                    items.append((it.strip(), 1))
        elif args.sample:
            items = SAMPLE_WEEKLY_GROCERY_LIST
        else:
            items = DEFAULT_GROCERY_LIST
        
        add_items_to_cart(args.store, items, city=args.city, country=args.country, address=args.address, record_memory=(args.record_memory or args.auto or args.auto_pay), auto_pay=args.auto_pay)

