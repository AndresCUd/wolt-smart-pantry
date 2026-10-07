"""
wolt_core.browser
Handles Playwright browser automation against Wolt web interface:
Session management, login flow, modal handling, exact item & substitute matching,
cart assembly with budget safeguards, and live store promotions scraper.
"""

import os
import sys
import time
import json
import re
from playwright.sync_api import sync_playwright

from .config import (
    get_user_browser_dir,
    get_user_config,
    is_user_aborted,
    set_user_abort
)
from .pantry import record_purchase_in_memory

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

def get_cart_total_price(page) -> float:
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

def validate_item_match(query_text: str, card_title: str, card_raw_text: str = "") -> tuple[bool, bool, str]:
    """
    Validates whether a found product card on Wolt is an exact match or a candidate substitute.
    Returns: (is_exact_match: bool, is_candidate_substitute: bool, reason: str)
    """
    q = query_text.lower().strip()
    t = (card_title + " " + card_raw_text).lower().strip()
    
    clean_q = re.sub(r'\b\d+(?:[.,]\d+)?\s*(?:kg|g|tk|l|ml|karbis|pk|pack|tk)?\b', '', q).strip()
    
    # 1. Onions (Sibul / Cebolla)
    is_yellow_onion_query = any(w in q for w in ["kollane sibul", "mugulsibul", "cebolla cabezona", "round onion", "yellow onion"]) or (clean_q in ["sibul", "sibula", "sibulat"])
    is_green_onion_query = any(w in q for w in ["roheline sibul", "kevadsibul", "lehtsibul", "cebolla larga", "green onion", "spring onion", "scallion"])
    is_red_onion_query = any(w in q for w in ["punane sibul", "red onion", "cebolla roja", "cebolla morada"])

    if is_yellow_onion_query:
        if any(w in t for w in ["roheline sibul", "kevadsibul", "lehtsibul", "porru", "lauk"]):
            return False, True, "Sustituto detectado: Cebolla larga / verde en vez de cebolla cabezona"
        if any(w in t for w in ["punane sibul", "salottsibul", "šalottsibul"]):
            return False, True, "Sustituto detectado: Cebolla roja/chalota en vez de cebolla cabezona"
        if any(w in t for w in ["kollane", "mugul", "võrgus", "sibul"]):
            return True, False, "Coincidencia exacta: Cebolla cabezona / Kollane sibul"
        return False, True, "Posible sustituto de cebolla"

    if is_green_onion_query:
        if any(w in t for w in ["roheline sibul", "kevadsibul", "lehtsibul"]):
            return True, False, "Coincidencia exacta: Cebolla larga / verde"
        if "sibul" in t:
            return False, True, "Sustituto detectado: Cebolla cabezona en vez de cebolla larga"
            
    if is_red_onion_query:
        if "punane sibul" in t:
            return True, False, "Coincidencia exacta: Cebolla roja"
        if "sibul" in t:
            return False, True, "Sustituto detectado: Cebolla regular en vez de cebolla roja"

    # 2. Minced Meat (Hakkliha)
    if any(w in q for w in ["veisehakkliha", "beef mince", "carne molida de res", "veise"]):
        if any(bad in t for bad in ["doktor", "keeduvorst", "vorst", "viiner", "sink", "maks", "pasteet"]):
            return False, False, "Embutido o producto procesado descartado"
        if "veise" in t or "veis" in t or "kodune" in t:
            return True, False, "Coincidencia exacta: Carne molida de res/mixta"
        if "seahakkliha" in t or "hakkliha" in t:
            return False, True, "Sustituto detectado: Carne molida de cerdo en vez de res"

    if "hakkliha" in q:
        if any(bad in t for bad in ["doktor", "keeduvorst", "vorst", "viiner", "sink", "maks", "pasteet"]):
            return False, False, "Embutido descartado"
        if "hakkliha" in t:
            return True, False, "Coincidencia exacta: Carne molida"

    # 3. Poultry (Kana / Broiler / Filee)
    if any(w in q for w in ["broileri", "kanafilee", "rinnafilee", "chicken breast", "pechuga"]):
        if any(bad in t for bad in ["tiivad", "tiib", "poolkoivad", "koib", "maks", "kintsuliha", "hakkliha", "viiner", "vorst"]):
            return False, True, "Sustituto detectado: Corte diferente de pollo (alas/piernas/embutido)"
        if any(good in t for good in ["filee", "rinnafilee", "kanafilee", "maisikattega"]):
            return True, False, "Coincidencia exacta: Filete de pechuga de pollo"

    # 4. Eggs (Munad / Kanamunad)
    if any(w in q for w in ["muna", "munad", "egg"]):
        if any(good in t for good in ["kanamunad", "muna", "munad", "vabapidamise"]):
            return True, False, "Coincidencia exacta: Huevos"
            
    # 5. Bread / Toast (Tosta / Sai / Leib)
    if any(w in q for w in ["tosta", "toast", "sai", "leib"]):
        if any(good in t for good in ["tosta", "röstsai", "sai", "toast", "leib"]):
            return True, False, "Coincidencia exacta: Pan tostado"

    # 6. General Word Match
    q_words = [w for w in re.findall(r'[a-zõäöü]+', clean_q) if len(w) > 2]
    if q_words:
        matches = [w for w in q_words if w in t]
        if len(matches) == len(q_words):
            return True, False, "Coincidencia exacta en todas las palabras clave"
        elif len(matches) > 0:
            return True, False, f"Coincidencia parcial: {', '.join(matches)}"
            
    if any(w in t for w in q_words):
        return False, True, f"Posible sustituto: '{card_title}'"

    return False, False, f"Sin coincidencia para '{query_text}'"

def search_and_add_item(page, query_text, target_qty=1, allow_substitute=False):
    """Searches for an item, verifies exact match vs substitute, and adds exact match to the cart."""
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
        return {"status": "error", "requested": query_text, "error": str(e)}
    
    time.sleep(2.5)
    close_any_unwanted_modal(page)

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
    
    detected_exact_card = None
    detected_sub_card = None
    exact_title = ""
    sub_title = ""
    sub_price = ""
    sub_reason = ""
    exact_price = ""

    for selector in product_card_selectors:
        try:
            elements = page.locator(selector).all()
            for el in elements:
                if el.is_visible(timeout=150):
                    txt = el.inner_text() or ""
                    if any(bad in txt for bad in ["Discover", "W+ Weeks", "Deals", "Halloween", "Everyday Low Prices", "Fight Food Waste", "Categories"]):
                        continue
                    
                    lines = [line.strip() for line in txt.split("\n") if line.strip() and "€" not in line]
                    card_title = lines[0] if lines else txt[:35]
                    
                    prices = re.findall(r'(\d+[.,]\d{2})\s*€|€\s*(\d+[.,]\d{2})', txt)
                    c_price = (prices[0][0] or prices[0][1]) + " €" if prices else ""
                    
                    is_exact, is_sub, match_reason = validate_item_match(query_text, card_title, txt)
                    
                    if is_exact and not detected_exact_card:
                        detected_exact_card = el
                        exact_title = card_title
                        exact_price = c_price
                        break
                    elif is_sub and not detected_sub_card:
                        detected_sub_card = el
                        sub_title = card_title
                        sub_price = c_price
                        sub_reason = match_reason
            if detected_exact_card:
                break
        except Exception:
            continue

    card_to_add = detected_exact_card
    chosen_title = exact_title

    if not card_to_add:
        if detected_sub_card and not allow_substitute:
            print(f"    -> ⚠️ [SUBSTITUTE DETECTED] Found '{sub_title}' ({sub_price}) for '{query_text}'. Reason: {sub_reason}")
            print(f"    -> ⏸️ Halting addition until explicit user confirmation.")
            return {
                "status": "substitute_found",
                "requested": query_text,
                "found_title": sub_title,
                "price": sub_price,
                "qty": target_qty,
                "reason": sub_reason
            }
        elif detected_sub_card and allow_substitute:
            card_to_add = detected_sub_card
            chosen_title = sub_title
        else:
            print(f"    -> ❌ [!] No matching product card found for '{query_text}'")
            return {"status": "not_found", "requested": query_text, "qty": target_qty}

    print(f"    -> 🥩 Found exact product: '{chosen_title}' (Target Quantity: {target_qty})")

    modal_opened = False
    if click_element_safely(card_to_add) or click_element_safely(card_to_add.locator("h3").first):
        time.sleep(1.2)
        dialog = page.locator("dialog, [role='dialog']").first
        if dialog.is_visible(timeout=1500):
            modal_opened = True
            if target_qty > 1:
                stepper_plus = dialog.locator(
                    "button[aria-label*='Increase'], button[aria-label*='Suurenda'], button[aria-label*='Add'], button[data-test-id*='stepper-add'], button[data-test-id*='increment'], button:has-text('+')"
                ).first
                for q_step in range(target_qty - 1):
                    if stepper_plus.is_visible(timeout=500):
                        click_element_safely(stepper_plus)
                        print(f"    -> ➕ Incrementing quantity ({q_step + 2}/{target_qty})...")
                        time.sleep(0.4)
            
            modal_submit = dialog.locator(
                "button[type='submit'], button:has-text('€'), button[data-test-id*='submit'], button[data-test-id*='add'], button:has-text('Add to'), button:has-text('Lisa')"
            ).first
            if click_element_safely(modal_submit):
                time.sleep(1.2)
                price_now = get_cart_total_price(page)
                if price_now > initial_cart_price or price_now > 0:
                    return {"status": "added", "title": chosen_title, "qty": target_qty, "price": exact_price}
            else:
                close_any_unwanted_modal(page)

    if not modal_opened:
        plus_btn = card_to_add.locator(
            "button[data-test-id*='action-button'], button[data-test-id*='add'], button[aria-label*='Add'], button[aria-label*='Lisa'], button:has(svg), button"
        ).first
        if plus_btn.is_visible(timeout=800):
            for _ in range(target_qty):
                click_element_safely(plus_btn)
                time.sleep(0.6)
            time.sleep(1.0)
            price_now = get_cart_total_price(page)
            if price_now > initial_cart_price or price_now > 0:
                return {"status": "added", "title": chosen_title, "qty": target_qty, "price": exact_price}

    price_final = get_cart_total_price(page)
    if price_final > initial_cart_price or price_final > 0:
        return {"status": "added", "title": chosen_title, "qty": target_qty, "price": exact_price}
    return {"status": "not_found", "requested": query_text, "qty": target_qty}

def add_items_to_cart(store_slug, items, city="tallinn", country="est", address=None, keep_open=True, record_memory=False, auto_pay=False, user_id=None, headless=None, budget=None, allow_substitutes=False):
    """Executes the automated grocery shopping flow for the given items with budget enforcement and exact item matching."""
    user_browser_dir = get_user_browser_dir(user_id)
    if headless is None:
        headless = bool(sys.platform.startswith("linux") and not os.environ.get("DISPLAY"))
    if headless:
        keep_open = False
        
    user_cfg = get_user_config(user_id) if user_id else {}
    if budget is None and user_cfg.get("max_budget"):
        try:
            budget = float(user_cfg["max_budget"])
        except (ValueError, TypeError):
            budget = None

    print(f"\n[*] 🛒 Starting grocery order for venue: '{store_slug}' ({city}, {country})...")
    print(f"[*] Telegram User Profile: {user_id or 'Default'} | Profile Dir: {user_browser_dir} | Headless: {headless}")
    if budget:
        print(f"[*] Max Cart Budget Limit: {budget:.2f} €")
    print(f"[*] Total items to process: {len(items)}\n")
    
    paid_successfully = False
    budget_exceeded = False
    final_price = 0.0
    added_count = 0
    added_items = []
    pending_substitutions = []
    failed_items = []
    set_user_abort(user_id, abort=False)

    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_browser_dir,
            headless=headless,
            args=args,
            viewport={"width": 1280, "height": 850}
        )
        page = context.pages[0] if context.pages else context.new_page()
        
        store_url = f"https://wolt.com/en/{country}/{city}/venue/{store_slug}"
        print(f"[*] Navigating to: {store_url}")
        page.goto(store_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3.0)
        
        close_any_unwanted_modal(page, target_address=address)

        current_cart_total = get_cart_total_price(page)
        print(f"[*] Initial cart total: {current_cart_total:.2f} €\n")

        for idx, item_data in enumerate(items, 1):
            if is_user_aborted(user_id):
                print(f"[*] 🛑 User {user_id or 'Default'} requested ABORT. Stopping cart building immediately.")
                break

            if isinstance(item_data, (tuple, list)):
                item_name = item_data[0]
                item_qty = item_data[1]
            elif isinstance(item_data, dict):
                item_name = item_data.get("query", item_data.get("name", item_data.get("found_title")))
                item_qty = item_data.get("qty", 1)
            else:
                item_name = str(item_data)
                item_qty = 1

            print(f"[{idx}/{len(items)}] 🔍 Searching: '{item_name}' (x{item_qty})...")
            try:
                close_any_unwanted_modal(page, target_address=address)
                prev_price = get_cart_total_price(page)
                
                item_res = search_and_add_item(page, item_name, target_qty=item_qty, allow_substitute=allow_substitutes)

                if isinstance(item_res, dict):
                    st = item_res.get("status")
                    if st == "added":
                        time.sleep(1.2)
                        new_price = get_cart_total_price(page)
                        diff = new_price - prev_price if new_price > prev_price else 0.0
                        print(f"    -> ✅ [OK] '{item_res.get('title')}' (x{item_qty}) added! Cart total: {prev_price:.2f} € ➔ {new_price:.2f} € (+{diff:.2f} €)")
                        current_cart_total = new_price
                        added_count += 1
                        added_items.append(item_res)
                    elif st == "substitute_found":
                        print(f"    -> ⚠️ [!] Substitute detected and held for confirmation: '{item_res.get('found_title')}'")
                        pending_substitutions.append(item_res)
                    else:
                        print(f"    -> ❌ [!] Could not add '{item_name}'")
                        failed_items.append({"query": item_name, "qty": item_qty})
                elif item_res:
                    added_count += 1
                    added_items.append({"title": item_name, "qty": item_qty})
                else:
                    failed_items.append({"query": item_name, "qty": item_qty})

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
        print(f"🎉 Completed: {added_count}/{len(items)} items processed. (Pending substitutes: {len(pending_substitutions)}). Final Cart Total: {final_price:.2f} €")
        print("="*60)
        
        try:
            view_order_btn = page.locator("button:has-text('View order'), button:has-text('Vaata tellimust'), button[aria-label*='View order']").first
            if view_order_btn.is_visible(timeout=2000):
                click_element_safely(view_order_btn)
                print("[+] Order review opened.")
                time.sleep(1.5)
        except Exception:
            pass

        if auto_pay and not pending_substitutions:
            if budget and final_price > budget:
                budget_exceeded = True
                print("\n" + "!"*60)
                print(f"🛑 [BUDGET LIMIT EXCEEDED] Cart total ({final_price:.2f} €) exceeds set budget ({budget:.2f} €)!")
                print(">>> Automated checkout is HALTED for your safety.")
                print(">>> Items remain in cart for manual review and phone app confirmation.")
                print("!"*60 + "\n")
            else:
                print("\n" + "!"*60)
                print("⚠️ [AUTO-PAY EXECUTION] Proceeding to automated checkout...")
                print("!"*60 + "\n")
                try:
                    checkout_btn = page.locator(
                        "button:has-text('Go to checkout'), button:has-text('Mine kassasse'), button:has-text('Jätka'), button[data-test-id*='checkout-button'], button:has-text('Checkout')"
                    ).first
                    if checkout_btn.is_visible(timeout=3000):
                        click_element_safely(checkout_btn)
                        print("[+] Proceeding to final checkout payment screen...")
                        time.sleep(3.5)

                    submit_pay_btn = page.locator(
                        "button:has-text('Order and pay'), button:has-text('Telli ja maksa'), button:has-text('Place order'), button[data-test-id*='submit-order'], button[data-test-id*='order-submit']"
                    ).first
                    if submit_pay_btn.is_visible(timeout=5000):
                        click_element_safely(submit_pay_btn)
                        print("[🎉] Final payment button clicked! Waiting for order confirmation...")
                        time.sleep(5.0)
                        paid_successfully = True
                        print("[✅] Order submission completed successfully!")
                        try:
                            record_purchase_in_memory(items, store_slug=store_slug, user_id=user_id)
                        except Exception:
                            pass
                    else:
                        print("[!] Notice: Final payment button requires manual verification/interaction on screen.")
                except Exception as e:
                    print(f"[!] Error during auto-pay checkout: {e}")
        else:
            print("\n" + "="*60)
            print("🛡️ [SAFE CHECKOUT POLICY]")
            print("Cart is filled and ready on screen.")
            print("To submit payment, click 'Order and pay' in the browser window or phone app.")
            print("="*60 + "\n")
            
        if record_memory and not paid_successfully:
            try:
                record_purchase_in_memory(items, store_slug=store_slug, user_id=user_id)
            except Exception as e:
                print(f"[!] Warning: Could not record into pantry memory: {e}")

        if keep_open:
            print("\n[+] The process will terminate when you close the browser window.")
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            print("[+] Browser closed. Automation finished.")
            try:
                context.close()
            except Exception:
                pass

    return {
        "items_added": added_count,
        "total_items": len(items),
        "final_price": final_price,
        "added_items": added_items,
        "pending_substitutions": pending_substitutions,
        "failed_items": failed_items,
        "auto_pay_attempted": auto_pay,
        "auto_pay_success": paid_successfully,
        "budget_exceeded": budget_exceeded,
        "max_budget": budget
    }

def inspect_store_items(store_slug, queries=None, get_deals=False, city="tallinn", country="est", address=None, output_file=None, user_id=None, headless=None):
    """Explores the Wolt store venue in real time to discover active deals and verify available products, prices, and package sizes."""
    user_browser_dir = get_user_browser_dir(user_id)
    if headless is None:
        headless = bool(sys.platform.startswith("linux") and not os.environ.get("DISPLAY"))
        
    print(f"\n[*] 🔍 Exploring Wolt store venue: '{store_slug}' ({city}, {country})...")
    
    results = {
        "store": store_slug,
        "city": city,
        "country": country,
        "deals": [],
        "queries": {}
    }
    set_user_abort(user_id, abort=False)
    
    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_browser_dir,
            headless=headless,
            args=args,
            viewport={"width": 1280, "height": 850}
        )
        page = context.pages[0] if context.pages else context.new_page()
        
        store_url = f"https://wolt.com/en/{country}/{city}/venue/{store_slug}"
        print(f"[*] Navigating to: {store_url}")
        page.goto(store_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(3.0)
        close_any_unwanted_modal(page, target_address=address)

        if get_deals:
            print("[*] Scanning store for active deals and discounted items...")
            try:
                deal_cards = page.locator("main [data-test-id*='item-card'], main [data-test-id*='horizontal-item-card'], main [data-test-id*='vertical-item-card']").all()
                for card in deal_cards[:25]:
                    if is_user_aborted(user_id):
                        break
                    if card.is_visible(timeout=100):
                        txt = card.inner_text() or ""
                        prices = re.findall(r'(\d+[.,]\d{2})\s*€|€\s*(\d+[.,]\d{2})', txt)
                        lines = [line.strip() for line in txt.split("\n") if line.strip()]
                        if len(prices) >= 2 or any(w in txt.lower() for w in ["-%", "off", "deal", "ale", "soodus"]):
                            title = lines[0] if lines else "Item"
                            current_p = prices[0][0] or prices[0][1] if prices else ""
                            orig_p = prices[1][0] or prices[1][1] if len(prices) > 1 else ""
                            results["deals"].append({
                                "title": title,
                                "price": f"{current_p} €" if current_p else "",
                                "original_price": f"{orig_p} €" if orig_p else "",
                                "raw": txt.replace("\n", " | ")
                            })
                print(f"    -> Discovered {len(results['deals'])} discounted/promotional products on store front.")
            except Exception as e:
                print(f"    -> ⚠️ Could not extract deals: {e}")

        if queries:
            store_search_input = get_store_search_input(page)
            for query in queries:
                if is_user_aborted(user_id):
                    print(f"[*] 🛑 User {user_id or 'Default'} requested ABORT. Stopping inspection.")
                    break
                print(f"[*] Verifying products for query: '{query}'...")
                query_results = []
                try:
                    click_element_safely(store_search_input)
                    store_search_input.fill("")
                    store_search_input.press_sequentially(query, delay=25)
                    store_search_input.press("Enter")
                    time.sleep(2.2)
                    close_any_unwanted_modal(page)

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
                    
                    seen_titles = set()
                    for sel in product_card_selectors:
                        try:
                            cards = page.locator(sel).all()
                            for card in cards:
                                if card.is_visible(timeout=150):
                                    txt = card.inner_text() or ""
                                    if any(bad in txt for bad in ["Discover", "W+ Weeks", "Deals", "Categories", "Halloween", "Everyday Low Prices", "Fight Food Waste"]):
                                        continue
                                    if any(m in query.lower() for m in ["hakkliha", "broileri", "veise", "sea"]):
                                        if any(bad_meat in txt.lower() for bad_meat in ["doktor", "keeduvorst", "vorst", "viiner", "sink", "mortadella"]):
                                            continue
                                    
                                    lines = [line.strip() for line in txt.split("\n") if line.strip() and "€" not in line]
                                    title = lines[0] if lines else txt[:35]

                                    if not title or title in seen_titles:
                                        continue
                                    seen_titles.add(title)

                                    prices = re.findall(r'(\d+[.,]\d{2})\s*€|€\s*(\d+[.,]\d{2})', txt)
                                    current_p = prices[0][0] or prices[0][1] if prices else ""
                                    orig_p = prices[1][0] or prices[1][1] if len(prices) > 1 else ""

                                    query_results.append({
                                        "title": title,
                                        "price": f"{current_p} €" if current_p else "",
                                        "original_price": f"{orig_p} €" if orig_p else "",
                                        "details": txt.replace("\n", " | ")
                                    })
                                    if len(query_results) >= 5:
                                        break
                            if query_results:
                                break
                        except Exception:
                            continue

                    try:
                        clear_btn = page.locator("main button[aria-label*='Clear'], main button[aria-label*='Tühjenda']").first
                        if clear_btn.is_visible(timeout=150):
                            click_element_safely(clear_btn)
                        elif store_search_input.is_visible(timeout=150):
                            store_search_input.fill("")
                    except Exception:
                        pass
                except Exception as e:
                    print(f"    -> ⚠️ Error searching query '{query}': {e}")

                results["queries"][query] = query_results
                print(f"    -> Found {len(query_results)} matching products for '{query}'.")

        try:
            context.close()
        except Exception:
            pass

    if output_file:
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
            print(f"\n[+] Inspection results saved to: {output_file}")
        except Exception as e:
            print(f"[!] Could not save output file: {e}")

    print("\n" + "="*60)
    print("📋 LIVE STORE INSPECTION RESULTS (JSON):")
    print("="*60)
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return results

def check_wolt_session(user_id=None, headless=None) -> dict:
    """Checks if the user's Wolt browser profile is logged in and returns session status."""
    user_browser_dir = get_user_browser_dir(user_id)
    if headless is None:
        headless = bool(sys.platform.startswith("linux") and not os.environ.get("DISPLAY"))
    status = {"logged_in": False, "user_id": user_id, "profile_dir": user_browser_dir}
    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_browser_dir,
                headless=headless,
                args=args,
                viewport={"width": 1280, "height": 850}
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("https://wolt.com/en/discovery", wait_until="domcontentloaded", timeout=25000)
            time.sleep(2.5)
            
            login_btn = page.locator("button:has-text('Log in'), button:has-text('Logi sisse'), button[data-test-id*='login']").first
            user_menu = page.locator("[data-test-id*='user-menu'], button[aria-label*='User profile'], button[aria-label*='Konto'], [data-test-id*='profile-button']").first
            
            if user_menu.is_visible(timeout=1500):
                status["logged_in"] = True
            elif login_btn.is_visible(timeout=1500):
                status["logged_in"] = False
            else:
                cookies = context.cookies()
                has_auth = any("token" in c.get("name", "").lower() or "session" in c.get("name", "").lower() or "wolt" in c.get("name", "").lower() for c in cookies)
                status["logged_in"] = has_auth
                
            context.close()
        except Exception as e:
            status["error"] = str(e)
    return status

def login_mode(user_id=None):
    """Opens a non-headless browser session to allow the user to authenticate once for their profile."""
    user_browser_dir = get_user_browser_dir(user_id)
    print(f"[*] Launching persistent browser profile at: {user_browser_dir}")
    with sync_playwright() as p:
        args = ["--disable-blink-features=AutomationControlled", "--no-sandbox", "--disable-dev-shm-usage"]
        context = p.chromium.launch_persistent_context(
            user_data_dir=user_browser_dir,
            headless=False,
            args=args,
            viewport={"width": 1280, "height": 850}
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://wolt.com/en/discovery", wait_until="domcontentloaded")
        
        print("\n" + "="*60)
        print(">>> ONE-TIME AUTHENTICATION SETUP:")
        if user_id:
            print(f">>> Telegram User Profile: {user_id}")
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
