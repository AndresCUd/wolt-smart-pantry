"""
wolt_core.config
Handles file paths, multi-user profile isolation, storage, preferences, and cancellation signals.
"""

import os
import sys
import json
import threading

# Base directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data", "users")
USER_DATA_DIR = os.path.join(BASE_DIR, ".wolt_profile")
PANTRY_MEMORY_FILE = os.path.join(BASE_DIR, "pantry_memory.json")
USER_PREFERENCES_FILE = os.path.join(BASE_DIR, "user_preferences.json")
MEAL_PLAN_FILE = os.path.join(BASE_DIR, "meal_plan.json")

# Defaults
DEFAULT_STORE = "wolt-market-maakri"
DEFAULT_CITY = "tallinn"
DEFAULT_COUNTRY = "est"

def get_user_dir(user_id=None) -> str:
    """Returns the dedicated data directory for a specific Telegram user ID."""
    if user_id:
        u_dir = os.path.join(DATA_DIR, str(user_id))
    else:
        u_dir = BASE_DIR
    os.makedirs(u_dir, exist_ok=True)
    return u_dir

def get_pantry_file(user_id=None) -> str:
    """Returns path to user's pantry memory file, initializing from global template if needed."""
    if user_id:
        p_file = os.path.join(get_user_dir(user_id), "pantry_memory.json")
        if not os.path.exists(p_file) and os.path.exists(PANTRY_MEMORY_FILE):
            try:
                import shutil
                shutil.copy2(PANTRY_MEMORY_FILE, p_file)
            except Exception:
                pass
        return p_file
    return PANTRY_MEMORY_FILE

def get_meal_plan_file(user_id=None) -> str:
    """Returns path to user's weekly meal plan file, initializing from global template if needed."""
    if user_id:
        m_file = os.path.join(get_user_dir(user_id), "meal_plan.json")
        if not os.path.exists(m_file) and os.path.exists(MEAL_PLAN_FILE):
            try:
                import shutil
                shutil.copy2(MEAL_PLAN_FILE, m_file)
            except Exception:
                pass
        return m_file
    return MEAL_PLAN_FILE

def get_user_preferences_file(user_id=None) -> str:
    """Returns path to user's dietary preferences file."""
    if user_id:
        pref_file = os.path.join(get_user_dir(user_id), "user_preferences.json")
        if not os.path.exists(pref_file) and os.path.exists(USER_PREFERENCES_FILE):
            try:
                import shutil
                shutil.copy2(USER_PREFERENCES_FILE, pref_file)
            except Exception:
                pass
        return pref_file
    return USER_PREFERENCES_FILE

def get_user_browser_dir(user_id=None) -> str:
    """Returns the isolated Playwright browser profile directory for a specific user."""
    if user_id:
        b_dir = os.path.join(get_user_dir(user_id), ".wolt_profile")
        if not os.path.exists(b_dir) and os.path.exists(USER_DATA_DIR):
            try:
                import shutil
                shutil.copytree(USER_DATA_DIR, b_dir, dirs_exist_ok=True)
            except Exception:
                pass
    else:
        b_dir = USER_DATA_DIR
    os.makedirs(b_dir, exist_ok=True)
    return b_dir

def get_user_config(user_id=None) -> dict:
    """Loads user-specific configuration (custom store, city, budget, auto-pay, custom API keys)."""
    if not user_id:
        return {}
    cfg_file = os.path.join(get_user_dir(user_id), "config.json")
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_user_config(cfg: dict, user_id=None):
    """Saves user-specific configuration."""
    if not user_id:
        return
    cfg_file = os.path.join(get_user_dir(user_id), "config.json")
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

def get_user_store_and_city(user_id=None) -> tuple[str, str, str]:
    """Returns (store_slug, city, country) tailored to the user profile."""
    cfg = get_user_config(user_id) if user_id else {}
    store = cfg.get("store") or os.getenv("WOLT_STORE", DEFAULT_STORE)
    city = cfg.get("city") or os.getenv("WOLT_CITY", DEFAULT_CITY)
    country = cfg.get("country") or os.getenv("WOLT_COUNTRY", DEFAULT_COUNTRY)
    return store, city, country

def list_active_users() -> list[int]:
    """Lists all user IDs with initialized profiles in data/users/."""
    if not os.path.exists(DATA_DIR):
        return []
    users = []
    for d in os.listdir(DATA_DIR):
        if d.isdigit():
            users.append(int(d))
    return users

# User operation cancellation flags
USER_ABORT_FLAGS = {}

def set_user_abort(user_id=None, abort=True):
    """Sets or clears the abort signal for a specific user's running tasks."""
    uid = user_id or 0
    if uid not in USER_ABORT_FLAGS:
        USER_ABORT_FLAGS[uid] = threading.Event()
    if abort:
        USER_ABORT_FLAGS[uid].set()
    else:
        USER_ABORT_FLAGS[uid].clear()

def is_user_aborted(user_id=None) -> bool:
    """Checks if the given user has requested to abort their current operation."""
    uid = user_id or 0
    ev = USER_ABORT_FLAGS.get(uid)
    return ev.is_set() if ev else False

def load_user_preferences(user_id=None) -> dict:
    """Loads persistent user dietary preferences, allergies, and food avoidances."""
    target_file = get_user_preferences_file(user_id)
    if os.path.exists(target_file):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
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

def save_user_preferences(prefs: dict, user_id=None):
    """Saves updated user dietary preferences and allergy profile to disk."""
    target_file = get_user_preferences_file(user_id)
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2, ensure_ascii=False)
    print(f"[👤] User dietary preferences saved: {target_file}")

def is_item_allowed(item_name: str, prefs=None, user_id=None) -> tuple[bool, str]:
    """Checks if an item violates any user allergies or food avoidance rules."""
    if prefs is None:
        prefs = load_user_preferences(user_id=user_id)
    
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

def display_user_preferences(user_id=None):
    """Prints a formatted summary of the user's dietary preferences and allergies."""
    prefs = load_user_preferences(user_id=user_id)
    print("\n" + "="*60)
    print(f"👤 USER DIETARY PROFILE & ALLERGY PREFERENCES (User: {user_id or 'default'})")
    print("="*60)
    print(f"• Diet Type:            {prefs.get('diet_type', 'omnivore').capitalize()}")
    print(f"• Household Size:       {prefs.get('household_size', 1)} person(s)")
    print(f"• Allergies:            {', '.join(prefs.get('allergies', [])) if prefs.get('allergies') else 'None recorded'}")
    print(f"• Avoided Ingredients:  {', '.join(prefs.get('avoided_ingredients', [])) if prefs.get('avoided_ingredients') else 'None recorded'}")
    print(f"• Preferred Proteins:   {', '.join(prefs.get('preferred_proteins', [])) if prefs.get('preferred_proteins') else 'Standard'}")
    if prefs.get("notes"):
        print(f"• Special Notes:        {prefs.get('notes')}")
    print("="*60 + "\n")
