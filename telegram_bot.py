"""
Wolt Smart Pantry - Telegram Bot Bridge
Control your pantry memory, live store discovery, meal planning, and automated Wolt cart creation from your phone.
Supports multiple users with isolated pantry memory, weekly plans, separate persistent Wolt browser sessions,
and user-configurable cart checkout flows (1-click Auto-Pay vs Safe Review with strict Budget Limits).
"""

import os
import sys
import json
import logging
from logging.handlers import RotatingFileHandler
import asyncio
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

import pytz
from datetime import datetime, time

# Import local Wolt automation modules
from wolt_manager import (
    load_pantry_memory,
    save_pantry_memory,
    record_purchase_in_memory,
    clear_pantry_memory,
    load_user_preferences,
    save_user_preferences,
    is_item_allowed,
    inspect_store_items,
    add_items_to_cart,
    check_wolt_session,
    list_active_users,
    get_user_config,
    save_user_config,
    get_user_browser_dir,
    load_meal_plan,
    save_meal_plan,
    get_day_menu_formatted,
    get_single_meal_formatted,
    log_meal_consumption,
    analyze_photo_with_vision,
    deduct_custom_ingredients,
    restock_pantry_from_detected_items,
    generate_ai_recipe,
    generate_ai_meal_swap,
    generate_ai_weekly_meal_plan,
    generate_weekly_meal_plan,
    generate_default_weekly_plan,
    get_candidate_grocery_list,
    set_user_abort,
    is_user_aborted,
    parse_natural_language_intent,
    SAMPLE_WEEKLY_GROCERY_LIST
)

# Persistent Log File Path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "telegram_bot.log")

# Logging configuration (Rotating File + Optional Console)
log_handlers = [RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")]
if sys.stdout is not None:
    log_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=log_handlers
)
logger = logging.getLogger("TelegramBot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Config
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS_RAW = os.getenv("TELEGRAM_ALLOWED_USERS", "")
ALLOWED_USERS = [int(uid.strip()) for uid in ALLOWED_USERS_RAW.split(",") if uid.strip().isdigit()]
DEFAULT_STORE = os.getenv("DEFAULT_STORE", "wolt-market-maakri")
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "tallinn")
DEFAULT_COUNTRY = os.getenv("DEFAULT_COUNTRY", "est")
TIMEZONE_STR = os.getenv("TIMEZONE", "Europe/Tallinn")
try:
    BOT_TIMEZONE = pytz.timezone(TIMEZONE_STR)
except Exception:
    BOT_TIMEZONE = pytz.timezone("Europe/Tallinn")
DAILY_MENU_TIME = os.getenv("DAILY_MENU_TIME", "09:00")

# Temporary in-memory session cache for pending shopping proposals, photo meals, active tasks, and waiting prompt states
user_pending_plans = {}
user_pending_substitutions = {}
user_pending_photo_meal = {}
active_user_tasks = {}
user_waiting_state = {}

def get_user_store_and_city(user_id: int):
    """Retrieves the store venue, city, and country for a given user ID."""
    cfg = get_user_config(user_id)
    store = cfg.get("store") or DEFAULT_STORE
    city = cfg.get("city") or DEFAULT_CITY
    country = cfg.get("country") or DEFAULT_COUNTRY
    return store, city, country

def is_authorized(user_id: int) -> bool:
    """Checks if the given Telegram user ID is authorized."""
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS

def auth_guard(func):
    """Decorator to protect commands against unauthorized Telegram users."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id if update.effective_user else 0
        if not is_authorized(user_id):
            logger.warning(f"Unauthorized access attempt by user ID: {user_id}")
            if update.effective_message:
                await update.effective_message.reply_text("⛔ Unauthorized. Your Telegram User ID is not allowed to control this bot.")
            elif update.callback_query:
                try:
                    await update.callback_query.answer("⛔ Unauthorized.", show_alert=True)
                except Exception:
                    pass
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

async def reply_safe(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode="Markdown"):
    """Safely replies whether the trigger was a direct text command or an inline button callback, with Markdown parsing fallback."""
    target = None
    if update.callback_query and update.callback_query.message:
        target = update.callback_query.message
    elif update.effective_message:
        target = update.effective_message

    if target:
        try:
            return await target.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            logger.debug(f"reply_safe failed with parse_mode={parse_mode}: {e}. Retrying without formatting.")
            try:
                return await target.reply_text(text, parse_mode=None, reply_markup=reply_markup)
            except Exception as e2:
                logger.error(f"reply_safe unformatted fallback failed: {e2}")
                return None
    elif update.effective_chat:
        try:
            return await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception:
            try:
                return await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=None, reply_markup=reply_markup)
            except Exception as e2:
                logger.error(f"context.bot.send_message failed: {e2}")
                return None
    return None

async def edit_safe(msg, text: str, reply_markup=None, parse_mode="Markdown"):
    """Safely edits an existing message with Markdown parsing fallback."""
    if not msg:
        return None
    try:
        return await msg.edit_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        logger.debug(f"edit_safe failed with parse_mode={parse_mode}: {e}. Retrying without formatting.")
        try:
            return await msg.edit_text(text, parse_mode=None, reply_markup=reply_markup)
        except Exception as e2:
            logger.error(f"edit_safe unformatted fallback failed: {e2}")
            return None

async def query_edit_safe(query, text: str, reply_markup=None, parse_mode="Markdown"):
    """Safely edits a callback query's message with Markdown fallback."""
    if not query:
        return None
    try:
        return await query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    except Exception as e:
        logger.debug(f"query_edit_safe failed with parse_mode={parse_mode}: {e}. Retrying plain text.")
        try:
            return await query.edit_message_text(text, parse_mode=None, reply_markup=reply_markup)
        except Exception:
            if query.message:
                try:
                    return await query.message.reply_text(text, parse_mode=None, reply_markup=reply_markup)
                except Exception:
                    pass
            return None

def format_cart_result_message(res: dict, store: str, city: str, country: str, user_id: int):
    """Formats the cart assembly result, including added items, budget alerts, and pending substitutes."""
    final_p = res.get("final_price", 0.0)
    budget_exceeded = res.get("budget_exceeded", False)
    budget_limit = res.get("max_budget")
    paid_ok = res.get("auto_pay_success", False)
    pending_subs = res.get("pending_substitutions", [])
    added = res.get("added_items", [])
    wolt_url = f"https://wolt.com/en/{country}/{city}/venue/{store}"

    # 1. Substitutions detected -> Ask user to accept or reject
    if pending_subs:
        user_pending_substitutions[user_id] = pending_subs
        sub_lines = []
        for s in pending_subs:
            req = s.get("requested", "Item")
            fnd = s.get("found_title", "Substitute")
            pr = s.get("price", "")
            rsn = s.get("reason", "Diferencia de producto")
            pr_txt = f" ({pr})" if pr else ""
            sub_lines.append(f"• *Solicitado:* `{req}`\n  ➔ *Encontrado:* `{fnd}`{pr_txt}\n  _{rsn}_")
            
        added_txt = f"\n\n✅ *Productos exactos agregados:* {len(added)}" if added else ""
        
        response_text = (
            "⚠️ *Sustituto(s) Detectados para Aprobación:*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n\n".join(sub_lines) +
            f"{added_txt}\n"
            f"💶 *Total actual en carrito:* **{final_p:.2f} €**\n\n"
            "❓ *¿Deseas agregar estos sustitutos a tu pedido en Wolt o descartarlos?*"
        )
        keyboard = [
            [
                InlineKeyboardButton("✅ Aceptar Sustituto(s)", callback_data="btn_accept_substitutes"),
                InlineKeyboardButton("❌ Descartar Sustituto(s)", callback_data="btn_skip_substitutes")
            ],
            [
                InlineKeyboardButton("📱 Abrir Wolt Web / App", url=wolt_url)
            ]
        ]
        return response_text, InlineKeyboardMarkup(keyboard)

    # 2. Budget exceeded alert
    if budget_exceeded:
        response_text = (
            "🚨 *Budget Safety Alert!*\n\n"
            f"💶 *Cart Total:* **{final_p:.2f} €**\n"
            f"🚫 *Set Budget Limit:* **{budget_limit:.2f} €**\n\n"
            "⚠️ Auto-Pay was **automatically blocked** because the total exceeds your budget!\n"
            "📲 All items are ready in your cart. You can open your **Wolt mobile app** or browser to review before paying."
        )
        keyboard = [
            [InlineKeyboardButton("📱 Open Wolt App", url=wolt_url)],
            [InlineKeyboardButton("💾 Sync Pantry Memory", callback_data="btn_record_last")]
        ]
        return response_text, InlineKeyboardMarkup(keyboard)

    # 3. Auto-pay success
    if paid_ok:
        response_text = (
            "✅ *Order Successfully Placed & Paid on Wolt!*\n\n"
            f"💶 *Total Paid:* **{final_p:.2f} €**\n"
            "📦 Items recorded into your pantry memory for next week."
        )
        keyboard = [
            [InlineKeyboardButton("📦 View Pantry Inventory", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        return response_text, InlineKeyboardMarkup(keyboard)

    # 4. Standard cart ready & synchronized
    response_text = (
        "🎉 *Cart Ready & Synchronized!*\n\n"
        f"💶 *Cart Total:* **{final_p:.2f} €**\n\n"
        "📲 *Wolt has synced your cart to your phone!* Open your **Wolt mobile app** to review items and pay with 1 tap.\n\n"
        "👇 Once placed, tap below to sync your virtual pantry memory:"
    )
    keyboard = [
        [InlineKeyboardButton("📱 Open Wolt App / Web", url=wolt_url)],
        [InlineKeyboardButton("💾 Confirm Order Placed (Sync Memory)", callback_data="btn_record_last")]
    ]
    return response_text, InlineKeyboardMarkup(keyboard)

@auth_guard
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message and interactive main menu."""
    user = update.effective_user
    user_id = user.id if user else 0
    store, city, _ = get_user_store_and_city(user_id)
    cfg = get_user_config(user_id)
    auto_pay_active = cfg.get("auto_pay", False)
    budget_limit = cfg.get("max_budget")
    budget_txt = f"{budget_limit:.2f} €" if budget_limit else "No Limit (∞)"
    
    welcome_text = (
        f"👋 Hello {user.first_name if user else 'there'}!\n\n"
        "🛒 *Wolt Smart Pantry Bot* is online.\n"
        f"👤 *Profile ID:* `{user_id}`\n"
        f"🏬 *Venue:* `{store}` ({city.capitalize()})\n"
        f"💳 *Checkout Flow:* `{'⚡ 1-Click Auto-Pay' if auto_pay_active else '🛡️ Safe Review & Sync'}`\n"
        f"💶 *Cart Budget:* `{budget_txt}`\n\n"
        "Here is what you can do:\n"
        "• 🌅 `/today` (or `/menu`) - Today's meals, portions & chef tips\n"
        "• 📅 `/week [sugerencias]` - Browse 7-day schedule (or re-plan with suggestions)\n"
        "• 📸 *Send a photo* of meals or groceries with `/stock`\n"
        "• `/plan [sugerencias]` - Generate 7-day meal plan & Wolt cart with custom suggestions\n"
        "• `/settings` (or `/cartflow`) - Configure Auto-Pay & Budget Limits\n"
        "• `/budget <amount>` - Set max cart budget (e.g. `/budget 50`)\n"
        "• `/pref` - Configure allergies, avoided foods & diet type\n"
        "• `/pantry` - View virtual pantry memory & staples\n"
        "• `/deals` - Explore live discounts on Wolt\n"
        "• `/cart <items>` - Build cart directly (e.g. `/cart Banaan:6`)\n"
        "• 👤 `/user` - View your profile, private API keys & settings\n"
        "• `/wolt` - Inspect your isolated Wolt browser session\n"
        "• `/help` - View complete command guide\n\n"
        f"⏰ *Morning Schedule:* Daily menu arrives automatically at `{DAILY_MENU_TIME}` ({BOT_TIMEZONE.zone})."
    )
    keyboard = [
        [
            InlineKeyboardButton("🌅 Today's Menu", callback_data="btn_today_menu"),
            InlineKeyboardButton("📅 Full Week Plan", callback_data="btn_week_plan")
        ],
        [
            InlineKeyboardButton("📋 Generate Plan", callback_data="btn_plan"),
            InlineKeyboardButton("✨ Plan con Sugerencias", callback_data="btn_prompt_plan_suggestions")
        ],
        [
            InlineKeyboardButton("🏷️ Active Deals", callback_data="btn_deals"),
            InlineKeyboardButton("🛒 Custom Cart", callback_data="btn_prompt_cart")
        ],
        [
            InlineKeyboardButton("👨‍🍳 Ask Recipe", callback_data="btn_prompt_recipe"),
            InlineKeyboardButton("⚙️ Cart & Budget", callback_data="btn_settings")
        ],
        [
            InlineKeyboardButton("👤 Mi Perfil y Keys", callback_data="btn_user"),
            InlineKeyboardButton("🏠 Pantry Memory", callback_data="btn_pantry")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_safe(update, context, welcome_text, reply_markup=reply_markup)

@auth_guard
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed command reference."""
    help_text = (
        "📖 *Command Guide:*\n\n"
        "• `/user` (or `/profile`) - View your profile, private API keys status, budget, and active settings.\n"
        "• `/today` (or `/menu`) - Today's scheduled meals, raw-to-cooked portions & freshness reminders.\n"
        "• `/week [sugerencias]` - 7-day full weekly meal schedule. Pass suggestions to generate a new plan (e.g. `/week platos italianos y más salmón`).\n"
        "• `/plan [sugerencias]` - Audits pantry memory and generates a 7-day meal plan & Wolt cart with optional suggestions (e.g. `/plan comida mexicana alta en proteína`).\n"
        "• `/settings` (or `/cartflow`) - Configure Auto-Pay flow, budget limits & store venues.\n"
        "• `/budget <amount>` - Set maximum cart budget (e.g. `/budget 50` or `/budget 0` to disable).\n"
        "• `/autopay [on|off]` - Toggle automated payment submission.\n"
        "• `/pref` - Manage your allergies, disliked ingredients, and household size.\n"
        "• `/pantry` - Shows active long-term staples (onions, oils, spices) and recent stock.\n"
        "• `/stock` - Restock items manually (`/stock eggs 10`) or upload a photo with caption `/stock`.\n"
        "• `/deals` - Scans Wolt Market for active promotional discounts.\n"
        "• `/cart Item:Qty Item:Qty` - Adds specific items directly (e.g. `/cart Banaan:6 Rukola:1`).\n"
        "• `/wolt` - Check your user's isolated Wolt session status.\n"
        "• `/store <slug> [city]` - Set your preferred Wolt store venue.\n"
        "• `/setkey <api_key>` - Set your private Gemini API key.\n"
        "• `/stop` - Abort active cart creation, scan, or proposal.\n"
        "• `/logs [lines]` - View live execution logs (default 25 lines).\n\n"
        "💬 *Natural Language Supported!*\n"
        "You can simply talk to the bot in plain English or Spanish (e.g., _'Quiero un plan con comida mexicana y alta en proteína'_, _'What should I eat for lunch?'_, _'Buy 6 bananas and arugula on Wolt'_, _'Set budget to 50 euros'_).\n\n"
        "🛡️ *Budget & Auto-Pay Protection:*\n"
        "If Auto-Pay is enabled, the bot checks your configured budget before placing the order. If the cart total exceeds your budget, **Auto-Pay is automatically blocked** and the cart is kept open for manual review in your phone app!"
    )
    await reply_safe(update, context, help_text)

@auth_guard
async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Interactive settings panel for Cart Flow, Auto-Pay, Budget Limits, and Store Configuration."""
    user_id = update.effective_user.id if update.effective_user else 0
    cfg = get_user_config(user_id)
    store, city, country = get_user_store_and_city(user_id)
    prefs = load_user_preferences(user_id=user_id)
    
    auto_pay_active = cfg.get("auto_pay", False)
    budget_limit = cfg.get("max_budget")
    budget_txt = f"{budget_limit:.2f} €" if budget_limit else "No Limit (∞)"
    
    status_text = (
        "⚙️ *Cart Flow & Budget Configuration:*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 *User Profile ID:* `{user_id}`\n\n"
        f"💳 *Checkout Mode:* `{'⚡ 1-Click Auto-Pay' if auto_pay_active else '🛡️ Safe Review (Sync to Wolt App)'}`\n"
        f"💶 *Max Cart Budget:* `{budget_txt}`\n"
        f"🏬 *Store Venue:* `{store}` ({city.capitalize()}, {country.upper()})\n"
        f"👥 *Household:* `{prefs.get('household_size', 1)} person(s)` | *Diet:* `{prefs.get('diet_type', 'omnivore').capitalize()}`\n\n"
        "🛡️ *Safety Rule:* If Auto-Pay is ON and the cart total exceeds your budget, automated payment is instantly blocked and sent to your phone for safe manual review.\n\n"
        "👇 *Tap a button below to configure your preferences:*"
    )
    
    autopay_btn_txt = "🔴 Turn Auto-Pay OFF (Safe Review)" if auto_pay_active else "🟢 Turn Auto-Pay ON (1-Click)"
    
    keyboard = [
        [InlineKeyboardButton(autopay_btn_txt, callback_data="btn_toggle_autopay")],
        [
            InlineKeyboardButton("💶 40 €", callback_data="btn_set_budget_40"),
            InlineKeyboardButton("💶 50 €", callback_data="btn_set_budget_50"),
            InlineKeyboardButton("💶 65 €", callback_data="btn_set_budget_65"),
            InlineKeyboardButton("💶 80 €", callback_data="btn_set_budget_80")
        ],
        [
            InlineKeyboardButton("✏️ Custom Budget", callback_data="btn_prompt_budget"),
            InlineKeyboardButton("🚫 No Budget Limit", callback_data="btn_set_budget_0")
        ],
        [
            InlineKeyboardButton("🏬 Change Store", callback_data="btn_prompt_store"),
            InlineKeyboardButton("🛍️ Wolt Session", callback_data="btn_wolt_status")
        ],
        [
            InlineKeyboardButton("👤 Dietary Profile", callback_data="btn_pref"),
            InlineKeyboardButton("📋 Generate Plan", callback_data="btn_plan")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_safe(update, context, status_text, reply_markup=reply_markup)

@auth_guard
async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sets user max cart budget limit: /budget <amount> or /budget 0 to disable"""
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args if context.args else []
    cfg = get_user_config(user_id)
    
    if not args:
        curr_b = cfg.get("max_budget")
        b_txt = f"{curr_b:.2f} €" if curr_b else "No Limit (∞)"
        msg = (
            f"💶 *Current Cart Budget Limit:* `{b_txt}`\n\n"
            "💡 *How to update:*\n"
            "• `/budget 50` (sets budget to 50.00 €)\n"
            "• `/budget 65.50`\n"
            "• `/budget 0` (removes budget limit)\n"
            "• Or use `/settings` for 1-tap buttons."
        )
        await reply_safe(update, context, msg)
        return
        
    val_str = args[0].replace(",", ".").replace("€", "").strip()
    try:
        val = float(val_str)
        if val <= 0:
            cfg["max_budget"] = None
            save_user_config(cfg, user_id=user_id)
            await reply_safe(update, context, "✅ *Cart Budget Limit Removed.* There is now no price ceiling before payment.")
        else:
            cfg["max_budget"] = val
            save_user_config(cfg, user_id=user_id)
            await reply_safe(update, context, f"✅ *Cart Budget Limit Set:* **{val:.2f} €**\nIf any grocery order exceeds this total, Auto-Pay will be automatically blocked for your safety.")
    except ValueError:
        await reply_safe(update, context, "⚠️ Invalid amount. Usage: `/budget 50` or `/budget 0` to disable.")

@auth_guard
async def autopay_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Enables or disables auto-pay mode: /autopay [on|off]"""
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args if context.args else []
    cfg = get_user_config(user_id)
    
    if not args:
        curr = cfg.get("auto_pay", False)
        status_txt = "🟢 ENABLED (1-Click Auto-Pay)" if curr else "🛡️ DISABLED (Safe Review Mode)"
        await reply_safe(update, context, f"💳 *Auto-Pay Status:* `{status_txt}`\n\nTo change: `/autopay on` or `/autopay off`")
        return
        
    arg = args[0].lower()
    if arg in ["on", "true", "enable", "yes", "1"]:
        cfg["auto_pay"] = True
        save_user_config(cfg, user_id=user_id)
        await reply_safe(update, context, "⚡ *Auto-Pay ENABLED!* Building carts will automatically submit payment on Wolt (subject to your budget limit).")
    elif arg in ["off", "false", "disable", "no", "0"]:
        cfg["auto_pay"] = False
        save_user_config(cfg, user_id=user_id)
        await reply_safe(update, context, "🛡️ *Auto-Pay DISABLED!* Safe Review mode is active. Carts will be built and synced to your phone app without charging your card.")
    else:
        await reply_safe(update, context, "Usage: `/autopay on` or `/autopay off`")

@auth_guard
async def stop_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aborts any running cart automation, store scanning, or pending proposals: /stop"""
    user_id = update.effective_user.id if update.effective_user else 0
    
    # 1. Signal background browser automation loops to break immediately
    set_user_abort(user_id, abort=True)
    
    # 2. Cancel active asyncio task if running
    task = active_user_tasks.pop(user_id, None)
    if task and not task.done():
        task.cancel()
        
    # 3. Clear pending in-memory proposals & meal photos
    user_pending_plans.pop(user_id, None)
    user_pending_photo_meal.pop(user_id, None)
    
    await reply_safe(
        update, 
        context, 
        "🛑 *Operation Aborted!*\n\nAny active Wolt cart creation, store scanning, or pending proposals have been stopped."
    )

@auth_guard
async def recipe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates a gourmet step-by-step AI chef recipe using in-stock ingredients: /recipe [dish or ingredients]"""
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args if context.args else []
    plan = load_meal_plan(user_id=user_id)
    days = plan.get("days", [])
    now = datetime.now()
    day_idx = min(6, max(0, now.weekday()))
    day_data = days[day_idx] if days and day_idx < len(days) else {}
    
    dish_name = "Today's Dish"
    ingredients = []
    
    if args:
        arg_str = " ".join(args).strip().lower()
        if arg_str in ["breakfast", "brekkie", "desayuno"]:
            meal = day_data.get("breakfast", {})
            dish_name = meal.get("title", "Breakfast")
            ingredients = meal.get("ingredients", ["Eggs", "Toast", "Avocado", "Butter"])
        elif arg_str in ["lunch", "almuerzo"]:
            meal = day_data.get("lunch", {})
            dish_name = meal.get("title", "Lunch")
            ingredients = meal.get("ingredients", ["Chicken fillet", "Rice", "Bell peppers", "Soy sauce"])
        elif arg_str in ["dinner", "cena"]:
            meal = day_data.get("dinner", {})
            dish_name = meal.get("title", "Dinner")
            ingredients = meal.get("ingredients", ["Protein", "Vegetables", "Pasta", "Garlic"])
        elif arg_str in ["snack"]:
            meal = day_data.get("snack", {})
            dish_name = meal.get("title", "Healthy Snack")
            ingredients = meal.get("ingredients", ["Banana", "Dark chocolate"])
        else:
            dish_name = "Custom Chef Creation"
            ingredients = [a.strip() for a in " ".join(args).split(",") if a.strip()]
    else:
        meal = day_data.get("lunch", {})
        dish_name = meal.get("title", "Today's Lunch")
        ingredients = meal.get("ingredients", ["Chicken", "Rice", "Peppers"])
        
    msg = await reply_safe(update, context, f"👨‍🍳 *Chef AI is writing a step-by-step gourmet recipe for:* `{dish_name}`...")
    
    recipe_text = await asyncio.to_thread(generate_ai_recipe, dish_name, ingredients, None, None, user_id)
    
    keyboard = [
        [
            InlineKeyboardButton("🍳 Breakfast Recipe", callback_data="btn_recipe_breakfast"),
            InlineKeyboardButton("🥗 Lunch Recipe", callback_data="btn_recipe_lunch"),
            InlineKeyboardButton("🍲 Dinner Recipe", callback_data="btn_recipe_dinner")
        ],
        [
            InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu"),
            InlineKeyboardButton("📦 Pantry Stock", callback_data="btn_pantry")
        ]
    ]
    if msg:
        await edit_safe(msg, recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await reply_safe(update, context, recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's scheduled meal plan, chef tips, portions, and freshness status."""
    user_id = update.effective_user.id if update.effective_user else 0
    text, day_data = get_day_menu_formatted(user_id=user_id)
    keyboard = [
        [
            InlineKeyboardButton("🍳 Breakfast", callback_data="btn_view_breakfast"),
            InlineKeyboardButton("🥗 Lunch", callback_data="btn_view_lunch"),
            InlineKeyboardButton("🍲 Dinner", callback_data="btn_view_dinner")
        ],
        [
            InlineKeyboardButton("👨‍🍳 Step-by-Step AI Recipe", callback_data="btn_recipe_lunch"),
            InlineKeyboardButton("🔄 Swap Meal", callback_data="btn_swap_meal")
        ],
        [
            InlineKeyboardButton("✅ Log Breakfast", callback_data="btn_eat_breakfast"),
            InlineKeyboardButton("✅ Log Lunch", callback_data="btn_eat_lunch"),
            InlineKeyboardButton("✅ Log Dinner", callback_data="btn_eat_dinner")
        ],
        [
            InlineKeyboardButton("📅 Full Week Plan", callback_data="btn_week_plan"),
            InlineKeyboardButton("📦 Pantry Stock", callback_data="btn_pantry")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_safe(update, context, text, reply_markup=reply_markup)

@auth_guard
async def breakfast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's breakfast with exact food quantities, weights, and chef tips."""
    user_id = update.effective_user.id if update.effective_user else 0
    text, meal = get_single_meal_formatted("breakfast", user_id=user_id)
    keyboard = [
        [
            InlineKeyboardButton("👨‍🍳 Full AI Cooking Recipe", callback_data="btn_recipe_breakfast"),
            InlineKeyboardButton("✅ Log Eaten (Deduct)", callback_data="btn_eat_breakfast")
        ],
        [InlineKeyboardButton("🥗 View Lunch", callback_data="btn_view_lunch"), InlineKeyboardButton("🌅 Full Day Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def lunch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's lunch with raw-to-cooked shrinkage math, exact ingredients & tips."""
    user_id = update.effective_user.id if update.effective_user else 0
    text, meal = get_single_meal_formatted("lunch", user_id=user_id)
    keyboard = [
        [
            InlineKeyboardButton("👨‍🍳 Full AI Cooking Recipe", callback_data="btn_recipe_lunch"),
            InlineKeyboardButton("✅ Log Eaten (Deduct)", callback_data="btn_eat_lunch")
        ],
        [InlineKeyboardButton("🍲 View Dinner", callback_data="btn_view_dinner"), InlineKeyboardButton("🌅 Full Day Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def dinner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's dinner with exact ingredients & chef tips."""
    user_id = update.effective_user.id if update.effective_user else 0
    text, meal = get_single_meal_formatted("dinner", user_id=user_id)
    keyboard = [
        [
            InlineKeyboardButton("👨‍🍳 Full AI Cooking Recipe", callback_data="btn_recipe_dinner"),
            InlineKeyboardButton("✅ Log Eaten (Deduct)", callback_data="btn_eat_dinner")
        ],
        [InlineKeyboardButton("🍎 View Snack", callback_data="btn_view_snack"), InlineKeyboardButton("🌅 Full Day Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def snack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's snack with exact food breakdown."""
    user_id = update.effective_user.id if update.effective_user else 0
    text, meal = get_single_meal_formatted("snack", user_id=user_id)
    keyboard = [
        [InlineKeyboardButton("✅ Log Snack Eaten", callback_data="btn_eat_snack")],
        [InlineKeyboardButton("🌅 Full Day Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def eat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logs a meal as consumed, deducts items from pantry memory, and displays the food used."""
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args if context.args else []
    meal_type = args[0].lower() if args else "lunch"
    if meal_type not in ["breakfast", "lunch", "dinner", "snack"]:
        meal_type = "lunch"
    text, used = log_meal_consumption(meal_type, user_id=user_id)
    keyboard = [
        [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
        [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE, suggestions: str = None):
    """Displays the full 7-day meal plan breakdown, or generates a fresh one if suggestions are provided."""
    user_id = update.effective_user.id if update.effective_user else 0
    
    if suggestions is None and context and context.args:
        suggestions = " ".join(context.args).strip()
        
    msg = None
    if suggestions:
        msg = await reply_safe(update, context, f"📐 Generando plan semanal de 7 días adaptado a tus sugerencias:\n_{suggestions}_...")
        prefs = load_user_preferences(user_id=user_id)
        plan = await asyncio.to_thread(
            generate_weekly_meal_plan,
            prefs=prefs,
            user_id=user_id,
            user_suggestions=suggestions
        )
        save_meal_plan(plan, user_id=user_id)
    else:
        plan = load_meal_plan(user_id=user_id)
        
    days = plan.get("days", [])
    h_size = plan.get("household_size", 1)
    applied_sug = plan.get("user_suggestions")
    sug_txt = f"💡 *Sugerencias aplicadas:* _{applied_sug}_\n\n" if applied_sug else "\n"
    
    text = (
        f"📅 *7-DAY SMART MEAL PLAN*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 *Household:* {h_size} person(s) | *Diet:* {plan.get('diet_type', 'Omnivore').capitalize()}\n"
        f"{sug_txt}"
    )
    for d in days:
        text += (
            f"📍 *{d.get('day_name', 'Day')}* — _{d.get('freshness_tier', 'Standard')}_\n"
            f"• 🍳 *Breakfast:* {d.get('breakfast', {}).get('title', 'Egg & Toast Scramble')}\n"
            f"• 🥗 *Lunch:* {d['lunch']['title']} ({d['lunch'].get('protein_raw', '')})\n"
            f"• 🍲 *Dinner:* {d['dinner']['title']} ({d['dinner'].get('protein_raw', '')})\n"
            f"• 🍎 *Snack:* {d['snack']['title']}\n"
            f"• 💡 _{d.get('freshness_alert', '')}_\n\n"
        )
    
    keyboard = [
        [
            InlineKeyboardButton("✨ Plan con Sugerencias", callback_data="btn_prompt_plan_suggestions"),
            InlineKeyboardButton("🔄 Regenerar Plan", callback_data="btn_plan")
        ],
        [
            InlineKeyboardButton("🌅 Today's Menu", callback_data="btn_today_menu"),
            InlineKeyboardButton("🛒 Build Wolt Cart", callback_data="btn_plan")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if msg:
        await edit_safe(msg, text, reply_markup=reply_markup)
    else:
        await reply_safe(update, context, text, reply_markup=reply_markup)

async def daily_morning_menu_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled task that runs every morning around 09:00 to deliver individualized meal plans to active users."""
    logger.info("🌅 Executing daily morning menu broadcast job...")
    keyboard = [
        [
            InlineKeyboardButton("🔄 Swap Today's Meal", callback_data="btn_swap_meal"),
            InlineKeyboardButton("📅 Full Week Plan", callback_data="btn_week_plan")
        ],
        [
            InlineKeyboardButton("🛒 Wolt Grocery List", callback_data="btn_plan"),
            InlineKeyboardButton("📦 Pantry Status", callback_data="btn_pantry")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    active_users = set(list_active_users() + (ALLOWED_USERS if ALLOWED_USERS else []))
    if not active_users:
        logger.warning("No active users found for morning menu broadcast.")
        return
        
    for user_id in active_users:
        try:
            text, day_data = get_day_menu_formatted(user_id=user_id)
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            logger.info(f"Daily morning menu sent to user ID: {user_id}")
        except Exception as e:
            logger.error(f"Failed to send daily menu to user ID {user_id}: {e}")

@auth_guard
async def preferences_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manages user dietary profile, allergies, and avoided ingredients."""
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args if context.args else []
    prefs = load_user_preferences(user_id=user_id)

    if args:
        subcmd = args[0].lower()
        val = " ".join(args[1:])
        
        if subcmd in ["allergy", "allergies"]:
            new_allergies = [a.strip() for a in val.split(",") if a.strip()]
            prefs["allergies"] = new_allergies
            save_user_preferences(prefs, user_id=user_id)
            await reply_safe(update, context, f"✅ *Allergies Updated:* {', '.join(new_allergies) if new_allergies else 'None'}")
            return
        elif subcmd in ["avoid", "avoided", "dislike", "dislikes"]:
            new_avoid = [a.strip() for a in val.split(",") if a.strip()]
            prefs["avoided_ingredients"] = new_avoid
            save_user_preferences(prefs, user_id=user_id)
            await reply_safe(update, context, f"✅ *Avoided Foods Updated:* {', '.join(new_avoid) if new_avoid else 'None'}")
            return
        elif subcmd == "diet":
            prefs["diet_type"] = val.strip().lower()
            save_user_preferences(prefs, user_id=user_id)
            await reply_safe(update, context, f"✅ *Diet Type Set To:* {val.strip().capitalize()}")
            return
        elif subcmd in ["people", "household", "size"]:
            if val.strip().isdigit():
                prefs["household_size"] = max(1, int(val.strip()))
                save_user_preferences(prefs, user_id=user_id)
                await reply_safe(update, context, f"✅ *Household Size Set To:* {prefs['household_size']} person(s)")
                return
        elif subcmd in ["reset", "clear"]:
            prefs = {
                "diet_type": "omnivore",
                "allergies": [],
                "avoided_ingredients": [],
                "preferred_proteins": ["chicken", "ground beef", "salmon", "eggs"],
                "household_size": 1,
                "notes": ""
            }
            save_user_preferences(prefs, user_id=user_id)
            await reply_safe(update, context, "🔄 Dietary preferences reset to standard default.")
            return

    # Show current preferences
    text = (
        "👤 *Your Dietary Profile & Preferences:*\n\n"
        f"• *Diet Type:* `{prefs.get('diet_type', 'omnivore').capitalize()}`\n"
        f"• *Household Size:* `{prefs.get('household_size', 1)} person(s)`\n"
        f"• *Allergies:* `{', '.join(prefs.get('allergies', [])) if prefs.get('allergies') else 'None recorded'}`\n"
        f"• *Avoided Foods:* `{', '.join(prefs.get('avoided_ingredients', [])) if prefs.get('avoided_ingredients') else 'None recorded'}`\n"
        f"• *Preferred Proteins:* `{', '.join(prefs.get('preferred_proteins', [])) if prefs.get('preferred_proteins') else 'Standard'}`\n\n"
        "💡 *How to update from Telegram:*\n"
        "• `/pref allergy peanuts, shellfish, lactose`\n"
        "• `/pref avoid pork, mushrooms, eggplant`\n"
        "• `/pref diet high-protein` _(or pescatarian, vegetarian, vegan)_\n"
        "• `/pref people 2`\n"
        "• `/pref reset`"
    )
    keyboard = [
        [
            InlineKeyboardButton("🥜 Set Allergies", callback_data="btn_prompt_allergy"),
            InlineKeyboardButton("🚫 Avoided Foods", callback_data="btn_prompt_avoid")
        ],
        [
            InlineKeyboardButton("👥 Household Size", callback_data="btn_prompt_household"),
            InlineKeyboardButton("🔄 Reset Preferences", callback_data="btn_reset_pref")
        ],
        [
            InlineKeyboardButton("📋 Generate Plan", callback_data="btn_plan"),
            InlineKeyboardButton("🏠 Pantry Status", callback_data="btn_pantry")
        ]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def pantry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays virtual pantry memory status."""
    user_id = update.effective_user.id if update.effective_user else 0
    data = load_pantry_memory(user_id=user_id)
    staples = data.get("staples", [])
    proteins = data.get("proteins", [])
    produce = data.get("produce", [])
    history = data.get("purchase_history", [])

    text = f"🏠 *Virtual Pantry Memory (User {user_id}):*\n\n"
    
    text += "📦 *Long-Term Staples (No Repurchase Needed):*\n"
    if staples:
        for s in staples:
            text += f"• `{s['name']}` (Qty: {s.get('qty', 1)}, ~{s.get('estimated_weeks', 4)} wks left)\n"
    else:
        text += "• _No staples recorded yet._\n"

    text += "\n🥩 *Tracked Proteins:*\n"
    if proteins:
        for p in proteins:
            text += f"• `{p['name']}` (Qty: {p.get('qty', 1)})\n"
    else:
        text += "• _None currently in stock._\n"

    if produce:
        text += "\n🥑 *Tracked Produce:*\n"
        for pr in produce:
            text += f"• `{pr['name']}` (Qty: {pr.get('qty', 1)})\n"

    text += f"\n📜 *Order History:* {len(history)} past orders recorded.\n"
    text += f"🕒 *Last Sync:* `{data.get('last_updated', 'N/A')}`"

    keyboard = [
        [InlineKeyboardButton("📋 Plan Week Based on Memory", callback_data="btn_plan")],
        [InlineKeyboardButton("🗑️ Clear Memory", callback_data="btn_clear_pantry")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def deals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches live deals from the default or user-configured Wolt venue."""
    user_id = update.effective_user.id if update.effective_user else 0
    store, city, country = get_user_store_and_city(user_id)
    msg = await reply_safe(update, context, f"🔍 Scanning live discounts on Wolt ({store})...")
    
    try:
        results = await asyncio.to_thread(
            inspect_store_items,
            store,
            queries=None,
            get_deals=True,
            city=city,
            country=country,
            user_id=user_id
        )
        deals = results.get("deals", [])
        if deals:
            text = f"🔥 *Top Active Deals in {store}:*\n\n"
            for d in deals[:10]:
                orig = f" ~{d['original_price']}~" if d.get('original_price') else ""
                text += f"• *{d['title']}*: `{d['price']}`{orig}\n"
            text += "\n_Use `/plan` to automatically incorporate deals into your meal plan._"
        else:
            text = f"ℹ️ No promotional deal badges detected on {store} right now."
    except Exception as e:
        logger.error(f"Error scanning deals: {e}", exc_info=True)
        text = f"⚠️ Could not scan deals: {e}"

    if msg:
        await edit_safe(msg, text, parse_mode="Markdown")
    else:
        await reply_safe(update, context, text)

@auth_guard
async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE, suggestions: str = None):
    """Generates a weekly meal plan and itemized grocery list customized for user preferences and optional suggestions."""
    user_id = update.effective_user.id if update.effective_user else 0
    
    if suggestions is None and context and context.args:
        suggestions = " ".join(context.args).strip()
        
    sug_notice = f" con sugerencias: _{suggestions}_" if suggestions else ""
    msg = await reply_safe(update, context, f"📐 Calculando plan de 7 días adaptado a tu perfil{sug_notice}...")
    
    prefs = load_user_preferences(user_id=user_id)
    cfg = get_user_config(user_id)
    household_multiplier = prefs.get("household_size", 1)
    diet = prefs.get("diet_type", "omnivore").lower()
    auto_pay_active = cfg.get("auto_pay", False)
    budget_limit = cfg.get("max_budget")
    budget_txt = f"{budget_limit:.2f} €" if budget_limit else "No Limit"

    # Candidate shopping list tailored by diet, allergies, household size, and breakfast
    filtered_items = get_candidate_grocery_list(prefs, user_id=user_id)

    # Synchronize and save the meal plan for these exact items with suggestions
    new_plan = await asyncio.to_thread(
        generate_weekly_meal_plan,
        inventory_items=filtered_items,
        prefs=prefs,
        user_id=user_id,
        user_suggestions=suggestions
    )
    save_meal_plan(new_plan, user_id=user_id)
    user_pending_plans[user_id] = filtered_items

    sug_header = f"\n💡 *Sugerencias aplicadas:* _{suggestions}_\n" if suggestions else ""

    plan_text = (
        f"📋 *Proposed 7-Day Meal Plan ({diet.capitalize()} / {household_multiplier} person(s)):*\n"
        f"{sug_header}\n"
        "• *🍳 Daily Breakfasts:* 3-egg scrambles with avocado & toast, mozzarella & tomato omelettes\n"
        "• *Days 1–3 (Tier 1 Fresh):* High-protein main meals (fresh ground beef, chicken cuts, delicate arugula)\n"
        "• *Days 4–5 (Tier 2 Medium):* Sautéed chicken breast, sweet bell peppers & baby potatoes\n"
        "• *Days 6–7 (Tier 3 Hardy):* Roasted vegetables, frittata & mozzarella pasta bake\n\n"
        "🛒 *Itemized Grocery List:*\n"
    )
    for it_name, it_qty in filtered_items:
        plan_text += f"• `{it_name}` × {it_qty}\n"

    plan_text += f"\n⚙️ *Configured Flow:* `{'⚡ 1-Click Auto-Pay' if auto_pay_active else '🛡️ Safe Review'}` (Budget: `{budget_txt}`)\n"
    plan_text += "👇 *Ready to build this cart on Wolt?*"

    keyboard = [
        [
            InlineKeyboardButton("🛒 Build Cart on Wolt", callback_data="btn_confirm_cart"),
        ],
        [
            InlineKeyboardButton("✨ Plan con Sugerencias", callback_data="btn_prompt_plan_suggestions"),
            InlineKeyboardButton("📅 Ver Semana Completa", callback_data="btn_week_plan")
        ],
        [
            InlineKeyboardButton("⚡ Auto Pay & Order", callback_data="btn_confirm_autopay"),
            InlineKeyboardButton("⚙️ Settings", callback_data="btn_settings")
        ],
        [
            InlineKeyboardButton("❌ Cancel", callback_data="btn_cancel_plan")
        ]
    ]
    if msg:
        await edit_safe(msg, plan_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await reply_safe(update, context, plan_text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def cart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct cart assembly command: /cart Item:Qty Item:Qty"""
    user_id = update.effective_user.id if update.effective_user else 0
    store, city, country = get_user_store_and_city(user_id)
    cfg = get_user_config(user_id)
    auto_pay_pref = cfg.get("auto_pay", False)
    budget_limit = cfg.get("max_budget")
    
    args = context.args if context.args else []
    if not args:
        await reply_safe(update, context, "Usage: `/cart Banaan:6 Rukola:1 Kollane sibul:1`")
        return

    items = []
    for it in args:
        if ":" in it:
            parts = it.rsplit(":", 1)
            name = parts[0].strip()
            qty = int(parts[1].strip()) if parts[1].strip().isdigit() else 1
            items.append((name, qty))
        else:
            items.append((it.strip(), 1))

    msg = await reply_safe(update, context, f"🛒 Launching browser automation for {len(items)} items on {store}...")
    
    try:
        res = await asyncio.to_thread(
            add_items_to_cart,
            store,
            items,
            city=city,
            country=country,
            keep_open=True,
            record_memory=False,
            auto_pay=auto_pay_pref,
            user_id=user_id,
            budget=budget_limit
        )
        user_pending_plans[user_id] = items
        response_text, reply_markup = format_cart_result_message(res, store, city, country, user_id)
        if msg:
            await edit_safe(msg, response_text, reply_markup=reply_markup)
        else:
            await reply_safe(update, context, response_text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Cart build failed: {e}", exc_info=True)
        if msg:
            await edit_safe(msg, f"⚠️ Cart build failed: {e}")
        else:
            await reply_safe(update, context, f"⚠️ Cart build failed: {e}")

@auth_guard
async def wolt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inspects the user's isolated Wolt session and store settings: /wolt"""
    user_id = update.effective_user.id if update.effective_user else 0
    store, city, country = get_user_store_and_city(user_id)
    profile_dir = get_user_browser_dir(user_id)
    wolt_url = f"https://wolt.com/en/{country}/{city}/venue/{store}"
    
    msg = await reply_safe(update, context, f"🔍 Inspecting Wolt session for User `{user_id}`...")
    res = await asyncio.to_thread(check_wolt_session, user_id)
    
    logged_in = res.get("logged_in", False)
    status_icon = "✅ Logged In / Active Session" if logged_in else "⚠️ No Active Login Detected"
    
    text = (
        f"🛍️ *Wolt Profile Status (User ID: `{user_id}`)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"• *Status:* {status_icon}\n"
        f"• *Venue:* `{store}` ({city.capitalize()}, {country.upper()})\n"
        f"• *Browser Profile:* `{os.path.basename(profile_dir)}`\n\n"
        "💡 *Tips:*\n"
        "• Change store venue: `/store <store-slug> [city]`\n"
        "• Configure Auto-Pay & Budget: `/settings`\n"
        "• When cart is built, items automatically sync to your Wolt phone app!"
    )
    keyboard = [
        [InlineKeyboardButton("📱 Open Wolt Store Web", url=wolt_url)],
        [InlineKeyboardButton("🛒 Test Sample Cart", callback_data="btn_sample_cart")]
    ]
    if msg:
        await edit_safe(msg, text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def store_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configures user-specific Wolt store venue and city: /store <store-slug> [city]"""
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args if context.args else []
    if not args:
        store, city, country = get_user_store_and_city(user_id)
        await reply_safe(update, context, f"🏬 *Your Current Wolt Store:* `{store}` in `{city}` ({country})\n\nTo change: `/store wolt-market-maakri tallinn`")
        return
        
    new_store = args[0].strip()
    new_city = args[1].strip().lower() if len(args) > 1 else DEFAULT_CITY
    
    cfg = get_user_config(user_id)
    cfg["store"] = new_store
    cfg["city"] = new_city
    save_user_config(cfg, user_id=user_id)
    
    await reply_safe(update, context, f"✅ *Store Venue Updated:*\n• Store: `{new_store}`\n• City: `{new_city}`\n\nAll subsequent `/deals`, `/plan`, and `/cart` commands will use this venue.")

@auth_guard
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends clean, high-level execution activity status to Telegram without verbose internal details."""
    lines_count = 15
    if context.args and context.args[0].isdigit():
        lines_count = min(50, max(5, int(context.args[0])))

    if not os.path.exists(LOG_FILE):
        await reply_safe(update, context, "ℹ️ No activity logged yet.")
        return

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        
        cleaned_entries = []
        for line in reversed(all_lines):
            line_str = line.strip()
            if not line_str:
                continue
            if "Traceback" in line_str or "File \"" in line_str:
                continue
            # Format clean timestamp and action message
            if " [" in line_str and "] " in line_str:
                parts = line_str.split("] ", 1)
                time_part = line_str[:19]
                msg_part = parts[1] if len(parts) > 1 else line_str
                if ": " in msg_part:
                    msg_part = msg_part.split(": ", 1)[-1]
                cleaned_entries.append(f"• `{time_part}` {msg_part}")
            else:
                cleaned_entries.append(f"• {line_str}")
            
            if len(cleaned_entries) >= lines_count:
                break

        cleaned_entries.reverse()
        status_body = "\n".join(cleaned_entries) if cleaned_entries else "System is running smoothly."
        await reply_safe(update, context, f"📜 *Recent Activity Summary:*\n\n{status_body}")
    except Exception as e:
        await reply_safe(update, context, f"⚠️ Error reading status: {e}")

@auth_guard
async def user_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows complete user profile, configured API keys (masked), budget, and preferences: /user"""
    user_id = update.effective_user.id if update.effective_user else 0
    cfg = get_user_config(user_id)
    prefs = load_user_preferences(user_id=user_id)
    pantry = load_pantry_memory(user_id=user_id)
    store, city, country = get_user_store_and_city(user_id)
    
    # 1. Masked API Keys
    gemini_key = cfg.get("gemini_api_key", "")
    jev_key = cfg.get("jev_ai_api_key") or cfg.get("typesafe_api_key", "")
    
    def mask_key(k):
        if not k:
            return "❌ No configurada"
        if len(k) <= 8:
            return "✅ Configurada (***)"
        return f"✅ Configurada (`{k[:4]}...{k[-4:]}`)"

    gemini_status = mask_key(gemini_key)
    jev_status = mask_key(jev_key)

    # 2. Checkout & Flow
    autopay_active = cfg.get("auto_pay", False)
    autopay_txt = "⚡ 1-Click Auto-Pay" if autopay_active else "🛡️ Safe Review (Manual)"
    budget_limit = cfg.get("max_budget")
    budget_txt = f"{budget_limit:.2f} €" if budget_limit else "Sin Límite (∞)"

    # 3. Wolt browser session
    browser_dir = get_user_browser_dir(user_id)
    has_session = (
        os.path.exists(os.path.join(browser_dir, "Default", "Cookies"))
        or os.path.exists(os.path.join(browser_dir, "Network", "Cookies"))
        or os.path.exists(os.path.join(browser_dir, "Cookies"))
    )
    wolt_txt = "✅ Sesión Guardada" if has_session else "⚠️ Requiere login (`/wolt`)"

    # 4. Dietary & Household
    diet = prefs.get("diet_type", "omnivore")
    h_size = prefs.get("household_size", 1)
    allergies = ", ".join(prefs.get("allergies", [])) or "Ninguna"
    avoided = ", ".join(prefs.get("avoided_ingredients", [])) or "Ninguno"

    # 5. Pantry counts
    n_prot = len(pantry.get("proteins", []))
    n_prod = len(pantry.get("produce", []))
    n_stap = len(pantry.get("staples", []))

    text = (
        "👤 *Tu Perfil y Configuración Activa*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *Telegram User ID:* `{user_id}`\n"
        f"🏪 *Tienda Wolt:* `{store}` ({city.capitalize()}, {country.upper()})\n\n"
        "🔑 *Estado de API Keys Privadas:*\n"
        f"• ♊ *Gemini AI (Visión & Chef):* {gemini_status}\n"
        f"• ⚡ *Jev AI (Enrutamiento NLU):* {jev_status}\n\n"
        "⚙️ *Ajustes de Carrito y Seguridad:*\n"
        f"• 💳 *Modo de Pago:* `{autopay_txt}`\n"
        f"• 💶 *Límite Presupuesto:* `{budget_txt}`\n"
        f"• 🌐 *Navegador Wolt:* {wolt_txt}\n\n"
        "🥗 *Perfil Dietético y Hogar:*\n"
        f"• 🍽️ *Dieta:* `{diet}` | 👥 *Hogar:* `{h_size} persona(s)`\n"
        f"• 🚫 *Alergias:* `{allergies}`\n"
        f"• 🙅 *Ingredientes Evitados:* `{avoided}`\n\n"
        "📦 *Inventario en Memoria:*\n"
        f"• 🥩 `{n_prot}` proteínas | 🥑 `{n_prod}` frescos | 🧂 `{n_stap}` básicos\n\n"
        "💡 *Comandos para actualizar:*\n"
        "• `/setkey <clave>` - Actualizar clave de Gemini\n"
        "• `/setjev <clave>` - Actualizar clave de Jev AI\n"
        "• `/budget <monto>` - Cambiar límite en €\n"
        "• `/autopay on|off` - Alternar modo de pago\n"
        "• `/pref` - Editar alergias y porciones\n"
        "• `/pantry` - Ver inventario detallado"
    )

    keyboard = [
        [
            InlineKeyboardButton("⚙️ Ajustes Carrito", callback_data="btn_settings"),
            InlineKeyboardButton("🥗 Preferencias", callback_data="btn_pref")
        ],
        [
            InlineKeyboardButton("📦 Ver Despensa", callback_data="btn_pantry"),
            InlineKeyboardButton("🌐 Estado Wolt", callback_data="btn_wolt_status")
        ]
    ]

    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def setkey_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configures or updates private user GEMINI_API_KEY for vision AI recognition: /setkey <key>"""
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args if context.args else []
    if not args:
        cfg = get_user_config(user_id)
        current_status = "Configured for your user" if cfg.get("gemini_api_key") else "Not configured"
        await reply_safe(
            update,
            context,
            f"🔑 *Your Private Gemini API Key Status:* `{current_status}`\n\n"
            "Each user must configure their own private Gemini API key.\n"
            "To configure, run:\n`/setkey AIzaSy...`\n\n"
            "_(Get a free API key at https://aistudio.google.com)_"
        )
        return
    
    key = args[0].strip()
    cfg = get_user_config(user_id)
    cfg["gemini_api_key"] = key
    save_user_config(cfg, user_id=user_id)
    
    await reply_safe(
        update,
        context,
        "✅ *Gemini API Key Saved for Your Profile!*\n\n"
        "Visual meal photo recognition, recipe creation, and AI 7-day meal planning are now active for your user account."
    )

@auth_guard
async def setjev_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Configures or updates private user JEV_AI_API_KEY for Jev AI System-1 decision routing: /setjev <key>"""
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args if context.args else []
    if not args:
        cfg = get_user_config(user_id)
        current_status = "Configured for your user" if (cfg.get("jev_ai_api_key") or cfg.get("typesafe_api_key")) else "Not configured"
        await reply_safe(
            update, 
            context, 
            f"⚡ *Your Private Jev AI Key Status:* `{current_status}`\n\n"
            "Each user must configure their own private Jev AI key.\n"
            "To configure your key from https://jev-ai.pro/jev-api, run:\n"
            "`/setjev YOUR_JEV_AI_API_KEY`\n\n"
            "_(Enables ultra-fast sub-200ms System-1 decision routing to https://jev-ai.pro/api/v1/systemone)_"
        )
        return
    
    key = args[0].strip()
    cfg = get_user_config(user_id)
    cfg["jev_ai_api_key"] = key
    cfg["typesafe_api_key"] = key
    save_user_config(cfg, user_id=user_id)
    
    await reply_safe(
        update,
        context,
        "⚡ *Jev AI Key Saved for Your Profile!*\n\n"
        "Ultra-fast System-1 decision routing is now active for your user account (connected to https://jev-ai.pro/api)."
    )

@auth_guard
async def setstock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Adjusts specific stock levels or instructs how to restock via photo: /stock eggs 10"""
    user_id = update.effective_user.id if update.effective_user else 0
    args = context.args if context.args else []
    if not args:
        help_stock_msg = (
            "📦 *Pantry Stock & Grocery Restock Guide:*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📸 **Photo Restock:** Send or take a photo of your new grocery haul, receipt, or fridge shelf with the caption `/stock`.\n"
            "_(Gemini Vision will automatically identify every item and add them as **new inventory** into your pantry!)_\n\n"
            "✏️ **Manual Stock Adjustment:**\n"
            "• `/stock eggs 10` (sets eggs to 10)\n"
            "• `/stock chicken 2`\n"
            "• `/stock avocado 3`\n\n"
            "🏠 Tap `/pantry` to view your current virtual inventory."
        )
        await reply_safe(update, context, help_stock_msg)
        return
    
    item_name = args[0].lower()
    qty_val = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    
    pantry = load_pantry_memory(user_id=user_id)
    matched = False
    
    # Check in proteins
    for p in pantry.get("proteins", []):
        if item_name in p.get("name", "").lower():
            p["qty"] = qty_val
            matched = True
            break
            
    # Check in produce
    if not matched:
        for pr in pantry.get("produce", []):
            if item_name in pr.get("name", "").lower():
                pr["qty"] = qty_val
                matched = True
                break

    # Check in staples
    if not matched:
        for s in pantry.get("staples", []):
            if item_name in s.get("name", "").lower():
                s["qty"] = qty_val
                matched = True
                break
                
    if not matched:
        pantry.setdefault("proteins", []).append({
            "name": item_name.capitalize(),
            "qty": qty_val,
            "category": "manual_stock"
        })
        
    save_pantry_memory(pantry, user_id=user_id)
    await reply_safe(update, context, f"✅ *Stock Updated:* `{item_name.capitalize()}` is now set to **{qty_val}** in your pantry memory.")

@auth_guard
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles uploaded meal plates or grocery restock photos with vision AI analysis."""
    if not update.message or not update.message.photo:
        return
    user_id = update.effective_user.id if update.effective_user else 0
    photo = update.message.photo[-1]
    caption = (update.message.caption or "").strip()
    is_stock_caption = "/stock" in caption.lower() or caption.lower().startswith("stock") or "restock" in caption.lower()

    msg = await reply_safe(update, context, "📸 Analyzing photo with culinary vision AI & checking pantry memory...")
    
    # Save photo to local scratch directory
    os.makedirs("scratch", exist_ok=True)
    photo_file = await photo.get_file()
    save_path = os.path.join("scratch", f"telegram_upload_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
    await photo_file.download_to_drive(save_path)
    logger.info(f"Saved uploaded photo for user {user_id} to: {save_path}")

    # Analyze with Vision AI
    res = await asyncio.to_thread(analyze_photo_with_vision, save_path, None, user_id)
    user_pending_photo_meal[user_id] = {"path": save_path, "data": res}
    
    if res.get("error"):
        err_type = res.get("error")
        if err_type == "missing_api_key":
            await edit_safe(
                msg,
                "🔑 *Configura tu Gemini API Key para Reconocimiento Visual*\n\n"
                "No has configurado tu clave personal de Gemini en este bot.\n"
                "Para que la IA pueda auditar y reconocer los productos de tu nevera o platos de comida, necesitas enlazar tu API key gratuita de Google Gemini.\n\n"
                "👉 *Cómo configurarla en 30 segundos:*\n"
                "1. Entra a [Google AI Studio](https://aistudio.google.com) y crea tu API key gratuita.\n"
                "2. Envía al bot el comando:\n"
                "`/setkey TU_API_KEY`\n\n"
                "Una vez guardada, vuelve a enviar la foto con `/stock` para añadirla a tu despensa."
            )
            return
        else:
            await edit_safe(
                msg,
                f"⚠️ *Error al procesar la foto con Gemini Vision:*\n\n"
                f"_{res.get('message', 'No se pudo conectar con el servicio de visión')}_\n\n"
                "Verifica que tu clave sea válida ejecutando `/setkey` o intenta con otra foto."
            )
            return

    items = res.get("detected_items", [])
    if not items:
        await edit_safe(
            msg,
            "🔍 *No se detectaron productos en la imagen.*\n\n"
            "Asegúrate de que los alimentos o empaques sean visibles y cuenten con buena iluminación.\n"
            "También puedes añadir stock manualmente con `/stock <producto> <cantidad>`."
        )
        return

    dish_title = res.get("dish_title", "Foto de Inventario")
    meal_type = (res.get("meal_type") or "desayuno").capitalize()
    comp = res.get("composition", {})
    macros = res.get("estimated_macros", {})
    notes = res.get("chef_notes", "")
    photo_type = res.get("photo_type", "cooked_meal")
    
    # If the user explicitly supplied /stock in caption or if detected as grocery restock:
    if is_stock_caption or photo_type == "groceries_restock":
        restock_text, added_summary = restock_pantry_from_detected_items(items, source="photo_stock_caption", photo_path=save_path, user_id=user_id)
        keyboard = [
            [InlineKeyboardButton("📦 View Full Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("📋 Generate Plan with New Stock", callback_data="btn_plan")]
        ]
        if msg:
            await edit_safe(msg, restock_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await reply_safe(update, context, restock_text, reply_markup=InlineKeyboardMarkup(keyboard))
        return

    comp_lines = []
    if comp.get("proteins"):
        comp_lines.append(f"• 🥩 *Proteins:* {', '.join(comp['proteins'])}")
    if comp.get("carbs"):
        comp_lines.append(f"• 🍞 *Carbs & Breads:* {', '.join(comp['carbs'])}")
    if comp.get("produce"):
        comp_lines.append(f"• 🥑 *Produce & Fresh:* {', '.join(comp['produce'])}")
    if comp.get("dairy_and_fats"):
        comp_lines.append(f"• 🧈 *Fats & Dairy:* {', '.join(comp['dairy_and_fats'])}")
        
    comp_text = "\n".join(comp_lines) if comp_lines else "\n".join([f"• `{it.get('qty')} {it.get('name')}`" for it in items])
    
    text = (
        f"📸 *Food & Inventory Recognition (Gemini Vision)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🍽️ *Identified Items:* **{dish_title}**\n\n"
        f"🔍 *Visual Breakdown Detected:*\n"
        f"{comp_text}\n"
    )
    if macros and macros.get("calories"):
        text += (
            f"\n📊 *Estimated Nutrition & Macros:*\n"
            f"🔥 **Calories:** ~{macros.get('calories', 480)} kcal\n"
            f"🥩 **Protein:** ~{macros.get('protein_g', 24)}g | 🍞 **Carbs:** ~{macros.get('carbs_g', 32)}g | 🥑 **Fat:** ~{macros.get('fat_g', 28)}g\n"
        )
    if notes:
        text += f"\n👨‍🍳 *Chef Visual Notes:*\n_{notes}_\n"
        
    text += "\n👇 *Choose an action for this photo:*"
    
    keyboard = [
        [
            InlineKeyboardButton("📦 Restock Pantry (+ Add All As New)", callback_data="btn_confirm_photo_restock"),
            InlineKeyboardButton("✅ Deduct Food Eaten", callback_data="btn_confirm_photo_deduct")
        ],
        [
            InlineKeyboardButton("🍳 Breakfast", callback_data="btn_photo_type_breakfast"),
            InlineKeyboardButton("🥗 Lunch", callback_data="btn_photo_type_lunch"),
            InlineKeyboardButton("🍲 Dinner", callback_data="btn_photo_type_dinner")
        ],
        [
            InlineKeyboardButton("👨‍🍳 Generate AI Recipe For This", callback_data="btn_recipe_photo"),
            InlineKeyboardButton("❌ Discard", callback_data="btn_cancel_photo")
        ]
    ]
    
    if msg:
        await edit_safe(msg, text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles interactive inline keyboard button clicks."""
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer()
    except Exception as e:
        logger.debug(f"query.answer() ignored timeout/error: {e}")
    data = query.data
    user_id = update.effective_user.id if update.effective_user else 0
    store, city, country = get_user_store_and_city(user_id)
    cfg = get_user_config(user_id)
    wolt_url = f"https://wolt.com/en/{country}/{city}/venue/{store}"

    if data == "btn_plan":
        await plan_command(update, context)
    elif data == "btn_deals":
        await deals_command(update, context)
    elif data == "btn_pantry":
        await pantry_command(update, context)
    elif data == "btn_pref":
        await preferences_command(update, context)
    elif data == "btn_settings":
        await settings_command(update, context)
    elif data == "btn_user":
        await user_status_command(update, context)
    elif data == "btn_wolt_status":
        await wolt_command(update, context)
    elif data == "btn_toggle_autopay":
        current = cfg.get("auto_pay", False)
        cfg["auto_pay"] = not current
        save_user_config(cfg, user_id=user_id)
        await settings_command(update, context)
    elif data.startswith("btn_set_budget_"):
        b_val = float(data.replace("btn_set_budget_", ""))
        if b_val <= 0:
            cfg["max_budget"] = None
        else:
            cfg["max_budget"] = b_val
        save_user_config(cfg, user_id=user_id)
        await settings_command(update, context)
    elif data == "btn_reset_pref":
        save_user_preferences({
            "diet_type": "omnivore",
            "allergies": [],
            "avoided_ingredients": [],
            "preferred_proteins": ["chicken", "ground beef", "salmon", "eggs"],
            "household_size": 1,
            "notes": ""
        }, user_id=user_id)
        await query_edit_safe(query, "🔄 Dietary preferences reset to standard default.")
    elif data == "btn_clear_pantry":
        clear_pantry_memory(user_id=user_id)
        await query_edit_safe(query, "🗑️ Virtual pantry memory has been reset.")
    elif data == "btn_sample_cart":
        items = SAMPLE_WEEKLY_GROCERY_LIST
        user_pending_plans[user_id] = items
        await query_edit_safe(query, f"🛒 Building sample grocery cart on Wolt ({store})...")
        try:
            res = await asyncio.to_thread(
                add_items_to_cart,
                store,
                items,
                city=city,
                country=country,
                keep_open=True,
                record_memory=False,
                auto_pay=False,
                user_id=user_id
            )
            response_text, reply_markup = format_cart_result_message(res, store, city, country, user_id)
            await query.message.reply_text(response_text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error building sample cart: {e}", exc_info=True)
            await query.message.reply_text(f"⚠️ Error building cart: {e}")
    elif data in ["btn_confirm_cart", "btn_confirm_autopay"]:
        items = user_pending_plans.get(user_id, SAMPLE_WEEKLY_GROCERY_LIST)
        is_autopay_requested = (data == "btn_confirm_autopay") or cfg.get("auto_pay", False)
        budget_limit = cfg.get("max_budget")

        action_msg = "⚡ *Assembling Cart & Submitting Payment...*" if is_autopay_requested else "🛒 *Building Cart on Wolt...*"
        await query_edit_safe(query, f"{action_msg}\nVenue: `{store}` ({len(items)} items).")

        try:
            res = await asyncio.to_thread(
                add_items_to_cart,
                store,
                items,
                city=city,
                country=country,
                keep_open=True,
                record_memory=is_autopay_requested,
                auto_pay=is_autopay_requested,
                user_id=user_id,
                budget=budget_limit
            )
            response_text, reply_markup = format_cart_result_message(res, store, city, country, user_id)
            await query.message.reply_text(response_text, parse_mode="Markdown", reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Error executing cart flow: {e}", exc_info=True)
            await query.message.reply_text(f"⚠️ Error building cart: {e}")
    elif data == "btn_accept_substitutes":
        subs = user_pending_substitutions.pop(user_id, [])
        if not subs:
            await query_edit_safe(query, "ℹ️ No hay sustitutos pendientes de confirmación.")
        else:
            await query_edit_safe(query, f"🛒 *Agregando {len(subs)} sustituto(s) aprobados al carrito de Wolt...*")
            try:
                sub_items = [(s.get("found_title", s.get("requested")), s.get("qty", 1)) for s in subs]
                res = await asyncio.to_thread(
                    add_items_to_cart,
                    store,
                    sub_items,
                    city=city,
                    country=country,
                    keep_open=True,
                    record_memory=False,
                    auto_pay=False,
                    user_id=user_id,
                    allow_substitutes=True
                )
                final_p = res.get("final_price", 0.0)
                wolt_url = f"https://wolt.com/en/{country}/{city}/venue/{store}"
                confirm_msg = (
                    "✅ *Sustituto(s) Agregados al Carrito de Wolt!*\n\n"
                    f"💶 *Total actualizado del carrito:* **{final_p:.2f} €**\n\n"
                    "📲 Abre tu **app de Wolt** en el teléfono para revisar tu pedido y pagar con 1 toque."
                )
                keyboard = [
                    [InlineKeyboardButton("📱 Abrir Wolt App / Web", url=wolt_url)],
                    [InlineKeyboardButton("💾 Confirmar Pedido Realizado (Sincronizar)", callback_data="btn_record_last")]
                ]
                await query.message.reply_text(confirm_msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception as e:
                logger.error(f"Error adding substitutes: {e}", exc_info=True)
                await query.message.reply_text(f"⚠️ Error al agregar sustitutos: {e}")
    elif data == "btn_skip_substitutes":
        user_pending_substitutions.pop(user_id, None)
        await query_edit_safe(
            query,
            "❌ *Sustituto(s) Descartados.*\n\nTu carrito en Wolt contiene únicamente los productos exactos confirmados."
        )
    elif data == "btn_today_menu":
        await menu_command(update, context)
    elif data == "btn_week_plan":
        await week_command(update, context)
    elif data == "btn_view_breakfast":
        await breakfast_command(update, context)
    elif data == "btn_view_lunch":
        await lunch_command(update, context)
    elif data == "btn_view_dinner":
        await dinner_command(update, context)
    elif data == "btn_view_snack":
        await snack_command(update, context)
    elif data == "btn_eat_breakfast":
        text, _ = log_meal_consumption("breakfast", user_id=user_id)
        keyboard = [
            [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "btn_eat_lunch":
        text, _ = log_meal_consumption("lunch", user_id=user_id)
        keyboard = [
            [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "btn_eat_dinner":
        text, _ = log_meal_consumption("dinner", user_id=user_id)
        keyboard = [
            [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "btn_eat_snack":
        text, _ = log_meal_consumption("snack", user_id=user_id)
        keyboard = [
            [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "btn_swap_meal":
        plan = load_meal_plan(user_id=user_id)
        days = plan.get("days", [])
        now = datetime.now()
        day_idx = min(6, max(0, now.weekday()))
        if days and day_idx < len(days):
            current_lunch = days[day_idx]["lunch"]["title"]
            pantry = load_pantry_memory(user_id=user_id)
            stock_items = [p.get("name") for p in pantry.get("proteins", [])] + [pr.get("name") for pr in pantry.get("produce", [])]
            
            await query_edit_safe(query, "👨‍🍳 *Chef AI is crafting an innovative alternative lunch for today...*")
            alt = await asyncio.to_thread(
                generate_ai_meal_swap,
                "lunch",
                current_lunch,
                stock_items,
                load_user_preferences(user_id=user_id),
                None,
                user_id
            )
            days[day_idx]["lunch"]["title"] = alt.get("title", "Gourmet Chef Special")
            days[day_idx]["lunch"]["tip"] = alt.get("tip", "Cook with care and season to taste.")
            if "ingredients" in alt:
                days[day_idx]["lunch"]["ingredients"] = alt["ingredients"]
            if "protein_raw" in alt:
                days[day_idx]["lunch"]["protein_raw"] = alt["protein_raw"]
            save_meal_plan(plan, user_id=user_id)
            await query.message.reply_text(
                f"🔄 *Meal Swapped for Today!*\n\n"
                f"🥗 *New Lunch:* {alt.get('title')}\n"
                f"⚖️ *Target:* `{alt.get('protein_raw', '')}`\n\n"
                f"👨‍🍳 *Chef Technique:*\n{alt.get('tip', '')}",
                parse_mode="Markdown"
            )
        else:
            await query_edit_safe(query, "🔄 Generated fresh meal plan variation.")
    elif data == "btn_record_last":
        items = user_pending_plans.get(user_id, SAMPLE_WEEKLY_GROCERY_LIST)
        record_purchase_in_memory(items, store_slug=store, user_id=user_id)
        new_plan = generate_weekly_meal_plan(inventory_items=items, prefs=load_user_preferences(user_id=user_id), user_id=user_id)
        save_meal_plan(new_plan, user_id=user_id)
        await query_edit_safe(query, "💾 *Success!* Items recorded into your virtual pantry memory and weekly meal plan is now 100% synchronized!")
    elif data == "btn_confirm_photo_restock":
        pending = user_pending_photo_meal.get(user_id)
        if pending:
            d = pending.get("data", {})
            items = d.get("detected_items", [])
            text, _ = restock_pantry_from_detected_items(items, source="photo_button_restock", photo_path=pending.get("path"), user_id=user_id)
            keyboard = [
                [InlineKeyboardButton("📦 View Full Pantry", callback_data="btn_pantry")],
                [InlineKeyboardButton("📋 Generate Plan with New Stock", callback_data="btn_plan")]
            ]
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.message.reply_text("ℹ️ No pending photo found. Send a fresh photo with `/stock` anytime!")
    elif data == "btn_confirm_photo_deduct":
        pending = user_pending_photo_meal.get(user_id)
        if pending:
            d = pending.get("data", {})
            text, _ = deduct_custom_ingredients(
                d.get("detected_items", []),
                meal_type=d.get("meal_type", "breakfast"),
                dish_title=d.get("dish_title", "Cooked Meal"),
                photo_path=pending.get("path"),
                user_id=user_id
            )
            keyboard = [
                [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
                [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
            ]
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.message.reply_text("ℹ️ No pending photo meal found. Send a fresh photo anytime!")
    elif data in ["btn_photo_type_breakfast", "btn_photo_type_lunch", "btn_photo_type_dinner"]:
        meal_type = "breakfast" if data == "btn_photo_type_breakfast" else ("lunch" if data == "btn_photo_type_lunch" else "dinner")
        pending = user_pending_photo_meal.get(user_id)
        if pending:
            d = pending.get("data", {})
            d["meal_type"] = meal_type
            text, _ = deduct_custom_ingredients(
                d.get("detected_items", []),
                meal_type=meal_type,
                dish_title=d.get("dish_title", "Cooked Meal"),
                photo_path=pending.get("path"),
                user_id=user_id
            )
            keyboard = [
                [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
                [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
            ]
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            text, _ = log_meal_consumption(meal_type, user_id=user_id)
            await query.message.reply_text(text, parse_mode="Markdown")
    elif data == "btn_recipe_photo":
        pending = user_pending_photo_meal.get(user_id, {})
        d = pending.get("data", {})
        dish_name = d.get("dish_title", "Custom Dish from Photo")
        detected = d.get("detected_items", [])
        ingredients = [f"{it.get('qty', 1)} {it.get('name', 'Ingredient')}" for it in detected] if detected else ["Plate ingredients"]
        
        msg = await query.message.reply_text(f"👨‍🍳 *Chef AI is preparing a step-by-step recipe for:* `{dish_name}`...")
        recipe_text = await asyncio.to_thread(generate_ai_recipe, dish_name, ingredients, None, None, user_id)
        keyboard = [
            [InlineKeyboardButton("✅ Deduct Exact Food Used", callback_data="btn_confirm_photo_deduct")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await edit_safe(msg, recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data in ["btn_recipe_breakfast", "btn_recipe_lunch", "btn_recipe_dinner"]:
        meal_type = "breakfast" if data == "btn_recipe_breakfast" else ("lunch" if data == "btn_recipe_lunch" else "dinner")
        plan = load_meal_plan(user_id=user_id)
        days = plan.get("days", [])
        now = datetime.now()
        day_idx = min(6, max(0, now.weekday()))
        day_data = days[day_idx] if days and day_idx < len(days) else {}
        meal = day_data.get(meal_type, {})
        dish_name = meal.get("title", f"{meal_type.capitalize()}")
        ingredients = meal.get("ingredients", [])
        msg = await query.message.reply_text(f"👨‍🍳 *Chef AI is preparing a step-by-step recipe for:* `{dish_name}`...")
        recipe_text = await asyncio.to_thread(generate_ai_recipe, dish_name, ingredients, None, None, user_id)
        keyboard = [
            [InlineKeyboardButton(f"✅ Log {meal_type.capitalize()} Eaten", callback_data=f"btn_eat_{meal_type}")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await edit_safe(msg, recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "btn_cancel_photo":
        await query_edit_safe(query, "❌ Meal photo discarded.")
    elif data == "btn_cancel_plan":
        await query_edit_safe(query, "❌ Meal plan cancelled.")
    elif data == "btn_prompt_plan_suggestions":
        user_waiting_state[user_id] = {"action": "plan_suggestions"}
        await query.message.reply_text(
            "✨ *Plan Semanal Personalizado con Sugerencias*\n\n"
            "Escribe tus sugerencias, antojos o preferencias para esta semana (por ejemplo: _'Comida mexicana y alta en proteína'_, _'Platos con mucho salmón y sin lácteos'_, _'Comida italiana y rápida'_) o envía `/cancel` para abortar:",
            parse_mode="Markdown"
        )
    elif data == "btn_prompt_budget":
        user_waiting_state[user_id] = {"action": "set_budget"}
        await query.message.reply_text(
            "💶 *Set Custom Cart Budget*\n\n"
            "Please reply with your maximum cart budget in Euros (e.g. `45` or `0` for no limit) or send `/cancel` to abort:",
            parse_mode="Markdown"
        )
    elif data == "btn_prompt_store":
        user_waiting_state[user_id] = {"action": "set_store"}
        await query.message.reply_text(
            "🏬 *Change Store Venue*\n\n"
            "Please reply with the Wolt store venue slug and optional city (e.g. `wolt-market-maakri tallinn`) or send `/cancel` to abort:",
            parse_mode="Markdown"
        )
    elif data == "btn_prompt_cart":
        user_waiting_state[user_id] = {"action": "set_cart"}
        await query.message.reply_text(
            "🛒 *Build Custom Wolt Cart*\n\n"
            "Please reply with the items and quantities you want (e.g. `Banaan:6 Rukola:1 Munad:1`) or send `/cancel` to abort:",
            parse_mode="Markdown"
        )
    elif data == "btn_prompt_recipe":
        user_waiting_state[user_id] = {"action": "get_recipe"}
        await query.message.reply_text(
            "👨‍🍳 *Ask Chef AI for a Recipe*\n\n"
            "What dish, meal, or ingredients would you like a gourmet recipe for? (e.g. `salmon with asparagus` or `lunch`) or send `/cancel` to abort:",
            parse_mode="Markdown"
        )
    elif data == "btn_prompt_allergy":
        user_waiting_state[user_id] = {"action": "set_allergy"}
        await query.message.reply_text(
            "🥜 *Update Allergy Profile*\n\n"
            "Please reply with your allergies separated by commas (e.g. `peanuts, shellfish, lactose`) or send `/cancel` to abort:",
            parse_mode="Markdown"
        )
    elif data == "btn_prompt_avoid":
        user_waiting_state[user_id] = {"action": "set_avoid"}
        await query.message.reply_text(
            "🚫 *Update Avoided Foods / Dislikes*\n\n"
            "Please reply with ingredients you dislike or avoid separated by commas (e.g. `pork, mushrooms, eggplant`) or send `/cancel` to abort:",
            parse_mode="Markdown"
        )
    elif data == "btn_prompt_household":
        user_waiting_state[user_id] = {"action": "set_household"}
        await query.message.reply_text(
            "👥 *Set Household Size*\n\n"
            "Please reply with the number of people to feed (e.g. `2`) or send `/cancel` to abort:",
            parse_mode="Markdown"
        )
    elif data == "btn_prompt_stock":
        user_waiting_state[user_id] = {"action": "set_stock"}
        await query.message.reply_text(
            "📦 *Adjust Stock Level*\n\n"
            "Please reply with the item name and count (e.g. `eggs 10` or `chicken 2`) or send `/cancel` to abort:",
            parse_mode="Markdown"
        )

@auth_guard
async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processes plain text messages: resolves active prompt waiting states or parses natural language intents."""
    if not update.message or not update.message.text:
        return
    user_text = update.message.text.strip()
    user_id = update.effective_user.id if update.effective_user else 0

    # 0. Global Cancellation
    if user_text.lower() in ["/cancel", "cancel", "/abort", "abort", "/stop", "stop"]:
        if user_id in user_waiting_state:
            user_waiting_state.pop(user_id, None)
            await reply_safe(update, context, "🚫 Action cancelled. How else can I help you?")
            return
        await stop_command(update, context)
        return

    # 1. Handle Active Prompt Waiting State
    if user_id in user_waiting_state:
        state = user_waiting_state.pop(user_id)
        action = state.get("action")
        
        if action == "plan_suggestions":
            suggestions = user_text.strip()
            await plan_command(update, context, suggestions=suggestions)
            return

        elif action == "set_budget":
            val_str = user_text.replace(",", ".").replace("€", "").strip()
            try:
                val = float(val_str)
                cfg = get_user_config(user_id)
                if val <= 0:
                    cfg["max_budget"] = None
                    save_user_config(cfg, user_id=user_id)
                    await reply_safe(update, context, "✅ *Cart Budget Limit Removed.* No price ceiling is enforced.")
                else:
                    cfg["max_budget"] = val
                    save_user_config(cfg, user_id=user_id)
                    await reply_safe(update, context, f"✅ *Cart Budget Limit Set:* **{val:.2f} €**\nIf any grocery order exceeds this total, Auto-Pay will be automatically blocked for your safety.")
            except ValueError:
                await reply_safe(update, context, "⚠️ Invalid amount. Budget not updated. You can try again or use `/budget <amount>`.")
            return

        elif action == "set_store":
            parts = user_text.split()
            if parts:
                new_store = parts[0].strip()
                new_city = parts[1].strip().lower() if len(parts) > 1 else DEFAULT_CITY
                cfg = get_user_config(user_id)
                cfg["store"] = new_store
                cfg["city"] = new_city
                save_user_config(cfg, user_id=user_id)
                await reply_safe(update, context, f"✅ *Store Venue Updated:*\n• Store: `{new_store}`\n• City: `{new_city}`\n\nAll subsequent scans and cart operations will use this store.")
            else:
                await reply_safe(update, context, "⚠️ No store specified. Try again with `/store <venue-slug>`.")
            return

        elif action == "set_cart":
            parts = user_text.split()
            items = []
            for it in parts:
                if ":" in it:
                    p = it.rsplit(":", 1)
                    name = p[0].strip()
                    qty = int(p[1].strip()) if p[1].strip().isdigit() else 1
                    items.append((name, qty))
                else:
                    items.append((it.strip(), 1))
            
            if not items:
                await reply_safe(update, context, "⚠️ No items found. Usage example: `Banaan:6 Rukola:1`")
                return
            
            store, city, country = get_user_store_and_city(user_id)
            cfg = get_user_config(user_id)
            auto_pay_pref = cfg.get("auto_pay", False)
            budget_limit = cfg.get("max_budget")
            msg = await reply_safe(update, context, f"🛒 Launching browser automation for {len(items)} items on {store}...")
            
            try:
                res = await asyncio.to_thread(
                    add_items_to_cart,
                    store,
                    items,
                    city=city,
                    country=country,
                    keep_open=True,
                    record_memory=False,
                    auto_pay=auto_pay_pref,
                    user_id=user_id,
                    budget=budget_limit
                )
                user_pending_plans[user_id] = items
                response_text, reply_markup = format_cart_result_message(res, store, city, country, user_id)
                if msg:
                    await edit_safe(msg, response_text, reply_markup=reply_markup)
                else:
                    await reply_safe(update, context, response_text, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Cart build failed: {e}", exc_info=True)
                if msg:
                    await edit_safe(msg, f"⚠️ Cart build failed: {e}")
                else:
                    await reply_safe(update, context, f"⚠️ Cart build failed: {e}")
            return

        elif action == "get_recipe":
            dish_name = user_text
            ingredients = [dish_name]
            msg = await reply_safe(update, context, f"👨‍🍳 *Chef AI is writing a step-by-step gourmet recipe for:* `{dish_name}`...")
            recipe_text = await asyncio.to_thread(generate_ai_recipe, dish_name, ingredients, None, None, user_id)
            keyboard = [
                [
                    InlineKeyboardButton("🍳 Breakfast Recipe", callback_data="btn_recipe_breakfast"),
                    InlineKeyboardButton("🥗 Lunch Recipe", callback_data="btn_recipe_lunch"),
                    InlineKeyboardButton("🍲 Dinner Recipe", callback_data="btn_recipe_dinner")
                ],
                [
                    InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu"),
                    InlineKeyboardButton("📦 Pantry Stock", callback_data="btn_pantry")
                ]
            ]
            if msg:
                await edit_safe(msg, recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                await reply_safe(update, context, recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))
            return

        elif action == "set_allergy":
            prefs = load_user_preferences(user_id=user_id)
            new_allergies = [a.strip() for a in user_text.split(",") if a.strip()]
            prefs["allergies"] = new_allergies
            save_user_preferences(prefs, user_id=user_id)
            await reply_safe(update, context, f"✅ *Allergies Updated:* {', '.join(new_allergies) if new_allergies else 'None'}")
            return

        elif action == "set_avoid":
            prefs = load_user_preferences(user_id=user_id)
            new_avoid = [a.strip() for a in user_text.split(",") if a.strip()]
            prefs["avoided_ingredients"] = new_avoid
            save_user_preferences(prefs, user_id=user_id)
            await reply_safe(update, context, f"✅ *Avoided Foods Updated:* {', '.join(new_avoid) if new_avoid else 'None'}")
            return

        elif action == "set_household":
            if user_text.strip().isdigit():
                prefs = load_user_preferences(user_id=user_id)
                prefs["household_size"] = max(1, int(user_text.strip()))
                save_user_preferences(prefs, user_id=user_id)
                await reply_safe(update, context, f"✅ *Household Size Set To:* {prefs['household_size']} person(s)")
            else:
                await reply_safe(update, context, "⚠️ Please provide a valid integer for household size (e.g. `2`).")
            return

        elif action == "set_stock":
            parts = user_text.split()
            if parts:
                item_name = parts[0].lower()
                qty_val = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
                pantry = load_pantry_memory(user_id=user_id)
                matched = False
                for p in pantry.get("proteins", []):
                    if item_name in p.get("name", "").lower():
                        p["qty"] = qty_val
                        matched = True
                        break
                if not matched:
                    for pr in pantry.get("produce", []):
                        if item_name in pr.get("name", "").lower():
                            pr["qty"] = qty_val
                            matched = True
                            break
                if not matched:
                    for s in pantry.get("staples", []):
                        if item_name in s.get("name", "").lower():
                            s["qty"] = qty_val
                            matched = True
                            break
                if not matched:
                    pantry.setdefault("proteins", []).append({
                        "name": item_name.capitalize(),
                        "qty": qty_val,
                        "category": "manual_stock"
                    })
                save_pantry_memory(pantry, user_id=user_id)
                await reply_safe(update, context, f"✅ *Stock Updated:* `{item_name.capitalize()}` is now set to **{qty_val}** in your pantry memory.")
            return

    # 2. Natural Language Understanding via Gemini / Fallback
    nlu_result = await asyncio.to_thread(parse_natural_language_intent, user_text, user_id)
    intent = nlu_result.get("intent", "chef_chat")
    params = nlu_result.get("parameters", {})
    
    logger.info(f"Command processed for User {user_id}: {intent}")

    if intent == "stop":
        await stop_command(update, context)
    elif intent == "user_status":
        await user_status_command(update, context)
    elif intent == "set_key":
        k_val = params.get("key_value")
        if k_val:
            cfg = get_user_config(user_id)
            cfg["gemini_api_key"] = k_val
            save_user_config(cfg, user_id=user_id)
            await reply_safe(update, context, "✅ *Gemini API Key Guardada!*\n\nReconocimiento visual y chef dinámico ahora están activos para tu usuario.")
        else:
            await setkey_command(update, context)
    elif intent == "set_jev":
        k_val = params.get("key_value")
        if k_val:
            cfg = get_user_config(user_id)
            cfg["jev_ai_api_key"] = k_val
            cfg["typesafe_api_key"] = k_val
            save_user_config(cfg, user_id=user_id)
            await reply_safe(update, context, "⚡ *Jev AI Key Guardada!*\n\nEnrutamiento ultrarrápido System-1 ahora está activo para tu usuario.")
        else:
            await setjev_command(update, context)
    elif intent == "today_menu":
        meal_type = params.get("meal_type")
        if meal_type == "breakfast":
            await breakfast_command(update, context)
        elif meal_type == "lunch":
            await lunch_command(update, context)
        elif meal_type == "dinner":
            await dinner_command(update, context)
        elif meal_type == "snack":
            await snack_command(update, context)
        else:
            await menu_command(update, context)
    elif intent == "week_plan":
        sug = params.get("user_suggestions")
        await week_command(update, context, suggestions=sug)
    elif intent == "plan_week":
        sug = params.get("user_suggestions")
        await plan_command(update, context, suggestions=sug)
    elif intent == "deals":
        await deals_command(update, context)
    elif intent == "pantry_status":
        await pantry_command(update, context)
    elif intent == "wolt_status":
        await wolt_command(update, context)
    elif intent == "log_meal":
        meal_t = params.get("meal_type") or "lunch"
        text, _ = log_meal_consumption(meal_t, user_id=user_id)
        keyboard = [
            [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif intent == "set_budget":
        b_val = params.get("budget_amount")
        if b_val is not None:
            cfg = get_user_config(user_id)
            if b_val <= 0:
                cfg["max_budget"] = None
                save_user_config(cfg, user_id=user_id)
                await reply_safe(update, context, "✅ *Cart Budget Limit Removed.* No price ceiling is enforced.")
            else:
                cfg["max_budget"] = b_val
                save_user_config(cfg, user_id=user_id)
                await reply_safe(update, context, f"✅ *Cart Budget Limit Set:* **{b_val:.2f} €**\nIf any grocery order exceeds this total, Auto-Pay will be automatically blocked for your safety.")
        else:
            await budget_command(update, context)
    elif intent == "set_autopay":
        val = params.get("autopay_value")
        if val is not None:
            cfg = get_user_config(user_id)
            cfg["auto_pay"] = val
            save_user_config(cfg, user_id=user_id)
            if val:
                await reply_safe(update, context, "⚡ *Auto-Pay ENABLED!* Building carts will automatically submit payment on Wolt (subject to your budget limit).")
            else:
                await reply_safe(update, context, "🛡️ *Auto-Pay DISABLED!* Safe Review mode is active. Carts will be built and synced to your phone app without charging your card.")
        else:
            await autopay_command(update, context)
    elif intent == "change_store":
        st = params.get("store_slug")
        ct = params.get("city") or DEFAULT_CITY
        if st:
            cfg = get_user_config(user_id)
            cfg["store"] = st
            cfg["city"] = ct
            save_user_config(cfg, user_id=user_id)
            await reply_safe(update, context, f"✅ *Store Venue Updated:*\n• Store: `{st}`\n• City: `{ct}`")
        else:
            await store_command(update, context)
    elif intent == "preferences":
        if params.get("allergies"):
            prefs = load_user_preferences(user_id=user_id)
            prefs["allergies"] = params["allergies"]
            save_user_preferences(prefs, user_id=user_id)
            await reply_safe(update, context, f"✅ *Allergies Updated:* {', '.join(params['allergies'])}")
        elif params.get("diet_type"):
            prefs = load_user_preferences(user_id=user_id)
            prefs["diet_type"] = params["diet_type"].lower()
            save_user_preferences(prefs, user_id=user_id)
            await reply_safe(update, context, f"✅ *Diet Type Set To:* {params['diet_type'].capitalize()}")
        elif params.get("household_size"):
            prefs = load_user_preferences(user_id=user_id)
            prefs["household_size"] = max(1, int(params["household_size"]))
            save_user_preferences(prefs, user_id=user_id)
            await reply_safe(update, context, f"✅ *Household Size Set To:* {prefs['household_size']} person(s)")
        else:
            await preferences_command(update, context)
    elif intent == "recipe":
        dish_name = params.get("dish_or_ingredients") or user_text
        msg = await reply_safe(update, context, f"👨‍🍳 *Chef AI is writing a step-by-step recipe for:* `{dish_name}`...")
        recipe_text = await asyncio.to_thread(generate_ai_recipe, dish_name, [dish_name], None, None, user_id)
        keyboard = [
            [
                InlineKeyboardButton("🍳 Breakfast Recipe", callback_data="btn_recipe_breakfast"),
                InlineKeyboardButton("🥗 Lunch Recipe", callback_data="btn_recipe_lunch"),
                InlineKeyboardButton("🍲 Dinner Recipe", callback_data="btn_recipe_dinner")
            ],
            [
                InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu"),
                InlineKeyboardButton("📦 Pantry Stock", callback_data="btn_pantry")
            ]
        ]
        if msg:
            await edit_safe(msg, recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await reply_safe(update, context, recipe_text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif intent == "restock":
        items_extracted = params.get("items", [])
        if items_extracted:
            restock_text, _ = restock_pantry_from_detected_items(items_extracted, source="nl_restock", user_id=user_id)
            keyboard = [
                [InlineKeyboardButton("📦 View Full Pantry", callback_data="btn_pantry")],
                [InlineKeyboardButton("📋 Generate Plan with New Stock", callback_data="btn_plan")]
            ]
            await reply_safe(update, context, restock_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await setstock_command(update, context)
    elif intent == "build_cart":
        items_extracted = params.get("items", [])
        if items_extracted:
            items_tuples = [(it["name"], it.get("qty", 1)) for it in items_extracted]
            user_pending_plans[user_id] = items_tuples
            store, city, country = get_user_store_and_city(user_id)
            cfg = get_user_config(user_id)
            auto_pay_pref = cfg.get("auto_pay", False)
            budget_limit = cfg.get("max_budget")
            msg = await reply_safe(update, context, f"🛒 Launching browser automation for {len(items_tuples)} items on {store}...")
            try:
                res = await asyncio.to_thread(
                    add_items_to_cart,
                    store,
                    items_tuples,
                    city=city,
                    country=country,
                    keep_open=True,
                    record_memory=False,
                    auto_pay=auto_pay_pref,
                    user_id=user_id,
                    budget=budget_limit
                )
                response_text, reply_markup = format_cart_result_message(res, store, city, country, user_id)
                if msg:
                    await edit_safe(msg, response_text, reply_markup=reply_markup)
                else:
                    await reply_safe(update, context, response_text, reply_markup=reply_markup)
            except Exception as e:
                logger.error(f"Cart build failed: {e}", exc_info=True)
                if msg:
                    await edit_safe(msg, f"⚠️ Cart build failed: {e}")
                else:
                    await reply_safe(update, context, f"⚠️ Cart build failed: {e}")
        else:
            user_waiting_state[user_id] = {"action": "set_cart"}
            await reply_safe(update, context, "🛒 *What items would you like to add to your Wolt cart?*\n\nSend items with quantities (e.g. `Banaan:6 Rukola:1 Sibul:1`) or `/cancel` to abort:")
    else:
        # chef_chat fallback
        chef_reply = nlu_result.get("chef_reply") or "👨‍🍳 I'm here to help with your meals, pantry, store deals, and Wolt carts! What would you like to cook or plan today?"
        keyboard = [
            [
                InlineKeyboardButton("🌅 Today's Menu", callback_data="btn_today_menu"),
                InlineKeyboardButton("📋 Generate Plan", callback_data="btn_plan")
            ],
            [
                InlineKeyboardButton("🏷️ Active Deals", callback_data="btn_deals"),
                InlineKeyboardButton("📦 Pantry Status", callback_data="btn_pantry")
            ]
        ]
        await reply_safe(update, context, f"👨‍🍳 {chef_reply}", reply_markup=InlineKeyboardMarkup(keyboard))

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs uncaught exceptions and sends a helpful message to the user."""
    err_str = str(context.error) if context.error else ""
    if "Query is too old" in err_str or "Message is not modified" in err_str:
        logger.debug(f"Ignored minor Telegram update error: {err_str}")
        return

    logger.error("Exception while handling Telegram update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"⚠️ *An error occurred during execution:*\n`{context.error}`\n\nUse `/logs` to view detailed trace.",
                parse_mode="Markdown"
            )
        except Exception:
            try:
                await update.effective_message.reply_text(
                    f"⚠️ An error occurred during execution:\n{context.error}\n\nUse /logs to view detailed trace."
                )
            except Exception:
                pass

async def post_init(application):
    """Registers bot autocomplete command suggestions with Telegram API."""
    commands = [
        BotCommand("today", "🌅 Today's scheduled meals, portions & tips"),
        BotCommand("menu", "🌅 View today's full menu"),
        BotCommand("recipe", "👨‍🍳 Get AI step-by-step recipe (/recipe lunch)"),
        BotCommand("breakfast", "🍳 View breakfast food quantities & tips"),
        BotCommand("lunch", "🥗 View lunch ingredients & portions"),
        BotCommand("dinner", "🍲 View dinner ingredients & portions"),
        BotCommand("snack", "🍎 View today's healthy snack"),
        BotCommand("eat", "✅ Log a meal & deduct ingredients (/eat breakfast)"),
        BotCommand("week", "📅 Browse 7-day scheduled meal plan"),
        BotCommand("plan", "📋 Generate 7-day meal plan & Wolt cart list"),
        BotCommand("user", "👤 Ver tu perfil, API keys y configuración activa"),
        BotCommand("settings", "⚙️ Configure Auto-Pay & Budget Limits"),
        BotCommand("budget", "💶 Set max cart budget (/budget 50)"),
        BotCommand("autopay", "💳 Toggle auto payment (/autopay on|off)"),
        BotCommand("pref", "👤 Dietary profile, allergies & household size"),
        BotCommand("pantry", "🏠 View virtual pantry memory & stock"),
        BotCommand("stock", "📦 Adjust ingredient stock (/stock eggs 10)"),
        BotCommand("deals", "🏷️ Scan live discount deals on Wolt"),
        BotCommand("cart", "🛒 Build Wolt cart directly (/cart Banaan:6)"),
        BotCommand("wolt", "🛍️ Check isolated Wolt browser session"),
        BotCommand("store", "🏬 Change Wolt store venue (/store wolt-market-maakri)"),
        BotCommand("setkey", "🔑 Configure Gemini API key for photo vision"),
        BotCommand("setjev", "⚡ Configure Jev AI key for sub-200ms decision routing"),
        BotCommand("stop", "🛑 Abort running cart creation or scan"),
        BotCommand("logs", "📜 View live system logs on PC (/logs 25)"),
        BotCommand("help", "📖 View complete command guide"),
        BotCommand("start", "👋 Welcome dashboard & main menu")
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Bot command autocomplete menu successfully registered with Telegram API.")
    except Exception as e:
        logger.warning(f"Failed to register bot commands with Telegram API: {e}")

def main():
    """Main application entry point."""
    if not BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN is missing! Please configure .env.")
        sys.exit(1)

    logger.info("🤖 Wolt Smart Pantry Telegram Bot Starting...")
    logger.info(f"[*] Default Store: {DEFAULT_STORE} ({DEFAULT_CITY}, {DEFAULT_COUNTRY})")
    logger.info(f"[*] Log File: {LOG_FILE}")
    if ALLOWED_USERS:
        logger.info(f"[*] Access restricted to Telegram user ID(s): {ALLOWED_USERS}")
    else:
        logger.warning("[!] TELEGRAM_ALLOWED_USERS is empty. Bot will accept commands from any user.")

    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(CommandHandler("abort", stop_command))
    app.add_handler(CommandHandler("cancel", stop_command))
    app.add_handler(CommandHandler("today", menu_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("recipe", recipe_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("breakfast", breakfast_command))
    app.add_handler(CommandHandler("lunch", lunch_command))
    app.add_handler(CommandHandler("dinner", dinner_command))
    app.add_handler(CommandHandler("snack", snack_command))
    app.add_handler(CommandHandler("eat", eat_command))
    app.add_handler(CommandHandler(["user", "me", "profile", "myconfig"], user_status_command))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("cartflow", settings_command))
    app.add_handler(CommandHandler("budget", budget_command))
    app.add_handler(CommandHandler("autopay", autopay_command))
    app.add_handler(CommandHandler("setkey", setkey_command))
    app.add_handler(CommandHandler("setjev", setjev_command))
    app.add_handler(CommandHandler("stock", setstock_command))
    app.add_handler(CommandHandler("eggs", setstock_command))
    app.add_handler(CommandHandler("preferences", preferences_command))
    app.add_handler(CommandHandler("pref", preferences_command))
    app.add_handler(CommandHandler("pantry", pantry_command))
    app.add_handler(CommandHandler("deals", deals_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("cart", cart_command))
    app.add_handler(CommandHandler("wolt", wolt_command))
    app.add_handler(CommandHandler("store", store_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    app.add_handler(CallbackQueryHandler(button_callback_handler))

    # Global error handler
    app.add_error_handler(global_error_handler)

    # Schedule Daily 09:00 AM Morning Menu Broadcast
    if app.job_queue:
        hour, minute = 9, 0
        try:
            parts = DAILY_MENU_TIME.split(":")
            hour, minute = int(parts[0]), int(parts[1])
        except Exception:
            pass
        target_time = time(hour=hour, minute=minute, tzinfo=BOT_TIMEZONE)
        app.job_queue.run_daily(
            daily_morning_menu_job,
            time=target_time,
            days=(0, 1, 2, 3, 4, 5, 6),
            name="daily_morning_menu"
        )
        logger.info(f"[*] Daily morning menu broadcast scheduled for {hour:02d}:{minute:02d} ({BOT_TIMEZONE.zone})")
    else:
        logger.warning("[!] Job queue not available. Morning broadcasts will not run.")

    logger.info("Bot is online and listening for Telegram updates.")
    try:
        app.run_polling()
    except Exception as e:
        logger.exception(f"Fatal error in app.run_polling: {e}")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.exception(f"Fatal crash on startup: {e}")
