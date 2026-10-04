"""
Wolt Smart Shopping Automation Assistant
Automates item search, quantity selection, and cart creation on Wolt with persistent session handling.
"""

import os
import sys
import time
import re
import argparse

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

from playwright.sync_api import sync_playwright

# Persistent browser profile path (relative and portable)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, ".wolt_profile")

# Default single-item test list
DEFAULT_GROCERY_LIST = [
    ("Tallegg maisikattega", 1)
]

# Verified weekly grocery list with real fresh meats and balanced produce
FULL_GROCERY_LIST = [
    ("Rakvere kodune hakkliha", 2),      # 2x 400g = 800g fresh mixed beef/pork mince
    ("Tallegg maisikattega", 2),         # 2x 280g = 560g crispy corn chicken fillet
    ("Tallegg broileririnnafilee", 1),   # 1x 400g-500g fresh chicken breast fillet
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

def add_items_to_cart(store_slug, items, city="tallinn", country="est", address=None, keep_open=True):
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
        
        # Open order summary for user review
        try:
            view_order_btn = page.locator("button:has-text('View order'), button:has-text('Vaata tellimust'), button[aria-label*='View order']").first
            if view_order_btn.is_visible(timeout=2000):
                click_element_safely(view_order_btn)
                print("[+] Order review opened.")
        except Exception:
            pass
            
        if keep_open:
            print("\n[+] Cart is ready in the browser. The process will terminate when you close the window.")
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            print("[+] Browser closed. Automation finished.")
            try:
                context.close()
            except Exception:
                pass

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wolt Smart Shopping Automation Assistant")
    parser.add_argument("mode", choices=["login", "add"], help="Mode: 'login' for one-time setup, 'add' to create cart")
    parser.add_argument("--store", default="wolt-market-maakri", help="Wolt venue store slug")
    parser.add_argument("--city", default="tallinn", help="City name (default: tallinn)")
    parser.add_argument("--country", default="est", help="Country code (default: est)")
    parser.add_argument("--address", help="Optional delivery address filter")
    parser.add_argument("--full", action="store_true", help="Run full weekly grocery list")
    parser.add_argument("--items", nargs="+", help="Custom items list to purchase")
    
    args = parser.parse_args()
    
    if args.items:
        items = [(it, 1) for it in args.items]
    elif args.full:
        items = FULL_GROCERY_LIST
    else:
        items = DEFAULT_GROCERY_LIST
    
    if args.mode == "login":
        login_mode()
    elif args.mode == "add":
        add_items_to_cart(args.store, items, city=args.city, country=args.country, address=args.address)
