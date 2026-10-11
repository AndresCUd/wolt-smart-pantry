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

_MODEL_CACHE = {}  # api_key_hash -> (timestamp, [models])

def list_supported_gemini_models(api_key: str) -> list[str]:
    """Dynamically queries Google Gemini API's ModelService to find models supporting generateContent for this API key."""
    if not api_key:
        return []
    clean_key = api_key.strip()
    key_hash = hash(clean_key)
    now = time.time()

    if key_hash in _MODEL_CACHE:
        ts, cached = _MODEL_CACHE[key_hash]
        if now - ts < 21600 and cached:
            return cached

    discovered = []
    for ver in ["v1beta", "v1"]:
        try:
            url = f"https://generativelanguage.googleapis.com/{ver}/models?key={clean_key}"
            req = urllib.request.Request(url, headers={"User-Agent": "WoltSmartPantry/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = data.get("models", [])
                for m in models:
                    methods = m.get("supportedGenerationMethods", [])
                    if "generateContent" in methods:
                        name = m.get("name", "").replace("models/", "").strip()
                        if name and name not in discovered:
                            discovered.append(name)
            if discovered:
                break
        except Exception:
            pass

    if discovered:
        def _model_priority(name: str):
            n = name.lower()
            if "3.8-flash" in n: return 5
            if "3.7-flash" in n: return 10
            if "3.6-flash" in n: return 15
            if "3.5-flash-lite" in n: return 20
            if "3.5-flash" in n: return 22
            if "2.5-flash-lite" in n: return 25
            if "2.5-flash" in n: return 28
            if "2.0-flash" in n and "exp" not in n: return 30
            if "1.5-flash" in n: return 35
            if "flash" in n: return 40
            if "pro" in n: return 50
            return 90

        discovered.sort(key=_model_priority)
        _MODEL_CACHE[key_hash] = (now, discovered)
        return discovered

    # Fallback if listModels request failed
    fallback = [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-1.5-pro",
    ]
    return fallback

def call_gemini_api(prompt: str, api_key: str = None, model_name: str = None, json_mode: bool = False) -> str:
    """Universal Gemini API client with fallback across discovered models, google-genai, google.generativeai, and direct REST endpoint."""
    if not api_key:
        return ""
    api_key = api_key.strip()

    models_to_try = [model_name] if model_name else []
    for m in list_supported_gemini_models(api_key):
        if m not in models_to_try:
            models_to_try.append(m)
    if not models_to_try:
        models_to_try = ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"]

    for m_target in models_to_try[:4]:
        # Strategy 1: Official Google GenAI SDK (v1)
        try:
            from google import genai
            client = genai.Client(api_key=api_key)
            config = {}
            if json_mode:
                config["response_mime_type"] = "application/json"
            resp = client.models.generate_content(
                model=m_target,
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
            model = legacy_genai.GenerativeModel(m_target, generation_config=gen_config)
            resp = model.generate_content(prompt)
            if resp and resp.text:
                return resp.text.strip()
        except Exception:
            pass

        # Strategy 3: Direct HTTP REST API via v1beta and v1 endpoints
        for ver in ["v1beta", "v1"]:
            try:
                url = f"https://generativelanguage.googleapis.com/{ver}/models/{m_target}:generateContent?key={api_key}"
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
            except Exception:
                pass

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
        
    # 4. Recipe / Special cooking request for X people
    if any(w in low for w in ["recipe", "how to cook", "how to make", "instructions for", "prepare", "receta"]) or (
        any(w in low for w in ["cook", "make", "hacer", "cocinar", "preparar", "lunch", "dinner", "almuerzo", "cena"]) and
        any(w in low for w in ["special", "especial", "person", "persons", "people", "personas", "guest", "guests", "for 2", "for 3", "for 4", "for 5", "para 2", "para 3", "para 4", "para 5"])
    ):
        servings = None
        m_s = re.search(r'(\d+)\s*(?:person|persons|people|personas|guests)', low)
        if not m_s:
            m_s = re.search(r'(?:for|para)\s+(\d+)', low)
        if m_s:
            servings = int(m_s.group(1))
        return {"intent": "recipe", "parameters": {"dish_or_ingredients": user_text, "servings": servings}}
        
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
        
    # 13. Preferences / Allergies / Custom Breakfast
    if any(w in low for w in ["breakfast", "desayuno"]) and any(w in low for w in ["prefer", "like", "want", "have", "change", "set", "quiero", "cambiar", "gustar", "custom"]):
        pref_text = user_text
        for kw in ["breakfast", "desayuno"]:
            if kw in low:
                idx = low.find(kw)
                after = user_text[idx + len(kw):].strip(" :-–—,/.")
                for prefix in ["i prefer", "i usually prefer", "i want", "to have", "is", "as"]:
                    if after.lower().startswith(prefix):
                        after = after[len(prefix):].strip(" :-–—,/.")
                if len(after) > 3:
                    pref_text = after
                break
        return {"intent": "preferences", "parameters": {"breakfast_preference": pref_text, "query": user_text}}

    if any(w in low for w in ["allergy", "allergic", "avoid", "dislike", "diet", "vegan", "vegetarian", "keto", "alergia"]):
        return {"intent": "preferences", "parameters": {"query": user_text}}
        
    return {
        "intent": "chef_chat",
        "parameters": {},
        "chef_reply": "I'm your Wolt Smart Pantry Chef! I can plan your meals, incorporate your custom meal suggestions, manage your fridge inventory, check live store deals, and build your Wolt carts automatically. Tell me what you'd like to do!"
    }

def generate_ai_recipe(dish_name: str, ingredients: list = None, prefs: dict = None, api_key: str = None, user_id=None, servings: int = None) -> str:
    """Generates an award-winning chef recipe using Gemini based on in-stock ingredients & preferences."""
    if not api_key and user_id:
        cfg = get_user_config(user_id)
        api_key = cfg.get("gemini_api_key", "")
    if prefs is None:
        prefs = load_user_preferences(user_id=user_id)
        
    # Auto-detect servings if not explicitly supplied
    if servings is None:
        m = re.search(r'(?:for|para|serves|serving|servings)\s+(\d+)\s*(?:person|persons|people|personas|guests)?', dish_name, re.IGNORECASE)
        if not m:
            m = re.search(r'\b(\d+)\s*(?:person|persons|people|personas|guests)\b', dish_name, re.IGNORECASE)
        if m:
            servings = int(m.group(1))

    h_size = servings if servings and servings > 0 else max(1, prefs.get("household_size", 1))
    ing_text = ", ".join(ingredients) if ingredients else "available in-stock pantry items"
    allergies = ", ".join(prefs.get("allergies", [])) or "None"
    avoided = ", ".join(prefs.get("avoided_ingredients", [])) or "None"
    diet = prefs.get("diet_type", "omnivore")

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
   - "wolt_receipt": Digital screenshot of a Wolt order, Wolt app order history/receipt, email receipt, supermarket paper receipt, or delivery invoice listing grocery items.
   - "groceries_restock": Freshly bought groceries, supermarket food packaging, items on a counter/table, pantry shelf, or fridge/freezer contents.
   - "cooked_meal": A prepared/cooked meal plate or bowl ready to eat (e.g. breakfast scramble, steak with veggies, salad).

2. If it is "wolt_receipt":
   - Parse each purchased grocery line item name, purchase multiplier/quantity (e.g. 1x, 2x, x2, 1 tk), package size, and unit.
   - EXCLUDE non-food or auxiliary lines: delivery fee (kohalevedu), service fee (teenustasu), paper bag fee (kott), courier tip, subtotal, total amount.
   - Normalize product names clearly in Estonian or English (e.g., "Eggo suured munad L 10tk", "Rakvere toorsuitsupeekon 150g", "Dolce Gusto Café Au Lait kapslid").
   - detected_items: Itemized list of every purchased grocery product with exact purchased quantities.
     Format: [{"name": "<Specific Product Name>", "qty": <int or float>, "unit": "<pack | pcs | slices | g | kg | bottle | box>", "category": "protein" | "produce" | "staple" | "dairy"}]
   - dish_title: "Wolt Order Receipt Restock"
   - chef_notes: Short summary of receipt items and total items counted.

3. If it is "groceries_restock":
   - Inspect every visible package, bag, carton, bottle, fruit, vegetable, or container.
   - Read packaging labels (in English or Estonian, e.g. Tallegg broilerifilee, kanafilee, hakkliha, riivjuust, juust, piim, munad, tortiljad, avokaado, banaan, sidrun/lemon juice).
   - detected_items: Itemized list of every distinct food product with realistic counts or packages.
     Format: [{"name": "<Specific Product Name>", "qty": <int or float>, "unit": "<pack | pcs | slices | g | kg | bottle | box>", "category": "protein" | "produce" | "staple" | "dairy"}]
   - dish_title: Summary description, e.g. "Fridge Grocery Restock" or "Kitchen Inventory Audit".
   - chef_notes: Short summary of detected items.

4. If it is "cooked_meal":
   - meal_type: "breakfast", "lunch", "dinner", or "snack".
   - dish_title: Concise culinary title of the prepared meal.
   - detected_items: Raw ingredients in this dish with realistic weights/quantities for 1-2 servings.
   - chef_notes: 1 sentence praising or advising on the cooking technique.

Respond ONLY with a valid JSON object matching this schema:
{
  "photo_type": "wolt_receipt" | "groceries_restock" | "cooked_meal",
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

    api_key = api_key.strip()

    def _optimize_image_for_vision(src_path: str, max_dim=1280, quality=85) -> tuple[bytes, str]:
        """Compresses and scales high-res smartphone images to ~150KB to prevent network timeouts."""
        try:
            from PIL import Image, ImageOps
            import io
            with Image.open(src_path) as img:
                try:
                    img = ImageOps.exif_transpose(img)
                except Exception:
                    pass
                img = img.convert("RGB")
                img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                return buf.getvalue(), "image/jpeg"
        except Exception:
            with open(src_path, "rb") as f:
                return f.read(), "image/jpeg"

    discovered = list_supported_gemini_models(api_key)
    models_to_try = [m for m in discovered] if discovered else [
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-1.5-pro",
    ]
    # Limit models tried to top 4 to prevent slow cascading timeouts
    models_to_try = models_to_try[:4]
    last_error = ""
    attempted_errors = []

    # Strategy 1: Official Google GenAI SDK (v1)
    for model_name in models_to_try:
        try:
            from google import genai
            from PIL import Image
            import io
            client = genai.Client(api_key=api_key)
            img_bytes, _ = _optimize_image_for_vision(image_path)
            pil_img = Image.open(io.BytesIO(img_bytes))
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
            attempted_errors.append(f"SDK:{model_name}->{e}")
            last_error = str(e)

    # Strategy 2: Legacy google.generativeai SDK
    for model_name in models_to_try:
        try:
            import google.generativeai as legacy_genai
            from PIL import Image
            import io
            legacy_genai.configure(api_key=api_key)
            model = legacy_genai.GenerativeModel(model_name)
            img_bytes, _ = _optimize_image_for_vision(image_path)
            pil_img = Image.open(io.BytesIO(img_bytes))
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
            attempted_errors.append(f"Legacy:{model_name}->{e}")
            last_error = str(e)

    # Strategy 3: Direct HTTP REST API via v1beta and v1 endpoints (Zero external dependencies)
    import base64
    import urllib.error
    try:
        img_bytes, mime_type = _optimize_image_for_vision(image_path)
        b64_img = base64.b64encode(img_bytes).decode("utf-8")

        candidate_configs = []
        for m_name in models_to_try:
            candidate_configs.append(("v1beta", m_name))
            candidate_configs.append(("v1", m_name))

        for api_ver, m_name in candidate_configs:
            m_code = m_name.replace("models/", "")
            try:
                url = f"https://generativelanguage.googleapis.com/{api_ver}/models/{m_code}:generateContent?key={api_key}"
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
                with urllib.request.urlopen(req, timeout=35) as response:
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
            except urllib.error.HTTPError as e_http:
                try:
                    err_json = json.loads(e_http.read().decode("utf-8"))
                    msg = err_json.get("error", {}).get("message", e_http.reason)
                    attempted_errors.append(f"{m_code}:{e_http.code} {msg}")
                    last_error = f"{e_http.code} {msg}"
                except Exception:
                    attempted_errors.append(f"{m_code}:{e_http.code} {e_http.reason}")
                    last_error = f"HTTP {e_http.code}: {e_http.reason}"
            except Exception as e_rest:
                attempted_errors.append(f"{m_code}->{e_rest}")
                last_error = str(e_rest)
    except Exception as e_prep:
        last_error = str(e_prep)

    err_summary = " | ".join(attempted_errors[-3:]) if attempted_errors else last_error
    return {
        "error": "vision_api_error",
        "message": f"Gemini Vision error across all strategies. Last: {err_summary}",
        "detected_items": [],
        "photo_type": "unknown"
    }

def parse_receipt_text_with_ai(receipt_text: str, api_key: str = None, user_id=None) -> list[dict]:
    """Parses pasted text from a Wolt receipt, order confirmation, or email into structured grocery items."""
    if not receipt_text or not receipt_text.strip():
        return []
    
    if not api_key and user_id:
        cfg = get_user_config(user_id)
        api_key = cfg.get("gemini_api_key", "")

    prompt = f"""
You are an expert grocery receipt parser for Wolt and Estonian supermarkets.
Parse the following Wolt receipt or grocery order text into a structured JSON list of food products:

\"\"\"{receipt_text}\"\"\"

Rules:
1. Extract only grocery / food products with their purchase quantity (e.g., 1, 2, 7).
2. Ignore delivery fees, bag fees (kott), service fees, tips, payment method lines, or totals.
3. Categorize each item as: "protein", "produce", "dairy", or "staple".
4. Return ONLY a valid JSON array of objects with schema:
[
  {{
    "name": "<Product Name in Estonian or English>",
    "qty": <number>,
    "unit": "<pack | pcs | g | kg | bottle | box | null>",
    "category": "<protein | produce | staple | dairy>"
  }}
]
"""
    if api_key:
        try:
            raw = call_gemini_api(prompt, api_key=api_key, json_mode=True)
            if raw:
                m = re.search(r'\[.*\]', raw, re.DOTALL)
                if m:
                    parsed = json.loads(m.group(0))
                    if isinstance(parsed, list) and parsed:
                        return parsed
        except Exception:
            pass

    # Deterministic fallback parsing (e.g. lines like '2x Milk', 'Eggo munad x1', 'Bread - 1')
    results = []
    lines = [l.strip() for l in receipt_text.split('\n') if l.strip()]
    for line in lines:
        if any(ign in line.lower() for ign in ["total", "subtotal", "fee", "kohalevedu", "teenustasu", "delivery", "tip", "kott", "kuller"]):
            continue
        q_match = re.search(r'(?:^|[\s(])(?:x\s*(\d+)|\b(\d+)\s*x|\b(\d+)\s*(?:tk|pcs|pk|pack))\b', line, re.IGNORECASE)
        qty = int(q_match.group(1) or q_match.group(2) or q_match.group(3)) if q_match else 1
        clean_name = re.sub(r'(?:^|[\s(])(?:x\s*\d+|\b\d+\s*x|\b\d+\s*(?:tk|pcs|pk|pack))\b', '', line, flags=re.IGNORECASE)
        clean_name = re.sub(r'\d+[.,]\d{2}\s*€|€\s*\d+[.,]\d{2}', '', clean_name).strip(' -•*,:;')
        if clean_name and len(clean_name) > 1:
            name_low = clean_name.lower()
            cat = "protein" if any(w in name_low for w in ["muna", "egg", "kana", "hakkliha", "veis", "peekon", "bacon", "kala"]) else ("produce" if any(w in name_low for w in ["tomat", "kurk", "paprika", "banaan", "sibul", "avokaado"]) else "staple")
            results.append({"name": clean_name.title(), "qty": qty, "unit": "pack", "category": cat})
    return results

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

    b_pref = prefs.get("breakfast_preference", "")
    breakfast_block = ""
    if b_pref and b_pref.strip():
        breakfast_block = f"""
User Favorite Breakfast Style:
"{b_pref.strip()}"
* CHEF GUIDANCE: The user loves this breakfast style ({b_pref.strip()}). Feature these ingredients as the primary morning anchor across the week (4-5 days with creative variations like sunny fried egg with bacon & toast, breakfast melts, fluffy pancake stacks, and scrambled skillets), while offering complementary variety (like lighter fruit bowls or vegetable bakes on reset days) so the morning routine is never monotonous.
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
{breakfast_block}
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
