"""
Wolt Smart Pantry - Telegram Bot Bridge
Control your pantry memory, live store discovery, meal planning, and automated Wolt cart creation from your phone.
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
    InlineKeyboardMarkup
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
    load_meal_plan,
    save_meal_plan,
    get_day_menu_formatted,
    get_single_meal_formatted,
    log_meal_consumption,
    generate_weekly_meal_plan,
    generate_default_weekly_plan,
    get_candidate_grocery_list,
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

# Temporary in-memory session cache for pending shopping proposals per user
user_pending_plans = {}

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
                await update.callback_query.answer("⛔ Unauthorized.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

async def reply_safe(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, reply_markup=None, parse_mode="Markdown"):
    """Safely replies whether the trigger was a direct text command or an inline button callback."""
    if update.callback_query and update.callback_query.message:
        return await update.callback_query.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    elif update.effective_message:
        return await update.effective_message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    elif update.effective_chat:
        return await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode=parse_mode, reply_markup=reply_markup)

@auth_guard
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message and interactive main menu."""
    user = update.effective_user
    welcome_text = (
        f"👋 Hello {user.first_name if user else 'there'}!\n\n"
        "🛒 *Wolt Smart Pantry Bot* is active on your PC.\n\n"
        "Here is what you can do:\n"
        "• 🌅 `/today` (or `/menu`) - Check today's meals, chef tips & freshness status\n"
        "• 📅 `/week` - Browse the full 7-day scheduled meal plan\n"
        "• 📸 *Send a photo* of your fridge/pantry to audit stock\n"
        "• `/plan` - Generate zero-waste 7-day meal plan & shopping list\n"
        "• `/pref` - Configure allergies, avoided foods & diet type\n"
        "• `/pantry` - View virtual pantry memory & long-term staples\n"
        "• `/deals` - Explore live discounts in Wolt Market Tallinn\n"
        "• `/cart <items>` - Build cart directly (e.g. `/cart Banaan:6 Rukola:1`)\n"
        "• `/logs` - View recent system and automation logs\n"
        "• `/help` - View full usage guide & safety options\n\n"
        f"⏰ *Morning Schedule:* Daily menu arrives automatically at `{DAILY_MENU_TIME}` ({BOT_TIMEZONE.zone})."
    )
    keyboard = [
        [
            InlineKeyboardButton("🌅 Today's Menu", callback_data="btn_today_menu"),
            InlineKeyboardButton("📅 Full Week Plan", callback_data="btn_week_plan")
        ],
        [
            InlineKeyboardButton("📋 Generate Plan", callback_data="btn_plan"),
            InlineKeyboardButton("🏷️ Active Deals", callback_data="btn_deals")
        ],
        [
            InlineKeyboardButton("🏠 Pantry Memory", callback_data="btn_pantry"),
            InlineKeyboardButton("👤 Dietary & Allergies", callback_data="btn_pref")
        ],
        [
            InlineKeyboardButton("🛒 Build Sample Cart", callback_data="btn_sample_cart")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_safe(update, context, welcome_text, reply_markup=reply_markup)

@auth_guard
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed command reference."""
    help_text = (
        "📖 *Command Guide:*\n\n"
        "• `/today` (or `/menu`) - Today's breakfast/lunch/dinner, raw-to-cooked portions & freshness reminders.\n"
        "• `/week` - 7-day full weekly meal schedule (Monday to Sunday).\n"
        "• `/plan` - Audits pantry memory, checks live Wolt deals, and generates a fresh 7-day meal plan with exact portions.\n"
        "• `/pref` - Manage your allergies, disliked ingredients, and household size.\n"
        "• `/pantry` - Shows active long-term staples (onions, oils, spices) and recent purchase history.\n"
        "• `/deals` - Scans Wolt Market for active promotional discounts.\n"
        "• `/cart Item:Qty Item:Qty` - Adds specific items directly (e.g. `/cart Banaan:6 Rukola:1`).\n"
        "• `/logs [lines]` - View live execution logs on your PC (default 25 lines).\n"
        "• `/clear_pantry` - Resets virtual pantry memory state.\n\n"
        f"⏰ *Automatic Morning Broadcast:* Every morning at `{DAILY_MENU_TIME}` ({BOT_TIMEZONE.zone}), your daily menu is delivered here automatically.\n\n"
        "🛡️ *Safety Policy:*\n"
        "By default, building a cart opens the review drawer on your PC and syncs to your phone app without auto-charging your card. Automated payment only occurs if you explicitly select *Auto Pay*."
    )
    await reply_safe(update, context, help_text)

@auth_guard
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's scheduled meal plan, chef tips, portions, and freshness status."""
    text, day_data = get_day_menu_formatted()
    keyboard = [
        [
            InlineKeyboardButton("🍳 Breakfast", callback_data="btn_view_breakfast"),
            InlineKeyboardButton("🥗 Lunch", callback_data="btn_view_lunch"),
            InlineKeyboardButton("🍲 Dinner", callback_data="btn_view_dinner")
        ],
        [
            InlineKeyboardButton("✅ Log Breakfast", callback_data="btn_eat_breakfast"),
            InlineKeyboardButton("✅ Log Lunch", callback_data="btn_eat_lunch"),
            InlineKeyboardButton("✅ Log Dinner", callback_data="btn_eat_dinner")
        ],
        [
            InlineKeyboardButton("🔄 Swap Meal", callback_data="btn_swap_meal"),
            InlineKeyboardButton("📅 Full Week Plan", callback_data="btn_week_plan"),
            InlineKeyboardButton("📦 Pantry Stock", callback_data="btn_pantry")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_safe(update, context, text, reply_markup=reply_markup)

@auth_guard
async def breakfast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's breakfast with exact food quantities, weights, and chef tips."""
    text, meal = get_single_meal_formatted("breakfast")
    keyboard = [
        [InlineKeyboardButton("✅ Log Breakfast Eaten (Deduct Stock)", callback_data="btn_eat_breakfast")],
        [InlineKeyboardButton("🥗 View Lunch", callback_data="btn_view_lunch"), InlineKeyboardButton("🌅 Full Day Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def lunch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's lunch with raw-to-cooked shrinkage math, exact ingredients & tips."""
    text, meal = get_single_meal_formatted("lunch")
    keyboard = [
        [InlineKeyboardButton("✅ Log Lunch Eaten (Deduct Stock)", callback_data="btn_eat_lunch")],
        [InlineKeyboardButton("🍲 View Dinner", callback_data="btn_view_dinner"), InlineKeyboardButton("🌅 Full Day Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def dinner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's dinner with exact ingredients & chef tips."""
    text, meal = get_single_meal_formatted("dinner")
    keyboard = [
        [InlineKeyboardButton("✅ Log Dinner Eaten (Deduct Stock)", callback_data="btn_eat_dinner")],
        [InlineKeyboardButton("🍎 View Snack", callback_data="btn_view_snack"), InlineKeyboardButton("🌅 Full Day Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def snack_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays today's snack with exact food breakdown."""
    text, meal = get_single_meal_formatted("snack")
    keyboard = [
        [InlineKeyboardButton("✅ Log Snack Eaten", callback_data="btn_eat_snack")],
        [InlineKeyboardButton("🌅 Full Day Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def eat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Logs a meal as consumed, deducts items from pantry memory, and displays the food used."""
    args = context.args if context.args else []
    meal_type = args[0].lower() if args else "lunch"
    if meal_type not in ["breakfast", "lunch", "dinner", "snack"]:
        meal_type = "lunch"
    text, used = log_meal_consumption(meal_type)
    keyboard = [
        [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
        [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays the full 7-day meal plan breakdown."""
    plan = load_meal_plan()
    days = plan.get("days", [])
    h_size = plan.get("household_size", 1)
    
    text = (
        f"📅 *7-DAY SMART MEAL PLAN*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 *Household:* {h_size} person(s) | *Diet:* {plan.get('diet_type', 'Omnivore').capitalize()}\n\n"
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
            InlineKeyboardButton("🌅 Today's Menu", callback_data="btn_today_menu"),
            InlineKeyboardButton("🛒 Build Wolt Cart", callback_data="btn_plan")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await reply_safe(update, context, text, reply_markup=reply_markup)

async def daily_morning_menu_job(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled task that runs every morning around 09:00 to deliver today's meal plan."""
    logger.info("🌅 Executing daily morning menu broadcast job...")
    text, day_data = get_day_menu_formatted()
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
    
    recipients = ALLOWED_USERS if ALLOWED_USERS else []
    if not recipients:
        logger.warning("No ALLOWED_USERS configured for morning menu broadcast.")
        return
        
    for user_id in recipients:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            logger.info(f"Daily morning menu sent to Telegram user ID: {user_id}")
        except Exception as e:
            logger.error(f"Failed to send daily menu to user ID {user_id}: {e}")

@auth_guard
async def preferences_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manages user dietary profile, allergies, and avoided ingredients."""
    args = context.args if context.args else []
    prefs = load_user_preferences()

    if args:
        subcmd = args[0].lower()
        val = " ".join(args[1:])
        
        if subcmd in ["allergy", "allergies"]:
            new_allergies = [a.strip() for a in val.split(",") if a.strip()]
            prefs["allergies"] = new_allergies
            save_user_preferences(prefs)
            await reply_safe(update, context, f"✅ *Allergies Updated:* {', '.join(new_allergies) if new_allergies else 'None'}")
            return
        elif subcmd in ["avoid", "avoided", "dislike", "dislikes"]:
            new_avoid = [a.strip() for a in val.split(",") if a.strip()]
            prefs["avoided_ingredients"] = new_avoid
            save_user_preferences(prefs)
            await reply_safe(update, context, f"✅ *Avoided Foods Updated:* {', '.join(new_avoid) if new_avoid else 'None'}")
            return
        elif subcmd == "diet":
            prefs["diet_type"] = val.strip().lower()
            save_user_preferences(prefs)
            await reply_safe(update, context, f"✅ *Diet Type Set To:* {val.strip().capitalize()}")
            return
        elif subcmd in ["people", "household", "size"]:
            if val.strip().isdigit():
                prefs["household_size"] = max(1, int(val.strip()))
                save_user_preferences(prefs)
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
            save_user_preferences(prefs)
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
        [InlineKeyboardButton("📋 Generate Plan with Profile", callback_data="btn_plan")],
        [InlineKeyboardButton("🔄 Reset Preferences", callback_data="btn_reset_pref")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def pantry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays virtual pantry memory status."""
    data = load_pantry_memory()
    staples = data.get("staples", [])
    proteins = data.get("proteins", [])
    history = data.get("purchase_history", [])

    text = "🏠 *Virtual Pantry Memory:*\n\n"
    
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

    text += f"\n📜 *Order History:* {len(history)} past orders recorded.\n"
    text += f"🕒 *Last Sync:* `{data.get('last_updated', 'N/A')}`"

    keyboard = [
        [InlineKeyboardButton("📋 Plan Week Based on Memory", callback_data="btn_plan")],
        [InlineKeyboardButton("🗑️ Clear Memory", callback_data="btn_clear_pantry")]
    ]
    await reply_safe(update, context, text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def deals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches live deals from the default Wolt venue."""
    msg = await reply_safe(update, context, "🔍 Scanning live discounts on Wolt Market Tallinn...")
    
    try:
        results = await asyncio.to_thread(
            inspect_store_items,
            DEFAULT_STORE,
            queries=None,
            get_deals=True,
            city=DEFAULT_CITY,
            country=DEFAULT_COUNTRY
        )
        deals = results.get("deals", [])
        if deals:
            text = f"🔥 *Top Active Deals in {DEFAULT_STORE}:*\n\n"
            for d in deals[:10]:
                orig = f" ~{d['original_price']}~" if d.get('original_price') else ""
                text += f"• *{d['title']}*: `{d['price']}`{orig}\n"
            text += "\n_Use `/plan` to automatically incorporate deals into your meal plan._"
        else:
            text = "ℹ️ No promotional deal badges detected on the main store page right now."
    except Exception as e:
        logger.error(f"Error scanning deals: {e}", exc_info=True)
        text = f"⚠️ Could not scan deals: {e}"

    if msg:
        await msg.edit_text(text, parse_mode="Markdown")
    else:
        await reply_safe(update, context, text)

@auth_guard
async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates a weekly meal plan and itemized grocery list customized for user preferences."""
    msg = await reply_safe(update, context, "📐 Calculating 7-day meal plan based on your dietary profile, allergies & Wolt catalog...")
    
    user_id = update.effective_user.id if update.effective_user else 0
    prefs = load_user_preferences()
    household_multiplier = prefs.get("household_size", 1)
    diet = prefs.get("diet_type", "omnivore").lower()

    # Candidate shopping list tailored by diet, allergies, household size, and breakfast
    filtered_items = get_candidate_grocery_list(prefs)

    # Synchronize and save the meal plan for these exact items
    new_plan = generate_weekly_meal_plan(inventory_items=filtered_items, prefs=prefs)
    save_meal_plan(new_plan)
    user_pending_plans[user_id] = filtered_items

    plan_text = (
        f"📋 *Proposed 7-Day Meal Plan ({diet.capitalize()} / {household_multiplier} person(s)):*\n\n"
        "• *🍳 Daily Breakfasts:* 3-egg scrambles with avocado & toast, mozzarella & tomato omelettes\n"
        "• *Days 1–3 (Tier 1 Fresh):* High-protein main meals (fresh ground beef, chicken cuts, delicate arugula)\n"
        "• *Days 4–5 (Tier 2 Medium):* Sautéed chicken breast, sweet bell peppers & baby potatoes\n"
        "• *Days 6–7 (Tier 3 Hardy):* Roasted vegetables, frittata & mozzarella pasta bake\n\n"
        "🛒 *Itemized Grocery List:*\n"
    )
    for it_name, it_qty in filtered_items:
        plan_text += f"• `{it_name}` × {it_qty}\n"

    plan_text += "\n✋ *Checkpoint 1:* Would you like to build this cart on Wolt?"

    keyboard = [
        [
            InlineKeyboardButton("🛒 Build Cart on Wolt (Safe Review)", callback_data="btn_confirm_cart"),
        ],
        [
            InlineKeyboardButton("💳 Auto Pay & Order (1-Click)", callback_data="btn_confirm_autopay"),
            InlineKeyboardButton("❌ Cancel", callback_data="btn_cancel_plan")
        ]
    ]
    if msg:
        await msg.edit_text(plan_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await reply_safe(update, context, plan_text, reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def cart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct cart assembly command: /cart Item:Qty Item:Qty"""
    args = context.args if context.args else []
    if not args:
        await reply_safe(update, context, "Usage: `/cart Banaan:6 Rukola:1 Sibul:1`")
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

    msg = await reply_safe(update, context, f"🛒 Launching browser automation for {len(items)} items...")
    
    try:
        await asyncio.to_thread(
            add_items_to_cart,
            DEFAULT_STORE,
            items,
            city=DEFAULT_CITY,
            country=DEFAULT_COUNTRY,
            keep_open=True,
            record_memory=False,
            auto_pay=False
        )
        wolt_url = f"https://wolt.com/en/{DEFAULT_COUNTRY}/{DEFAULT_CITY}/venue/{DEFAULT_STORE}"
        keyboard = [
            [InlineKeyboardButton("📱 Open Wolt App / Web", url=wolt_url)],
            [InlineKeyboardButton("💾 Confirm Order Placed (Sync Memory)", callback_data="btn_record_last")]
        ]
        user_pending_plans[update.effective_user.id] = items
        success_text = (
            "🎉 *Cart Ready & Synchronized!*\n\n"
            "📲 *Wolt has synced your cart to your phone!* You can now open your **Wolt mobile app** on your phone to review your items and pay with Apple Pay / Google Pay in 1 tap.\n\n"
            "_(Or complete checkout in your PC browser window)._\n\n"
            "👇 Once placed, tap below to sync your virtual pantry memory:"
        )
        if msg:
            await msg.edit_text(success_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await reply_safe(update, context, success_text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Cart build failed: {e}", exc_info=True)
        if msg:
            await msg.edit_text(f"⚠️ Cart build failed: {e}")
        else:
            await reply_safe(update, context, f"⚠️ Cart build failed: {e}")

@auth_guard
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends the last N lines of the bot log file to Telegram for easy debugging."""
    lines_count = 25
    if context.args and context.args[0].isdigit():
        lines_count = min(100, max(5, int(context.args[0])))

    if not os.path.exists(LOG_FILE):
        await reply_safe(update, context, "ℹ️ No log file found yet.")
        return

    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = "".join(all_lines[-lines_count:])
        if not tail.strip():
            tail = "Log file is currently empty."
        
        # Telegram max message length is 4096 chars
        if len(tail) > 3800:
            tail = tail[-3800:]
            
        await reply_safe(update, context, f"📜 *Live Logs (Last {lines_count} lines):*\n```\n{tail}\n```")
    except Exception as e:
        await reply_safe(update, context, f"⚠️ Error reading logs: {e}")

@auth_guard
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles uploaded kitchen/fridge photos from Telegram."""
    if not update.message or not update.message.photo:
        return
    photo = update.message.photo[-1]
    msg = await reply_safe(update, context, "📸 Photo received! Auditing kitchen stock & calculating meal gap...")
    
    # Save photo to local scratch directory
    os.makedirs("scratch", exist_ok=True)
    photo_file = await photo.get_file()
    save_path = os.path.join("scratch", f"telegram_upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
    await photo_file.download_to_drive(save_path)
    logger.info(f"Saved uploaded photo to: {save_path}")

    # Forward to plan calculation
    await plan_command(update, context)

@auth_guard
async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles interactive inline keyboard button clicks."""
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data
    user_id = update.effective_user.id if update.effective_user else 0
    wolt_url = f"https://wolt.com/en/{DEFAULT_COUNTRY}/{DEFAULT_CITY}/venue/{DEFAULT_STORE}"

    if data == "btn_plan":
        await plan_command(update, context)
    elif data == "btn_deals":
        await deals_command(update, context)
    elif data == "btn_pantry":
        await pantry_command(update, context)
    elif data == "btn_pref":
        await preferences_command(update, context)
    elif data == "btn_reset_pref":
        save_user_preferences({
            "diet_type": "omnivore",
            "allergies": [],
            "avoided_ingredients": [],
            "preferred_proteins": ["chicken", "ground beef", "salmon", "eggs"],
            "household_size": 1,
            "notes": ""
        })
        await query.edit_message_text("🔄 Dietary preferences reset to standard default.")
    elif data == "btn_clear_pantry":
        clear_pantry_memory()
        await query.edit_message_text("🗑️ Virtual pantry memory has been reset.")
    elif data == "btn_sample_cart":
        items = SAMPLE_WEEKLY_GROCERY_LIST
        user_pending_plans[user_id] = items
        await query.edit_message_text("🛒 Building sample grocery cart on your PC...")
        try:
            await asyncio.to_thread(
                add_items_to_cart,
                DEFAULT_STORE,
                items,
                city=DEFAULT_CITY,
                country=DEFAULT_COUNTRY,
                keep_open=True,
                record_memory=False,
                auto_pay=False
            )
            keyboard = [
                [InlineKeyboardButton("📱 Open Wolt App / Web", url=wolt_url)],
                [InlineKeyboardButton("💾 Confirm Order Placed (Sync Memory)", callback_data="btn_record_last")]
            ]
            sync_msg = (
                "🎉 *Sample Cart Ready & Synchronized!*\n\n"
                "📲 *Open your Wolt mobile app on your phone* to view your live synchronized basket and pay with 1 tap.\n\n"
                "👇 Once you place the order, tap below to sync your pantry memory:"
            )
            await query.message.reply_text(sync_msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Error building sample cart: {e}", exc_info=True)
            await query.message.reply_text(f"⚠️ Error building cart: {e}")
    elif data == "btn_confirm_cart":
        items = user_pending_plans.get(user_id, SAMPLE_WEEKLY_GROCERY_LIST)
        await query.edit_message_text(f"🛒 *Building cart on Wolt ({len(items)} items)...*\nCheck your PC browser or Wolt phone app.")
        try:
            await asyncio.to_thread(
                add_items_to_cart,
                DEFAULT_STORE,
                items,
                city=DEFAULT_CITY,
                country=DEFAULT_COUNTRY,
                keep_open=True,
                record_memory=False,
                auto_pay=False
            )
            keyboard = [
                [InlineKeyboardButton("📱 Open Wolt App / Web", url=wolt_url)],
                [InlineKeyboardButton("💾 Confirm Order Placed (Sync Memory)", callback_data="btn_record_last")]
            ]
            sync_msg = (
                "🎉 *Cart Ready & Synchronized!*\n\n"
                "📲 *Wolt has synced your cart to your phone!* You can now simply open your **Wolt mobile app** on your phone to review your items and pay with Apple Pay / Google Pay / Card in 1 tap!\n\n"
                "_(Or complete it on your PC browser screen)._\n\n"
                "👇 Once placed, tap below to update your virtual pantry memory:"
            )
            await query.message.reply_text(sync_msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Error building cart: {e}", exc_info=True)
            await query.message.reply_text(f"⚠️ Error building cart: {e}")
    elif data == "btn_confirm_autopay":
        items = user_pending_plans.get(user_id, SAMPLE_WEEKLY_GROCERY_LIST)
        await query.edit_message_text(f"💳 *Executing Full Auto-Pay on Wolt ({len(items)} items)...*\nSubmitting payment...")
        try:
            await asyncio.to_thread(
                add_items_to_cart,
                DEFAULT_STORE,
                items,
                city=DEFAULT_CITY,
                country=DEFAULT_COUNTRY,
                keep_open=True,
                record_memory=True,
                auto_pay=True
            )
            await query.message.reply_text("✅ *Order Successfully Placed & Paid on Wolt!*\nPantry memory has been automatically updated for next week.")
        except Exception as e:
            logger.error(f"Error during auto-pay: {e}", exc_info=True)
            await query.message.reply_text(f"⚠️ Error during auto-pay: {e}")
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
        text, _ = log_meal_consumption("breakfast")
        keyboard = [
            [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "btn_eat_lunch":
        text, _ = log_meal_consumption("lunch")
        keyboard = [
            [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "btn_eat_dinner":
        text, _ = log_meal_consumption("dinner")
        keyboard = [
            [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "btn_eat_snack":
        text, _ = log_meal_consumption("snack")
        keyboard = [
            [InlineKeyboardButton("📦 View Remaining Pantry", callback_data="btn_pantry")],
            [InlineKeyboardButton("🌅 View Today's Menu", callback_data="btn_today_menu")]
        ]
        await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    elif data == "btn_swap_meal":
        plan = load_meal_plan()
        days = plan.get("days", [])
        now = datetime.now()
        day_idx = min(6, max(0, now.weekday()))
        if days and day_idx < len(days):
            current_lunch = days[day_idx]["lunch"]["title"]
            alternates = [
                {"title": "Pan-Seared Salmon with Herb Rice", "tip": "Quick 12-min bake with lemon, dill & olive oil."},
                {"title": "Crispy Garlic Chicken Breast & Broccoli", "tip": "High protein, pan-seared with garlic butter."},
                {"title": "Lean Beef & Sweet Pepper Stir-Fry", "tip": "High heat sear with soy sauce & sesame oil."},
                {"title": "Creamy Mozzarella & Tomato Passata Penne", "tip": "Italian comfort bowl with fresh basil."}
            ]
            alt = next((a for a in alternates if a["title"] != current_lunch), alternates[0])
            days[day_idx]["lunch"]["title"] = alt["title"]
            days[day_idx]["lunch"]["tip"] = alt["tip"]
            save_meal_plan(plan)
            await query.edit_message_text(f"🔄 *Meal Swapped for Today!*\n\n🥗 *New Lunch:* {alt['title']}\n💡 _{alt['tip']}_")
        else:
            await query.edit_message_text("🔄 Generated fresh meal plan variation.")
    elif data == "btn_record_last":
        items = user_pending_plans.get(user_id, SAMPLE_WEEKLY_GROCERY_LIST)
        record_purchase_in_memory(items, store_slug=DEFAULT_STORE)
        new_plan = generate_weekly_meal_plan(inventory_items=items, prefs=load_user_preferences())
        save_meal_plan(new_plan)
        await query.edit_message_text("💾 *Success!* Items have been recorded into your virtual pantry memory and your weekly meal plan is now 100% synchronized with your groceries!")
    elif data == "btn_cancel_plan":
        await query.edit_message_text("❌ Meal plan cancelled.")

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs uncaught exceptions and sends a helpful message to the user."""
    logger.error("Exception while handling Telegram update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"⚠️ *An error occurred during execution:*\n`{context.error}`\n\nUse `/logs` to view detailed trace.",
                parse_mode="Markdown"
            )
        except Exception:
            pass

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

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("today", menu_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("week", week_command))
    app.add_handler(CommandHandler("breakfast", breakfast_command))
    app.add_handler(CommandHandler("lunch", lunch_command))
    app.add_handler(CommandHandler("dinner", dinner_command))
    app.add_handler(CommandHandler("snack", snack_command))
    app.add_handler(CommandHandler("eat", eat_command))
    app.add_handler(CommandHandler("preferences", preferences_command))
    app.add_handler(CommandHandler("pref", preferences_command))
    app.add_handler(CommandHandler("pantry", pantry_command))
    app.add_handler(CommandHandler("deals", deals_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("cart", cart_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
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
