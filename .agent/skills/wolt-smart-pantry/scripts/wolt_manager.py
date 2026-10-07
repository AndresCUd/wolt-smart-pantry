"""
Wolt Smart Shopping Automation Assistant
Modular façade and CLI interface that re-exports all domain modules from wolt_core:
- wolt_core.config: Multi-user profiles, directories, preferences & settings.
- wolt_core.gemini: Gemini LLM & Vision, NLU intent classification & recipe generation.
- wolt_core.pantry: Virtual pantry inventory state, restocking & ingredient deductions.
- wolt_core.planner: 7-day meal schedule construction, thermal shrinkage & candidate items.
- wolt_core.browser: Playwright browser automation, exact matching & Wolt cart actions.
"""

import sys
import json
import argparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Re-export all symbols from wolt_core for 100% backward compatibility
from wolt_core import (
    # Config & Storage
    BASE_DIR,
    DATA_DIR,
    USER_DATA_DIR,
    PANTRY_MEMORY_FILE,
    USER_PREFERENCES_FILE,
    MEAL_PLAN_FILE,
    DEFAULT_STORE,
    DEFAULT_CITY,
    DEFAULT_COUNTRY,
    get_user_dir,
    get_pantry_file,
    get_meal_plan_file,
    get_user_preferences_file,
    get_user_browser_dir,
    get_user_config,
    save_user_config,
    get_user_store_and_city,
    list_active_users,
    set_user_abort,
    is_user_aborted,
    load_user_preferences,
    save_user_preferences,
    is_item_allowed,
    display_user_preferences,
    # Gemini AI & Vision
    call_gemini_api,
    parse_natural_language_intent,
    generate_ai_recipe,
    analyze_photo_with_vision,
    generate_ai_weekly_meal_plan,
    generate_ai_meal_swap,
    # Pantry Memory
    load_pantry_memory,
    save_pantry_memory,
    clear_pantry_memory,
    record_purchase_in_memory,
    display_pantry_memory,
    restock_pantry_from_detected_items,
    deduct_custom_ingredients,
    # Meal Planner & Nutrition
    DEFAULT_GROCERY_LIST,
    SAMPLE_WEEKLY_GROCERY_LIST,
    get_candidate_grocery_list,
    generate_weekly_meal_plan,
    generate_default_weekly_plan,
    load_meal_plan,
    save_meal_plan,
    get_day_menu_formatted,
    get_single_meal_formatted,
    log_meal_consumption,
    # Browser Automation
    click_element_safely,
    get_cart_total_price,
    close_any_unwanted_modal,
    get_store_search_input,
    validate_item_match,
    search_and_add_item,
    add_items_to_cart,
    inspect_store_items,
    check_wolt_session,
    login_mode,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wolt Smart Shopping Automation Assistant")
    parser.add_argument("mode", choices=["login", "check", "search", "deals", "add", "pantry", "preferences"], help="Mode: 'login', 'check', 'search', 'deals', 'add', 'pantry', 'preferences'")
    parser.add_argument("--user", type=int, help="Telegram User ID for multi-user profile isolation")
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
    parser.add_argument("--headless", action="store_true", help="Force headless browser execution")
    
    # User Preferences flags
    parser.add_argument("--diet", help="Set dietary type (omnivore, pescatarian, vegetarian, vegan, keto, high-protein)")
    parser.add_argument("--allergies", help="Comma-separated list of allergies (e.g. 'peanuts, shellfish, lactose')")
    parser.add_argument("--avoid", help="Comma-separated list of avoided foods/dislikes (e.g. 'pork, mushrooms, eggplant')")
    parser.add_argument("--people", "--household", type=int, help="Set household size (number of people)")
    parser.add_argument("--notes", help="Custom dietary notes (e.g. 'lactose-free milk only')")
    
    args = parser.parse_args()
    user_id = args.user
    
    if args.mode == "login":
        login_mode(user_id=user_id)
    elif args.mode == "check":
        res = check_wolt_session(user_id=user_id, headless=args.headless or None)
        print(json.dumps(res, indent=2))
    elif args.mode == "preferences":
        prefs = load_user_preferences(user_id=user_id)
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
            save_user_preferences(prefs, user_id=user_id)
        display_user_preferences(user_id=user_id)
    elif args.mode == "pantry":
        if args.action == "status":
            display_pantry_memory(user_id=user_id)
        elif args.action == "clear":
            clear_pantry_memory(user_id=user_id)
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
            record_purchase_in_memory(items_to_record, store_slug=args.store, user_id=user_id)
            display_pantry_memory(user_id=user_id)
    elif args.mode == "deals":
        inspect_store_items(args.store, queries=args.queries, get_deals=True, city=args.city, country=args.country, address=args.address, output_file=args.output, user_id=user_id, headless=args.headless or None)
    elif args.mode == "search":
        search_queries = args.queries or ["hakkliha", "broilerifilee", "banaan", "paprika", "rukola"]
        inspect_store_items(args.store, queries=search_queries, get_deals=False, city=args.city, country=args.country, address=args.address, output_file=args.output, user_id=user_id, headless=args.headless or None)
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
        
        add_items_to_cart(args.store, items, city=args.city, country=args.country, address=args.address, record_memory=(args.record_memory or args.auto or args.auto_pay), auto_pay=args.auto_pay, user_id=user_id, headless=args.headless or None)
