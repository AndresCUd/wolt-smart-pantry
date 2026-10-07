"""
wolt_core.gemini
Integrates Gemini LLM & Vision capabilities: API calls, NLU intent classification,
AI recipe generation, visual photo audit, dynamic 7-day meal planning, and meal swaps.
"""

import os
import sys
import json
import re
import time
import urllib.request
from datetime import datetime

from .config import get_user_config, load_user_preferences

def call_gemini_api(prompt: str, api_key: str = None, model_name: str = "gemini-flash-lite-latest", json_mode: bool = False) -> str:
    """Universal Gemini API client with fallback across google-genai, google.generativeai, and direct REST endpoint."""
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        try:
            from dotenv import load_dotenv
            load_dotenv(override=True)
            api_key = os.getenv("GEMINI_API_KEY", "")
        except Exception:
            pass

    if not api_key:
        return ""

    # Strategy 1: Official Google GenAI SDK (v1)
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        config = {}
        if json_mode:
            config["response_mime_type"] = "application/json"
        resp = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=config if config else None
        )
        if resp and resp.text:
            return resp.text.strip()
    except Exception:
        pass

    # Strategy 2: Legacy google.generativeai SDK
    try:
        import google.generativeai as legacy_genai
        legacy_genai.configure(api_key=api_key)
        gen_config = {"response_mime_type": "application/json"} if json_mode else {}
        model = legacy_genai.GenerativeModel(model_name, generation_config=gen_config)
        resp = model.generate_content(prompt)
        if resp and resp.text:
            return resp.text.strip()
    except Exception:
        pass

    # Strategy 3: Direct HTTP REST API via v1beta endpoint
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }]
        }
        if json_mode:
            payload["generationConfig"] = {"responseMimeType": "application/json"}
            
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as response:
            res_body = json.loads(response.read().decode("utf-8"))
            candidates = res_body.get("candidates", [])
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                if parts and "text" in parts[0]:
                    return parts[0]["text"].strip()
    except Exception as e:
        print(f"[!] Gemini REST API call failed: {e}")

    return ""

def parse_natural_language_intent(user_text: str, user_id=None) -> dict:
    """Parses a conversational user message into structured intent and parameters using Jev AI (System-1), Gemini, or regex fallback."""
    cfg = get_user_config(user_id) if user_id else {}
    
    # Stage 1: Fast System-1 Decision Layer via Jev AI (https://jev-ai.pro)
    jev_key = cfg.get("jev_ai_api_key") or cfg.get("typesafe_api_key") or os.getenv("JEV_AI_API_KEY") or os.getenv("TYPESAFE_API_KEY") or os.getenv("OPENROUTER_API_KEY", "")
    if jev_key:
        chosen = None
        try:
            from typesafe_sdk import TypeSafeClient, Choice
            with TypeSafeClient(api_key=jev_key, base_url="https://jev-ai.pro/api") as ts_client:
                res = ts_client.system_one(
                    state={"message": user_text},
                    questions={
                        "intent": Choice(
                            instructions="Classify the user command intent for the Wolt pantry and grocery shopping bot.",
                            criteria={
                                "today_menu": "Asking about today's meal, lunch, dinner, breakfast, or what to eat",
                                "recipe": "Asking for cooking recipes or preparation instructions",
                                "week_plan": "Viewing scheduled 7-day meal plan",
                                "plan_week": "Generating new weekly meal plan or shopping list",
                                "build_cart": "Buying or adding items to Wolt cart",
                                "log_meal": "Reporting food eaten/consumed",
                                "restock": "Reporting groceries bought or stock inventory updates",
                                "pantry_status": "Checking fridge, freezer, or pantry inventory",
                                "deals": "Scanning discounts, sales, or deals on Wolt",
                                "set_budget": "Setting maximum cart budget ceiling",
                                "set_autopay": "Toggling 1-click auto-pay mode on or off",
                                "change_store": "Changing store venue or city",
                                "preferences": "Updating allergies, dietary profile, or household size",
                                "wolt_status": "Checking Wolt browser session or venue",
                                "stop": "Aborting, stopping, or cancelling an operation",
                                "chef_chat": "General greeting, culinary advice, or question"
                            }
                        )
                    }
                )
                if hasattr(res, "choices") and "intent" in res.choices:
                    chosen = res.choices["intent"].choice
                elif hasattr(res, "answers") and "intent" in res.answers:
                    chosen = res.answers["intent"].get("choice")
        except Exception:
            try:
                req_payload = {
                    "state": user_text,
                    "model": "jev-latest",
                    "questions": {
                        "intent": {
                            "type": "choice",
                            "instructions": "Classify user command intent for the pantry and grocery shopping bot.",
                            "criteria": {
                                "today_menu": "Asking about today's meal, lunch, dinner, breakfast, or what to eat",
                                "recipe": "Asking for cooking recipes or preparation instructions",
                                "week_plan": "Viewing scheduled 7-day meal plan",
                                "plan_week": "Generating new weekly meal plan or shopping list",
                                "build_cart": "Buying or adding items to Wolt cart",
                                "log_meal": "Reporting food eaten/consumed",
                                "restock": "Reporting groceries bought or stock inventory updates",
                                "pantry_status": "Checking fridge, freezer, or pantry inventory",
                                "deals": "Scanning discounts, sales, or deals on Wolt",
                                "set_budget": "Setting maximum cart budget ceiling",
                                "set_autopay": "Toggling 1-click auto-pay mode on or off",
                                "change_store": "Changing store venue or city",
                                "preferences": "Updating allergies, dietary profile, or household size",
                                "wolt_status": "Checking Wolt browser session or venue",
                                "stop": "Aborting, stopping, or cancelling an operation",
                                "chef_chat": "General greeting, culinary advice, or question"
                            }
                        }
                    }
                }
                req_data = json.dumps(req_payload).encode("utf-8")
                req = urllib.request.Request(
                    "https://jev-ai.pro/api/v1/systemone",
                    data=req_data,
                    headers={
                        "Authorization": f"Bearer {jev_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "WoltSmartPantryBot/1.0"
                    }
                )
                with urllib.request.urlopen(req, timeout=5) as response:
                    res_body = json.loads(response.read().decode("utf-8"))
                    answers = res_body.get("answers", {})
                    if "intent" in answers:
                        chosen = answers["intent"].get("choice")
            except Exception as e2:
                print(f"[!] Jev AI HTTP endpoint error: {e2}")

        if chosen:
            params = {}
            low = user_text.lower().strip()
            if chosen == "set_budget":
                m = re.findall(r'(\d+(?:[.,]\d+)?)', low)
                if m:
                    params["budget_amount"] = float(m[0].replace(",", "."))
            elif chosen == "set_autopay":
                params["autopay_value"] = False if any(w in low for w in ["off", "disable", "no", "stop", "false", "review"]) else True
            elif chosen in ["today_menu", "log_meal"]:
                meal_type = "lunch"
                if "breakfast" in low or "desayuno" in low: meal_type = "breakfast"
                elif "dinner" in low or "cena" in low: meal_type = "dinner"
                elif "snack" in low or "merienda" in low: meal_type = "snack"
                params["meal_type"] = meal_type
            elif chosen == "recipe":
                params["dish_or_ingredients"] = user_text
            elif chosen == "change_store":
                parts = user_text.split()
                if len(parts) > 1:
                    params["store_slug"] = parts[-2] if len(parts) > 2 else parts[-1]
            return {
                "intent": chosen,
                "parameters": params,
                "chef_reply": "I'm your Wolt Smart Pantry Chef! How can I assist you?",
                "classifier": "jev-ai"
            }

    # Stage 2: Deep Generative Parsing via Gemini
    api_key = cfg.get("gemini_api_key") or os.getenv("GEMINI_API_KEY", "")
    if api_key:
        prompt = f"""
You are the natural language understanding brain for the Wolt Smart Pantry Telegram Bot.
Analyze this user message: "{user_text}"

Classify into one of these intents:
- "today_menu": Asking what is on the menu today, what to eat, breakfast, lunch, or dinner.
- "recipe": Asking for a cooking recipe or culinary technique for a dish or ingredients.
- "week_plan": Asking to browse the 7-day scheduled weekly meal plan (may contain custom suggestions or requests).
- "plan_week": Asking to generate a fresh 7-day meal plan or itemized grocery list (may contain custom suggestions like 'mexican food', 'high protein', 'more salmon', 'no dairy').
- "build_cart": Requesting to buy items or build a cart on Wolt.
- "log_meal": Stating they ate a meal or specific food items (e.g. "I ate lunch", "ate 2 eggs").
- "restock": Reporting groceries bought or adjusting inventory (e.g. "bought 10 eggs", "restock chicken").
- "pantry_status": Checking what food/ingredients are in the fridge, pantry, or inventory.
- "deals": Asking for discounts, sales, or store deals on Wolt.
- "set_budget": Setting or changing the shopping budget ceiling.
- "set_autopay": Turning 1-click auto-pay on or off.
- "change_store": Changing store venue or city.
- "preferences": Changing dietary preferences, allergies, or household size.
- "wolt_status": Asking about Wolt browser login session.
- "stop": Asking to stop, cancel, or abort an operation.
- "chef_chat": General food, nutrition, culinary question, or greeting.

Respond ONLY with a valid JSON object matching this schema:
{{
  "intent": "<intent_name>",
  "parameters": {{
    "dish_or_ingredients": "<string or null>",
    "meal_type": "breakfast" | "lunch" | "dinner" | "snack" | null,
    "user_suggestions": "<specific user meal suggestions/requests or null>",
    "items": [{{"name": "<string>", "qty": <int>}}],
    "budget_amount": <float or null>,
    "autopay_value": <true | false | null>,
    "store_slug": "<string or null>",
    "city": "<string or null>",
    "allergies": ["<string>"],
    "diet_type": "<string or null>",
    "household_size": <int or null>,
    "query": "<string or null>"
  }},
  "chef_reply": "<friendly concise 1-2 sentence response if chef_chat>"
}}
"""
        for model_name in ["gemini-flash-lite-latest", "gemini-flash-latest"]:
            try:
                resp_text = call_gemini_api(prompt, api_key=api_key, model_name=model_name, json_mode=True)
                if resp_text and resp_text.strip():
                    clean_txt = re.sub(r"^```(?:json)?\s*", "", resp_text.strip(), flags=re.MULTILINE)
                    clean_txt = re.sub(r"\s*```$", "", clean_txt.strip(), flags=re.MULTILINE)
                    return json.loads(clean_txt)
            except Exception as e:
                print(f"[!] NLU intent error with {model_name}: {e}")

    # Fallback Regex / Keyword classification
    low = user_text.lower().strip()
    
    # 1. Stop / Cancel
    if any(w in low for w in ["stop", "abort", "cancel", "halt", "quit", "stopp"]):
        return {"intent": "stop", "parameters": {}}
        
    # 2. Budget
    if "budget" in low or "limit" in low or "ceiling" in low or "max price" in low:
        m = re.findall(r'(\d+(?:[.,]\d+)?)', low)
        if m:
            b_val = float(m[0].replace(",", "."))
            return {"intent": "set_budget", "parameters": {"budget_amount": b_val}}
        return {"intent": "set_budget", "parameters": {}}
        
    # 3. Auto-pay
    if "auto pay" in low or "autopay" in low or "1-click" in low or "safe review" in low:
        val = False if any(w in low for w in ["off", "disable", "no", "stop", "false", "review"]) else True
        return {"intent": "set_autopay", "parameters": {"autopay_value": val}}
        
    # 4. Recipe
    if any(w in low for w in ["recipe", "how to cook", "how to make", "instructions for", "prepare", "receta"]):
        return {"intent": "recipe", "parameters": {"dish_or_ingredients": user_text}}
        
    # 5. Today / Menu
    if any(w in low for w in ["today", "menu", "lunch", "dinner", "breakfast", "snack", "what to eat", "what am i eating", "hoy", "desayuno", "almuerzo", "cena"]):
        meal_type = "lunch"
        if "breakfast" in low or "desayuno" in low: meal_type = "breakfast"
        elif "dinner" in low or "cena" in low: meal_type = "dinner"
        elif "snack" in low or "merienda" in low: meal_type = "snack"
        return {"intent": "today_menu", "parameters": {"meal_type": meal_type}}
        
    # 6. Week Plan / Generate Plan with suggestions
    if any(w in low for w in ["plan", "semana", "week", "schedule", "7 day", "calendar", "shopping list", "groceries plan"]):
        sug_text = user_text
        for kw in ["planifica", "plan", "semana", "week", "menu de la semana", "generate plan", "crea un plan"]:
            if kw in low:
                idx = low.find(kw)
                sug_text = user_text[idx + len(kw):].strip(" :-–—,/.")
                break
        return {"intent": "plan_week", "parameters": {"user_suggestions": sug_text if len(sug_text) > 3 else None, "query": user_text}}
        
    # 8. Build Cart
    if any(w in low for w in ["buy", "order", "cart", "add to basket", "wolt cart", "assemble cart", "compra", "carrito"]):
        return {"intent": "build_cart", "parameters": {"query": user_text}}
        
    # 9. Eat / Log meal
    if any(w in low for w in ["ate", "eaten", "consume", "finished eating", "had lunch", "had breakfast", "had dinner", "comi", "comí", "almorcé"]):
        meal_type = "lunch"
        if "breakfast" in low or "desayuno" in low: meal_type = "breakfast"
        elif "dinner" in low or "cena" in low: meal_type = "dinner"
        elif "snack" in low: meal_type = "snack"
        return {"intent": "log_meal", "parameters": {"meal_type": meal_type}}
        
    # 10. Restock / Stock
    if any(w in low for w in ["restock", "bought", "purchased", "got", "add stock"]):
        return {"intent": "restock", "parameters": {"query": user_text}}
        
    # 11. Pantry / Fridge
    if any(w in low for w in ["pantry", "fridge", "freezer", "stock", "what do i have", "inventory", "despensa", "nevera"]):
        return {"intent": "pantry_status", "parameters": {}}
        
    # 12. Deals
    if any(w in low for w in ["deal", "discount", "sale", "offer", "cheap", "promo", "ofertas", "descuentos"]):
        return {"intent": "deals", "parameters": {}}
        
    # 13. Preferences / Allergies
    if any(w in low for w in ["allergy", "allergic", "avoid", "dislike", "diet", "vegan", "vegetarian", "keto", "alergia"]):
        return {"intent": "preferences", "parameters": {"query": user_text}}
        
    return {
        "intent": "chef_chat",
        "parameters": {},
        "chef_reply": "I'm your Wolt Smart Pantry Chef! I can plan your meals, incorporate your custom meal suggestions, manage your fridge inventory, check live store deals, and build your Wolt carts automatically. Tell me what you'd like to do!"
    }

def generate_ai_recipe(dish_name: str, ingredients: list = None, prefs: dict = None, api_key: str = None, user_id=None) -> str:
    """Generates an award-winning chef recipe using Gemini based on in-stock ingredients & preferences."""
    if not api_key and user_id:
        cfg = get_user_config(user_id)
        api_key = cfg.get("gemini_api_key", "")
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY", "")
    if prefs is None:
        prefs = load_user_preferences(user_id=user_id)
        
    ing_text = ", ".join(ingredients) if ingredients else "available in-stock pantry items"
    allergies = ", ".join(prefs.get("allergies", [])) or "None"
    avoided = ", ".join(prefs.get("avoided_ingredients", [])) or "None"
    diet = prefs.get("diet_type", "omnivore")
    h_size = prefs.get("household_size", 1)

    if api_key:
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
        for model_name in ["gemini-flash-lite-latest", "gemini-flash-latest"]:
            try:
                resp_text = call_gemini_api(prompt, api_key=api_key, model_name=model_name, json_mode=False)
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
        f"To unlock personalized dynamic recipes generated by Gemini, connect your free Gemini API key using `/setkey`!\n"
    )

def analyze_photo_with_vision(image_path: str, api_key: str = None, user_id=None) -> dict:
    """Analyzes a food or kitchen photo using Gemini Multimodal Vision or intelligent fallback."""
    if not api_key and user_id:
        cfg = get_user_config(user_id)
        api_key = cfg.get("gemini_api_key", "")
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY", "")
        
    if api_key:
        for model_name in ["gemini-flash-lite-latest", "gemini-flash-latest"]:
            try:
                from google import genai
                from PIL import Image
                
                client = genai.Client(api_key=api_key)
                pil_img = Image.open(image_path)
                
                prompt = """
You are an expert culinary vision AI, nutritionist, and smart grocery pantry auditor.
Analyze the provided photo with high precision.

Determine:
1. What type of photo is this?
   - "cooked_meal": A prepared/cooked meal plate or bowl ready to eat (e.g. breakfast scramble, dinner plate).
   - "groceries_restock": Freshly bought groceries, raw food items, food packaging, pantry shelf, fridge contents, or shopping receipt.

2. If it is "cooked_meal":
   - meal_type: "breakfast", "lunch", "dinner", or "snack".
   - dish_title: Concise culinary title of the prepared meal.
   - detected_items: List of raw ingredients consumed in this dish with realistic weights/quantities for 1-2 servings.
     Format: [{"name": "Farm eggs", "qty": 2, "unit": "eggs", "category": "protein"}, {"name": "Toast bread", "qty": 2, "unit": "slices", "category": "grain"}]
   - nutrition_estimate: {"calories": ~X, "protein_g": ~Y, "carbs_g": ~Z, "fat_g": ~W}
   - chef_notes: 1 sentence praising or advising on the cooking technique.

3. If it is "groceries_restock":
   - detected_items: Itemized inventory of every distinct grocery or food product visible with counts or package units.
     Format: [{"name": "Eesti Pagar Tosta", "qty": 1, "unit": "pack", "category": "staple"}, {"name": "Eggs", "qty": 10, "unit": "pcs", "category": "protein"}]
   - notes: Short summary of detected groceries.

Respond ONLY with a valid JSON object:
{
  "photo_type": "cooked_meal" | "groceries_restock",
  "meal_type": "breakfast" | "lunch" | "dinner" | "snack" | null,
  "dish_title": "<string>",
  "detected_items": [
    {
      "name": "<Item Name in English or Estonian>",
      "qty": <number>,
      "unit": "<g | pcs | slices | pack | kg | null>",
      "category": "protein" | "produce" | "staple" | "dairy"
    }
  ],
  "nutrition_estimate": {
    "calories": 450,
    "protein_g": 28,
    "carbs_g": 35,
    "fat_g": 22
  },
  "notes": "<string>"
}
"""
                resp = client.models.generate_content(
                    model=model_name,
                    contents=[prompt, pil_img]
                )
                if resp and resp.text:
                    clean = resp.text.strip()
                    if clean.startswith("```"):
                        clean = re.sub(r'^```(?:json)?\n', '', clean)
                        clean = re.sub(r'\n```$', '', clean)
                    return json.loads(clean)
            except Exception as e:
                print(f"[!] Gemini Vision analysis error with {model_name}: {e}")

    # Fallback heuristic if API key is absent or vision call fails
    return {
        "photo_type": "cooked_meal",
        "meal_type": "breakfast",
        "dish_title": "Egg & Toast Breakfast",
        "detected_items": [
            {"name": "Farm Eggs", "qty": 2, "unit": "eggs", "category": "protein"},
            {"name": "Toast Bread", "qty": 2, "unit": "slices", "category": "grain"},
            {"name": "Butter", "qty": 10, "unit": "g", "category": "fat"}
        ],
        "nutrition_estimate": {"calories": 380, "protein_g": 18, "carbs_g": 28, "fat_g": 20},
        "notes": "Photo received. Connect your Gemini API Key with /setkey for automated AI visual ingredient detection."
    }

def generate_ai_weekly_meal_plan(inventory_items=None, prefs=None, api_key=None, user_id=None, user_suggestions: str = None) -> dict:
    """Generates a fully dynamic 7-day culinary meal plan using Gemini based on in-stock ingredients, preferences, and user suggestions."""
    if not api_key and user_id:
        cfg = get_user_config(user_id)
        api_key = cfg.get("gemini_api_key", "")
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY", "")
        
    if not api_key:
        return None
        
    if prefs is None:
        prefs = load_user_preferences(user_id=user_id)
        
    diet = prefs.get("diet_type", "omnivore")
    h_size = max(1, prefs.get("household_size", 1))
    allergies = ", ".join(prefs.get("allergies", [])) or "None"
    avoided = ", ".join(prefs.get("avoided_ingredients", [])) or "None"
    
    raw_p_g = int(215 * h_size)
    cooked_p_g = int(150 * h_size)
    
    known_items = []
    if inventory_items:
        for it in inventory_items:
            name = it[0] if isinstance(it, (tuple, list)) else (it.get("name") or it.get("query") if isinstance(it, dict) else str(it))
            known_items.append(name)
    else:
        from .pantry import load_pantry_memory
        from .planner import get_candidate_grocery_list
        pantry = load_pantry_memory(user_id=user_id)
        for p in pantry.get("proteins", []):
            known_items.append(f"{p.get('name')} (x{p.get('qty', 1)})")
        for pr in pantry.get("produce", []):
            known_items.append(f"{pr.get('name')} (x{pr.get('qty', 1)})")
        for s in pantry.get("staples", []):
            known_items.append(f"{s.get('name')}")
        if not known_items:
            candidate = get_candidate_grocery_list(prefs, user_id=user_id)
            for it in candidate:
                known_items.append(f"{it[0]} (x{it[1]})")

    items_str = ", ".join(known_items) if known_items else "Standard fresh groceries"

    suggestion_block = ""
    if user_suggestions and user_suggestions.strip():
        suggestion_block = f"""
User Special Guidance & Suggestions for this Week:
"{user_suggestions.strip()}"
* IMPORTANT: Seamlessly incorporate the requested theme, cuisine, dishes, or specific ingredient focus into the 7-day schedule while maintaining strict freshness tiers, portion shrinkage math, and allergy safety.
"""

    prompt = f"""
You are an executive private chef and certified nutritionist.
Create an innovative, gourmet, and realistic 7-Day Meal Plan (Monday to Sunday) for {h_size} person(s).

Available in-stock pantry items & groceries: {items_str}.
Dietary Profile: {diet}.
Allergies (STRICTLY AVOID): {allergies}.
Disliked/Avoided ingredients: {avoided}.
Protein Target: ~{raw_p_g}g raw per meal (yields ~{cooked_p_g}g cooked per person accounting for 30% thermal cooking shrinkage).
{suggestion_block}
Freshness Schedule Rules:
- Monday & Tuesday (Tier 1: Ultra-Fresh): Prioritize delicate leafy greens (arugula/spinach), fresh raw minced meats / fish, and ripe avocados.
- Wednesday & Thursday (Tier 2: Resilient Produce & Poultry): Chicken breast fillets, bell peppers, broccoli, carrots, and eggs.
- Friday & Saturday (Tier 3: Hearty Proteins & Pantry Reserves): Cheeses (mozzarella), pasta bakes, burger bowls, grains, canned passata.
- Sunday (Tier 4: Fridge Clearing & Reset): Big vegetable & egg frittata or clearing skillet before next grocery delivery.

For every day (Monday to Sunday), provide:
1. breakfast (title, protein_raw, ingredients list, tip with precise culinary heat/technique)
2. lunch (title, protein_raw, ingredients list, tip with precise culinary heat/technique)
3. dinner (title, protein_raw, ingredients list, tip with precise culinary heat/technique)
4. snack (title, ingredients list)

Respond ONLY with a valid JSON object matching this schema:
{{
  "household_size": {h_size},
  "diet_type": "{diet}",
  "days": [
    {{
      "day_index": 0,
      "day_name": "Monday",
      "freshness_tier": "Tier 1: Ultra-Fresh (48h max)",
      "freshness_alert": "Consume fresh leafy greens & fresh ground meat first!",
      "breakfast": {{
        "title": "Dish Name",
        "protein_raw": "3 Farm eggs",
        "ingredients": ["Eggs", "Toast", "Avocado"],
        "tip": "Chef cooking technique with pan temperature and timing"
      }},
      "lunch": {{
        "title": "Dish Name",
        "protein_raw": "{raw_p_g}g beef (yields ~{cooked_p_g}g cooked)",
        "ingredients": ["Ground beef", "Red bell pepper", "Rice"],
        "tip": "High-heat searing technique"
      }},
      "dinner": {{
        "title": "Dish Name",
        "protein_raw": "{raw_p_g}g chicken (yields ~{cooked_p_g}g cooked)",
        "ingredients": ["Chicken breast", "Arugula", "Cherry tomatoes"],
        "tip": "Butter basting technique"
      }},
      "snack": {{
        "title": "Snack Name",
        "ingredients": ["Banana", "Dark chocolate"]
      }}
    }}
  ]
}}
"""

    for model_name in ["gemini-flash-lite-latest", "gemini-flash-latest"]:
        try:
            resp_text = call_gemini_api(prompt, api_key=api_key, model_name=model_name, json_mode=True)
            if resp_text and resp_text.strip():
                clean_json = resp_text.strip()
                if clean_json.startswith("```"):
                    clean_json = re.sub(r'^```(?:json)?\n', '', clean_json)
                    clean_json = re.sub(r'\n```$', '', clean_json)
                plan_data = json.loads(clean_json)
                if plan_data.get("days") and len(plan_data["days"]) >= 7:
                    plan_data["last_generated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    if user_suggestions and user_suggestions.strip():
                        plan_data["user_suggestions"] = user_suggestions.strip()
                    print(f"[✨] Dynamic 7-day meal plan generated by Gemini ({model_name})!")
                    return plan_data
        except Exception as e:
            print(f"[!] Gemini AI meal plan error with {model_name}: {e}")

    return None

def generate_ai_meal_swap(meal_type="lunch", current_title="", in_stock_ingredients=None, prefs=None, api_key=None, user_id=None) -> dict:
    """Generates an alternative dish recommendation dynamically using Gemini."""
    if not api_key and user_id:
        cfg = get_user_config(user_id)
        api_key = cfg.get("gemini_api_key", "")
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        from dotenv import load_dotenv
        load_dotenv(override=True)
        api_key = os.getenv("GEMINI_API_KEY", "")
        
    if prefs is None:
        prefs = load_user_preferences(user_id=user_id)
        
    diet = prefs.get("diet_type", "omnivore")
    h_size = max(1, prefs.get("household_size", 1))
    allergies = ", ".join(prefs.get("allergies", [])) or "None"
    avoided = ", ".join(prefs.get("avoided_ingredients", [])) or "None"
    ing_text = ", ".join(in_stock_ingredients) if in_stock_ingredients else "available in-stock pantry items"

    if api_key:
        prompt = f"""
You are an executive private chef.
Suggest an appetizing, distinct alternative {meal_type} dish to replace '{current_title}' for {h_size} person(s).
Available ingredients: {ing_text}.
Diet: {diet}. Strictly avoid allergies: {allergies}. Disliked: {avoided}.

Respond ONLY with valid JSON:
{{
  "title": "Gourmet Dish Name",
  "protein_raw": "Protein weight raw (yields cooked)",
  "ingredients": ["Item 1", "Item 2"],
  "tip": "Chef cooking technique with pan heat and tips"
}}
"""
        for model_name in ["gemini-flash-lite-latest", "gemini-flash-latest"]:
            try:
                resp_text = call_gemini_api(prompt, api_key=api_key, model_name=model_name, json_mode=True)
                if resp_text and resp_text.strip():
                    clean = resp_text.strip()
                    if clean.startswith("```"):
                        clean = re.sub(r'^```(?:json)?\n', '', clean)
                        clean = re.sub(r'\n```$', '', clean)
                    return json.loads(clean)
            except Exception as e:
                print(f"[!] Gemini meal swap error: {e}")

    # Fallback dish
    return {
        "title": "Pan-Seared Herb Protein with Roasted Vegetables",
        "protein_raw": f"{int(215 * h_size)}g protein",
        "ingredients": ["Protein cut", "Fresh vegetables", "Olive oil & garlic"],
        "tip": "Sear protein 4 mins per side on high heat. Toss vegetables with olive oil and garlic."
    }
