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
    jev_key = cfg.get("jev_ai_api_key") or cfg.get("typesafe_api_key") if cfg else ""
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
                                "user_status": "Viewing user profile, active configuration, API keys status, or account settings (e.g. 'show my config', 'my profile', 'how are my keys', 'ver mi perfil', 'mi configuracion', 'mis claves', 'user status')",
                                "set_key": "Setting or configuring an API key (e.g. 'setkey AIzaSy...', 'set gemini key ...', 'guardar clave...')",
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
                                "user_status": "Viewing user profile, active configuration, API keys status, or account settings (e.g. 'show my config', 'my profile', 'how are my keys', 'ver mi perfil', 'mi configuracion', 'mis claves', 'user status')",
                                "set_key": "Setting or configuring an API key (e.g. 'setkey AIzaSy...', 'set gemini key ...', 'guardar clave...')",
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
            elif chosen == "set_key":
                parts = user_text.split()
                for p in parts:
                    if p.startswith("AIzaSy"):
                        params["key_value"] = p.strip()
                        params["key_type"] = "gemini"
                        break
            return {
                "intent": chosen,
                "parameters": params,
                "chef_reply": "I'm your Wolt Smart Pantry Chef! How can I assist you?",
                "classifier": "jev-ai"
            }

    # Stage 2: Deep Generative Parsing via Gemini
    api_key = cfg.get("gemini_api_key", "") if cfg else ""
    if api_key:
        prompt = f"""
You are the natural language understanding brain for the Wolt Smart Pantry Telegram Bot.
Analyze this user message: "{user_text}"

Classify into one of these intents:
- "user_status": Asking to view user profile, active configuration, API keys status, budget, or account settings (e.g. "my profile", "show my config", "ver mi configuracion", "mis claves", "user status").
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
    
    # 0. User Status / Profile / Config
    if any(w in low for w in ["profile", "perfil", "mi config", "my config", "mis claves", "my keys", "user status", "mi estado", "account status", "estado de usuario", "who am i", "quien soy", "mis ajustes", "configuracion"]):
        return {"intent": "user_status", "parameters": {}}

    # 0.1 Set Key
    if low.startswith("setkey") or low.startswith("set key") or "aizasy" in low:
        parts = user_text.split()
        for p in parts:
            if p.startswith("AIzaSy"):
                return {"intent": "set_key", "parameters": {"key_value": p.strip(), "key_type": "gemini"}}
        return {"intent": "set_key", "parameters": {}}

    if low.startswith("setjev") or low.startswith("set jev"):
        parts = user_text.split()
        if len(parts) > 1:
            return {"intent": "set_jev", "parameters": {"key_value": parts[-1].strip()}}
        return {"intent": "set_jev", "parameters": {}}

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
    """Analyzes a food or kitchen photo using Gemini Multimodal Vision across google-genai, google.generativeai, and direct REST endpoint."""
    if not api_key and user_id:
        cfg = get_user_config(user_id)
        api_key = cfg.get("gemini_api_key", "")
        
    if not api_key:
        return {
            "error": "missing_api_key",
            "message": "No private Gemini API key configured. Run /setkey AIzaSy... to activate photo vision recognition.",
            "detected_items": [],
            "photo_type": "unknown"
        }

    prompt = """
You are an expert culinary vision AI, nutritionist, and smart grocery pantry auditor for supermarket items in Estonia/Europe.
Analyze the provided photo with high precision.

Determine:
1. What type of photo is this?
   - "groceries_restock": Freshly bought groceries, supermarket food packaging, items on a counter/table, pantry shelf, fridge/freezer contents, or shopping receipt.
   - "cooked_meal": A prepared/cooked meal plate or bowl ready to eat (e.g. breakfast scramble, steak with veggies, salad).

2. If it is "groceries_restock":
   - Inspect every visible package, bag, carton, bottle, fruit, vegetable, or container.
   - Read packaging labels (in English or Estonian, e.g. Tallegg broilerifilee, kanafilee, hakkliha, riivjuust, juust, piim, munad, tortiljad, avokaado, banaan, sidrun/lemon juice).
   - detected_items: Itemized list of every distinct food product with realistic counts or packages.
     Format: [{"name": "<Specific Product Name>", "qty": <int or float>, "unit": "<pack | pcs | slices | g | kg | bottle | box>", "category": "protein" | "produce" | "staple" | "dairy"}]
   - dish_title: Summary description, e.g. "Fridge Grocery Restock" or "Kitchen Inventory Audit".
   - chef_notes: Short summary of detected items.

3. If it is "cooked_meal":
   - meal_type: "breakfast", "lunch", "dinner", or "snack".
   - dish_title: Concise culinary title of the prepared meal.
   - detected_items: Raw ingredients in this dish with realistic weights/quantities for 1-2 servings.
   - chef_notes: 1 sentence praising or advising on the cooking technique.

Respond ONLY with a valid JSON object matching this schema:
{
  "photo_type": "groceries_restock" | "cooked_meal",
  "dish_title": "<string>",
  "meal_type": "breakfast" | "lunch" | "dinner" | "snack" | null,
  "detected_items": [
    {
      "name": "<Item Name in English or Estonian>",
      "qty": <number>,
      "unit": "<pack | pcs | slices | g | kg | bottle | box | null>",
      "category": "protein" | "produce" | "staple" | "dairy"
    }
  ],
  "composition": {
    "proteins": ["<Protein items>"],
    "carbs": ["<Carb items>"],
    "produce": ["<Produce items>"],
    "dairy_and_fats": ["<Dairy and fat items>"]
  },
  "estimated_macros": {
    "calories": 450,
    "protein_g": 28,
    "carbs_g": 35,
    "fat_g": 22
  },
  "chef_notes": "<string>"
}
"""

    models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash", "gemini-flash-latest"]
    last_error = ""

    # Strategy 1: Official Google GenAI SDK (v1)
    for model_name in models_to_try:
        try:
            from google import genai
            from PIL import Image
            client = genai.Client(api_key=api_key)
            pil_img = Image.open(image_path)
            resp = client.models.generate_content(
                model=model_name,
                contents=[prompt, pil_img]
            )
            if resp and resp.text:
                clean = resp.text.strip()
                if clean.startswith("```"):
                    clean = re.sub(r'^```(?:json)?\n', '', clean)
                    clean = re.sub(r'\n```$', '', clean)
                data = json.loads(clean)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            last_error = str(e)

    # Strategy 2: Legacy google.generativeai SDK
    for model_name in ["gemini-1.5-flash", "gemini-1.5-pro"]:
        try:
            import google.generativeai as legacy_genai
            from PIL import Image
            legacy_genai.configure(api_key=api_key)
            model = legacy_genai.GenerativeModel(model_name)
            pil_img = Image.open(image_path)
            resp = model.generate_content([prompt, pil_img])
            if resp and resp.text:
                clean = resp.text.strip()
                if clean.startswith("```"):
                    clean = re.sub(r'^```(?:json)?\n', '', clean)
                    clean = re.sub(r'\n```$', '', clean)
                data = json.loads(clean)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            last_error = str(e)

    # Strategy 3: Direct HTTP REST API via v1beta endpoint (Zero external dependencies)
    import base64
    import mimetypes
    try:
        mime_type = mimetypes.guess_type(image_path)[0] or "image/jpeg"
        with open(image_path, "rb") as f:
            b64_img = base64.b64encode(f.read()).decode("utf-8")

        for model_name in models_to_try:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
                payload = {
                    "contents": [{
                        "parts": [
                            {"text": prompt},
                            {
                                "inline_data": {
                                    "mime_type": mime_type,
                                    "data": b64_img
                                }
                            }
                        ]
                    }],
                    "generationConfig": {
                        "responseMimeType": "application/json"
                    }
                }
                req_data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as response:
                    res_body = json.loads(response.read().decode("utf-8"))
                    candidates = res_body.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts and "text" in parts[0]:
                            raw_txt = parts[0]["text"].strip()
                            clean = re.sub(r'^```(?:json)?\n', '', raw_txt)
                            clean = re.sub(r'\n```$', '', clean)
                            data = json.loads(clean)
                            if isinstance(data, dict):
                                return data
            except Exception as e_rest:
                last_error = str(e_rest)
    except Exception as e_prep:
        last_error = str(e_prep)

    return {
        "error": "vision_api_error",
        "message": f"Gemini Vision error across all strategies. Last error: {last_error}",
        "detected_items": [],
        "photo_type": "unknown"
    }

def generate_ai_weekly_meal_plan(inventory_items=None, prefs=None, api_key=None, user_id=None, user_suggestions: str = None) -> dict:
    """Generates a fully dynamic 7-day culinary meal plan using Gemini based on in-stock ingredients, preferences, and user suggestions."""
    if not api_key and user_id:
        cfg = get_user_config(user_id)
        api_key = cfg.get("gemini_api_key", "")
        
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
