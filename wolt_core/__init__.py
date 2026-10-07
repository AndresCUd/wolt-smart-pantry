"""
wolt_core package
Modular domain-driven architecture for Wolt Smart Pantry:
- config: Paths, multi-user isolation, preferences, and cancellation signals.
- gemini: LLM & Vision capabilities, NLU intent classification, recipes, and dynamic meal plan AI.
- pantry: Virtual pantry memory state, restocking, and ingredient deductions.
- planner: 7-day meal schedule construction, thermal shrinkage calculations, and recipe formatting.
- browser: Playwright browser automation, exact matching, substitute approvals, and Wolt cart operations.
"""

from .config import (
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
)

from .gemini import (
    call_gemini_api,
    parse_natural_language_intent,
    generate_ai_recipe,
    analyze_photo_with_vision,
    generate_ai_weekly_meal_plan,
    generate_ai_meal_swap,
)

from .pantry import (
    load_pantry_memory,
    save_pantry_memory,
    clear_pantry_memory,
    record_purchase_in_memory,
    display_pantry_memory,
    restock_pantry_from_detected_items,
    deduct_custom_ingredients,
)

from .planner import (
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
)

from .browser import (
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

__all__ = [
    # Config
    "BASE_DIR",
    "DATA_DIR",
    "USER_DATA_DIR",
    "PANTRY_MEMORY_FILE",
    "USER_PREFERENCES_FILE",
    "MEAL_PLAN_FILE",
    "DEFAULT_STORE",
    "DEFAULT_CITY",
    "DEFAULT_COUNTRY",
    "get_user_dir",
    "get_pantry_file",
    "get_meal_plan_file",
    "get_user_preferences_file",
    "get_user_browser_dir",
    "get_user_config",
    "save_user_config",
    "get_user_store_and_city",
    "list_active_users",
    "set_user_abort",
    "is_user_aborted",
    "load_user_preferences",
    "save_user_preferences",
    "is_item_allowed",
    "display_user_preferences",
    # Gemini
    "call_gemini_api",
    "parse_natural_language_intent",
    "generate_ai_recipe",
    "analyze_photo_with_vision",
    "generate_ai_weekly_meal_plan",
    "generate_ai_meal_swap",
    # Pantry
    "load_pantry_memory",
    "save_pantry_memory",
    "clear_pantry_memory",
    "record_purchase_in_memory",
    "display_pantry_memory",
    "restock_pantry_from_detected_items",
    "deduct_custom_ingredients",
    # Planner
    "DEFAULT_GROCERY_LIST",
    "SAMPLE_WEEKLY_GROCERY_LIST",
    "get_candidate_grocery_list",
    "generate_weekly_meal_plan",
    "generate_default_weekly_plan",
    "load_meal_plan",
    "save_meal_plan",
    "get_day_menu_formatted",
    "get_single_meal_formatted",
    "log_meal_consumption",
    # Browser
    "click_element_safely",
    "get_cart_total_price",
    "close_any_unwanted_modal",
    "get_store_search_input",
    "validate_item_match",
    "search_and_add_item",
    "add_items_to_cart",
    "inspect_store_items",
    "check_wolt_session",
    "login_mode",
]
