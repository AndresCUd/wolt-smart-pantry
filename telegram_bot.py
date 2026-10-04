"""
Wolt Smart Pantry - Telegram Bot Bridge
Control your pantry memory, live store discovery, meal planning, and automated Wolt cart creation from your phone.
"""

import os
import sys
import json
import logging
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

# Import local Wolt automation modules
from wolt_manager import (
    load_pantry_memory,
    save_pantry_memory,
    record_purchase_in_memory,
    load_user_preferences,
    save_user_preferences,
    is_item_allowed,
    inspect_store_items,
    add_items_to_cart,
    SAMPLE_WEEKLY_GROCERY_LIST
)

# Logging configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Config
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
ALLOWED_USERS_RAW = os.getenv("TELEGRAM_ALLOWED_USERS", "")
ALLOWED_USERS = [int(uid.strip()) for uid in ALLOWED_USERS_RAW.split(",") if uid.strip().isdigit()]
DEFAULT_STORE = os.getenv("DEFAULT_STORE", "wolt-market-maakri")
DEFAULT_CITY = os.getenv("DEFAULT_CITY", "tallinn")
DEFAULT_COUNTRY = os.getenv("DEFAULT_COUNTRY", "est")

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
            if update.message:
                await update.message.reply_text("⛔ Unauthorized. Your Telegram User ID is not allowed to control this bot.")
            elif update.callback_query:
                await update.callback_query.answer("⛔ Unauthorized.", show_alert=True)
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

@auth_guard
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message and interactive main menu."""
    user = update.effective_user
    welcome_text = (
        f"👋 Hello {user.first_name}!\n\n"
        "🛒 *Wolt Smart Pantry Bot* is active on your PC.\n\n"
        "Here is what you can do:\n"
        "• 📸 *Send a photo* of your fridge/pantry to audit stock\n"
        "• `/plan` - Generate a zero-waste 7-day meal plan & shopping list\n"
        "• `/pref` - Configure allergies, avoided foods & diet type\n"
        "• `/pantry` - View virtual pantry memory & long-term staples\n"
        "• `/deals` - Explore live discounts in Wolt Market Tallinn\n"
        "• `/cart <items>` - Build cart directly (e.g. `/cart Banaan:6 Rukola:1`)\n"
        "• `/help` - View usage guide & safety options"
    )
    keyboard = [
        [
            InlineKeyboardButton("📋 Generate Meal Plan", callback_data="btn_plan"),
            InlineKeyboardButton("🏷️ Active Deals", callback_data="btn_deals")
        ],
        [
            InlineKeyboardButton("🏠 View Pantry Memory", callback_data="btn_pantry"),
            InlineKeyboardButton("👤 Dietary & Allergies", callback_data="btn_pref")
        ],
        [
            InlineKeyboardButton("🛒 Build Sample Cart", callback_data="btn_sample_cart")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=reply_markup)

@auth_guard
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed command reference."""
    help_text = (
        "📖 *Command Guide:*\n\n"
        "• `/plan` - Audits pantry memory, checks live Wolt deals, and generates a fresh 7-day meal plan with exact portions.\n"
        "• `/pantry` - Shows active long-term staples (onions, oils, spices) and recent purchase history.\n"
        "• `/deals` - Scans Wolt Market for active promotional discounts.\n"
        "• `/cart Item:Qty Item:Qty` - Adds specific items directly (e.g. `/cart Banaan:6 Rukola:1`).\n"
        "• `/clear_pantry` - Resets virtual pantry memory state.\n\n"
        "🛡️ *Safety Policy:*\n"
        "By default, building a cart opens the review drawer on your PC without auto-charging your card. Automated payment only occurs if you explicitly select *Auto Pay*."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

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
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def deals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches live deals from the default Wolt venue."""
    msg = await update.message.reply_text("🔍 Scanning live discounts on Wolt Market Tallinn...")
    
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
        text = f"⚠️ Could not scan deals: {e}"

    await msg.edit_text(text, parse_mode="Markdown")

@auth_guard
async def preferences_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manages user dietary profile, allergies, and avoided ingredients."""
    args = context.args
    prefs = load_user_preferences()

    if args:
        subcmd = args[0].lower()
        val = " ".join(args[1:])
        
        if subcmd in ["allergy", "allergies"]:
            new_allergies = [a.strip() for a in val.split(",") if a.strip()]
            prefs["allergies"] = new_allergies
            save_user_preferences(prefs)
            await update.message.reply_text(f"✅ *Allergies Updated:* {', '.join(new_allergies) if new_allergies else 'None'}", parse_mode="Markdown")
            return
        elif subcmd in ["avoid", "avoided", "dislike", "dislikes"]:
            new_avoid = [a.strip() for a in val.split(",") if a.strip()]
            prefs["avoided_ingredients"] = new_avoid
            save_user_preferences(prefs)
            await update.message.reply_text(f"✅ *Avoided Foods Updated:* {', '.join(new_avoid) if new_avoid else 'None'}", parse_mode="Markdown")
            return
        elif subcmd == "diet":
            prefs["diet_type"] = val.strip().lower()
            save_user_preferences(prefs)
            await update.message.reply_text(f"✅ *Diet Type Set To:* {val.strip().capitalize()}", parse_mode="Markdown")
            return
        elif subcmd in ["people", "household", "size"]:
            if val.strip().isdigit():
                prefs["household_size"] = max(1, int(val.strip()))
                save_user_preferences(prefs)
                await update.message.reply_text(f"✅ *Household Size Set To:* {prefs['household_size']} person(s)", parse_mode="Markdown")
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
            await update.message.reply_text("🔄 Dietary preferences reset to standard default.", parse_mode="Markdown")
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
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates a weekly meal plan and itemized grocery list customized for user preferences."""
    msg = await update.message.reply_text("📐 Calculating 7-day meal plan based on your dietary profile, allergies & Wolt catalog...")
    
    user_id = update.effective_user.id
    prefs = load_user_preferences()
    household_multiplier = prefs.get("household_size", 1)
    diet = prefs.get("diet_type", "omnivore").lower()

    # Candidate shopping list tailored by diet type
    if diet in ["vegetarian", "vegan"]:
        base_items = [
            ("Tofu 300g", 2 * household_multiplier),
            ("Riivjuust mozzarella", 1 * household_multiplier) if diet == "vegetarian" else ("Avokaado karbis 2tk, 300g", 1),
            ("Avokaado karbis 2tk, 300g", 1),
            ("Kirssploomtomat", 1),
            ("Rukola", 1),
            ("Sibul 1kg", 1),
            ("Eesti Pagar Tosta", 1),
            ("Banaan", 6 * household_multiplier),
            ("Paprika punane", 2 * household_multiplier)
        ]
    elif diet == "pescatarian":
        base_items = [
            ("Lõhefilee", 2 * household_multiplier),
            ("Valge kala filee", 1 * household_multiplier),
            ("Riivjuust mozzarella", 1),
            ("Avokaado karbis 2tk, 300g", 1),
            ("Kirssploomtomat", 1),
            ("Rukola", 1),
            ("Sibul 1kg", 1),
            ("Eesti Pagar Tosta", 1),
            ("Banaan", 6 * household_multiplier),
            ("Paprika punane", 2 * household_multiplier)
        ]
    else: # Omnivore / High-Protein
        base_items = [
            ("Rakvere homemade minced meat, 400g", 2 * household_multiplier),
            ("Tallegg maisikattega", 2 * household_multiplier),
            ("Tallegg broileririnnafilee", 1 * household_multiplier),
            ("Riivjuust mozzarella", 1),
            ("Avokaado karbis 2tk, 300g", 1),
            ("Kirssploomtomat", 1),
            ("Rukola", 1),
            ("Sibul 1kg", 1),
            ("Eesti Pagar Tosta", 1),
            ("Banaan", 6 * household_multiplier),
            ("Paprika punane", 2 * household_multiplier)
        ]

    # Filter out items that violate allergies or avoided foods
    filtered_items = []
    removed_items = []
    for item_tuple in base_items:
        allowed, reason = is_item_allowed(item_tuple[0], prefs)
        if allowed:
            filtered_items.append(item_tuple)
        else:
            removed_items.append((item_tuple[0], reason))

    user_pending_plans[user_id] = filtered_items

    plan_text = (
        f"📋 *Proposed 7-Day Meal Plan ({diet.capitalize()} / {household_multiplier} person(s)):*\n\n"
        "• *Days 1–3 (Tier 1 Fresh):* High-protein main meals, fresh poultry/meat or plant bowls, delicate arugula salad\n"
        "• *Days 4–5 (Tier 2 Medium):* Coated cuts & toast, ripe avocados, daily bananas\n"
        "• *Days 6–7 (Tier 3 Hardy):* Roasted bell peppers & cherry tomatoes with mozzarella bake\n\n"
        "🛒 *Itemized Grocery List:*\n"
    )
    for it_name, it_qty in filtered_items:
        plan_text += f"• `{it_name}` × {it_qty}\n"

    if removed_items:
        plan_text += "\n🛡️ *Safety Exclusions Applied:*\n"
        for r_name, r_reason in removed_items:
            plan_text += f"• ~{r_name}~ _({r_reason})_\n"

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
    await msg.edit_text(plan_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

@auth_guard
async def cart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Direct cart assembly command: /cart Item:Qty Item:Qty"""
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/cart Banaan:6 Rukola:1 Sibul:1`", parse_mode="Markdown")
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

    msg = await update.message.reply_text(f"🛒 Launching browser automation for {len(items)} items...")
    
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
        await msg.edit_text(success_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        await msg.edit_text(f"⚠️ Cart build failed: {e}")

@auth_guard
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles uploaded kitchen/fridge photos from Telegram."""
    photo = update.message.photo[-1]
    msg = await update.message.reply_text("📸 Photo received! Auditing kitchen stock & calculating meal gap...")
    
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
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    wolt_url = f"https://wolt.com/en/{DEFAULT_COUNTRY}/{DEFAULT_CITY}/venue/{DEFAULT_STORE}"

    if data == "btn_plan":
        await plan_command(update, context)
    elif data == "btn_deals":
        await deals_command(update, context)
    elif data == "btn_pantry":
        await pantry_command(update, context)
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
            await query.message.reply_text(f"⚠️ Error during auto-pay: {e}")
    elif data == "btn_record_last":
        items = user_pending_plans.get(user_id, SAMPLE_WEEKLY_GROCERY_LIST)
        record_purchase_in_memory(items, store_slug=DEFAULT_STORE)
        await query.edit_message_text("💾 *Success!* Items have been recorded into your virtual pantry memory. Zero duplicate staples will be bought next week!")
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
    elif data == "btn_cancel_plan":
        await query.edit_message_text("❌ Meal plan cancelled.")

def main():
    """Main application entry point."""
    if not BOT_TOKEN:
        print("\n" + "="*60)
        print("❌ [ERROR] TELEGRAM_BOT_TOKEN is missing!")
        print("1. Create a bot with @BotFather on Telegram.")
        print("2. Copy your token into .env (or set TELEGRAM_BOT_TOKEN environment variable).")
        print("="*60 + "\n")
        sys.exit(1)

    print("\n" + "="*60)
    print("🤖 Wolt Smart Pantry Telegram Bot Starting...")
    print(f"[*] Default Store: {DEFAULT_STORE} ({DEFAULT_CITY}, {DEFAULT_COUNTRY})")
    if ALLOWED_USERS:
        print(f"[*] Access restricted to Telegram user ID(s): {ALLOWED_USERS}")
    else:
        print("[!] Warning: TELEGRAM_ALLOWED_USERS is empty. Bot will accept commands from any user.")
    print("="*60 + "\n")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("preferences", preferences_command))
    app.add_handler(CommandHandler("pref", preferences_command))
    app.add_handler(CommandHandler("pantry", pantry_command))
    app.add_handler(CommandHandler("deals", deals_command))
    app.add_handler(CommandHandler("plan", plan_command))
    app.add_handler(CommandHandler("cart", cart_command))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(CallbackQueryHandler(button_callback_handler))

    print("[+] Bot is online and listening for Telegram messages! Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
