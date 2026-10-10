"""
wolt_core.pantry
Manages virtual pantry memory, long-term staples, fresh proteins, produce,
order purchase history, photo-based restocking, and ingredient deductions.
"""

import os
import time
import json
from datetime import datetime

from .config import get_pantry_file

def load_pantry_memory(user_id=None) -> dict:
    """Loads virtual pantry memory state from disk for specific user or creates an initial structure."""
    target_file = get_pantry_file(user_id)
    if os.path.exists(target_file):
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "staples": [],
        "proteins": [],
        "produce": [],
        "purchase_history": []
    }

def save_pantry_memory(data: dict, user_id=None):
    """Saves updated pantry memory to JSON file for specific user."""
    target_file = get_pantry_file(user_id)
    data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[💾] Pantry memory state saved: {target_file}")

def clear_pantry_memory(user_id=None):
    """Resets pantry memory for specific user."""
    empty = {
        "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "staples": [],
        "proteins": [],
        "produce": [],
        "purchase_history": []
    }
    save_pantry_memory(empty, user_id=user_id)
    print(f"[🗑️] Virtual pantry memory reset for user: {user_id or 'default'}")

def record_purchase_in_memory(items, store_slug="wolt-market-maakri", user_id=None):
    """Records a completed grocery order into the persistent pantry memory state for specific user."""
    data = load_pantry_memory(user_id=user_id)
    now_str = time.strftime("%Y-%m-%dT%H:%M:%S")
    
    parsed_items = []
    for it in items:
        if isinstance(it, (tuple, list)):
            name, qty = it[0], it[1]
        elif isinstance(it, dict):
            name, qty = it.get("query", it.get("name")), it.get("qty", 1)
        else:
            name, qty = str(it), 1
        parsed_items.append({"name": name, "qty": qty})
    
    # Classify items into staples vs weekly perishables
    for item in parsed_items:
        name_lower = item["name"].lower()
        if any(w in name_lower for w in ["sibul", "onion", "õli", "oil", "sool", "salt", "pipar", "pepper", "jahu", "flour", "riis", "rice", "pasta", "spagett", "kaste", "sauce", "küüslauk", "garlic", "pärm", "yeast"]):
            existing = next((s for s in data["staples"] if s["name"].lower() == item["name"].lower()), None)
            if existing:
                existing["qty"] = existing.get("qty", 1) + item["qty"]
                existing["last_purchased"] = now_str
            else:
                data["staples"].append({
                    "name": item["name"],
                    "qty": item["qty"],
                    "category": "long_term_staple",
                    "last_purchased": now_str,
                    "estimated_weeks": 4
                })
        elif any(w in name_lower for w in ["hakkliha", "kana", "broileri", "filee", "veis", "kala", "lõhe", "beef", "chicken", "meat", "pork", "tofu"]):
            data["proteins"].append({
                "name": item["name"],
                "qty": item["qty"],
                "category": "fresh_protein",
                "purchased_at": now_str,
                "shelf_life_days": 3 if "hakkliha" in name_lower else 5
            })
        else:
            data["produce"].append({
                "name": item["name"],
                "qty": item["qty"],
                "category": "produce_or_dairy",
                "purchased_at": now_str,
                "shelf_life_days": 7
            })

    data.setdefault("purchase_history", []).append({
        "timestamp": now_str,
        "store": store_slug,
        "items": parsed_items
    })
    
    save_pantry_memory(data, user_id=user_id)

def display_pantry_memory(user_id=None):
    """Outputs a human-readable and JSON summary of the virtual pantry state for specific user."""
    data = load_pantry_memory(user_id=user_id)
    print("\n" + "="*60)
    print(f"🏠 VIRTUAL PANTRY & INVENTORY MEMORY (User: {user_id or 'default'})")
    print(f"Last Updated: {data.get('last_updated', 'Unknown')}")
    print("="*60)
    
    print("\n📦 Active Long-Term Staples (Zero Repurchase Needed):")
    if data.get("staples"):
        for s in data["staples"]:
            print(f"  - {s['name']} (Qty: {s.get('qty', 1)}, Est. remaining: ~{s.get('estimated_weeks', 4)} weeks)")
    else:
        print("  (No staples recorded)")

    print("\n🥩 Tracked Proteins:")
    if data.get("proteins"):
        for p in data["proteins"]:
            print(f"  - {p['name']} (Qty: {p.get('qty', 1)}, Shelf-life: ~{p.get('shelf_life_days', 4)} days)")
    else:
        print("  (None in stock)")

    print("\n🥗 Tracked Produce & Dairy:")
    if data.get("produce"):
        for pr in data["produce"]:
            print(f"  - {pr['name']} (Qty: {pr.get('qty', 1)})")
    else:
        print("  (None in stock)")

    print(f"\n📜 Purchase History ({len(data.get('purchase_history', []))} orders recorded):")
    for rec in data.get("purchase_history", [])[-3:]:
        print(f"  - [{rec.get('timestamp')}] {rec.get('store')}: {len(rec.get('items', []))} items")
    print("="*60 + "\n")

def restock_pantry_from_detected_items(detected_items, source="photo", photo_path=None, user_id=None):
    """Adds newly purchased or inventoried groceries detected from a photo or receipt into the user's pantry memory."""
    if not detected_items:
        return "⚠️ *No items detected to restock.*", []

    pantry = load_pantry_memory(user_id=user_id)
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    
    added_summary = []
    
    for item in detected_items:
        raw_name = item.get("name", "")
        qty_num = item.get("qty", 1)
        unit = item.get("unit", "")
        cat = item.get("category", "").lower()
        name_lower = raw_name.lower()
        matched = False
        
        is_staple = any(w in name_lower or w in cat for w in ["sibul", "onion", "õli", "oil", "sool", "salt", "pipar", "pepper", "jahu", "flour", "riis", "rice", "pasta", "spagett", "sauce", "kaste", "garlic", "küüslauk", "pärm", "yeast", "bread", "toast", "sai", "leib"])
        is_protein = any(w in name_lower or w in cat for w in ["muna", "egg", "hakkliha", "kana", "broileri", "filee", "veis", "kala", "lõhe", "beef", "chicken", "meat", "pork", "tofu", "bacon", "peekon", "salmon", "ham"])
        
        if is_staple:
            target_list = pantry.setdefault("staples", [])
        elif is_protein:
            target_list = pantry.setdefault("proteins", [])
        else:
            target_list = pantry.setdefault("produce", [])
        
        # Match existing item
        for entry in target_list:
            e_name = entry.get("name", "").lower()
            if name_lower in e_name or e_name in name_lower or (is_protein and "egg" in name_lower and "egg" in e_name):
                old_qty = entry.get("qty", 0)
                entry["qty"] = round(old_qty + qty_num, 2)
                if isinstance(entry["qty"], float) and entry["qty"].is_integer():
                    entry["qty"] = int(entry["qty"])
                entry["last_restocked"] = now_str
                matched = True
                unit_str = f" {unit}" if unit else ""
                added_summary.append(f"• ➕ `{entry['name']}`: **+{qty_num}{unit_str}** (Stock: **{entry['qty']}**)")
                break
                
        if not matched:
            new_entry = {
                "name": raw_name.strip().title(),
                "qty": qty_num,
                "unit": unit,
                "category": cat or ("fresh_protein" if is_protein else ("long_term_staple" if is_staple else "produce_or_dairy")),
                "added_at": now_str
            }
            target_list.append(new_entry)
            unit_str = f" {unit}" if unit else ""
            added_summary.append(f"• 🆕 `{new_entry['name']}`: **+{qty_num}{unit_str}** (New: **{qty_num}**)")
            
    pantry.setdefault("purchase_history", []).append({
        "timestamp": now_str,
        "type": "photo_restock",
        "source": source,
        "photo_file": os.path.basename(photo_path) if photo_path else None,
        "items": detected_items
    })
    
    save_pantry_memory(pantry, user_id=user_id)
    
    summary_text = "\n".join(added_summary) if added_summary else "• (No items identified to restock)"
    text = (
        f"📦 *Pantry Restock Successful!*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"All products in the image have been counted as **new inventory** and added to your pantry memory:\n\n"
        f"{summary_text}\n\n"
        f"🏠 *Check Stock:* Tap `/pantry` to view all active inventory."
    )
    return text, added_summary

def deduct_custom_ingredients(detected_items, meal_type="breakfast", dish_title="Cooked Meal", photo_path=None, user_id=None):
    """Accurately deducts ingredients detected from an uploaded photo or manual selection
    from persistent pantry memory and updates consumption history."""
    pantry = load_pantry_memory(user_id=user_id)
    now_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    
    used_summary = []
    low_stock_alerts = []
    
    for item in detected_items:
        name = item.get("name", "")
        qty = item.get("qty", 1)
        unit = item.get("unit", "")
        name_lower = name.lower()
        
        used_summary.append(f"{qty} {unit} {name}".strip())
        
        # Match proteins
        for p in pantry.get("proteins", []):
            if name_lower in p.get("name", "").lower() or p.get("name", "").lower() in name_lower:
                curr_q = p.get("qty", 1)
                p["qty"] = max(0, curr_q - qty)
                if p["qty"] <= 1:
                    low_stock_alerts.append(f"⚠️ *{p['name']} Low:* Only {p['qty']} left in stock!")
                break
                
        # Match produce
        for pr in pantry.get("produce", []):
            if name_lower in pr.get("name", "").lower() or pr.get("name", "").lower() in name_lower:
                curr_q = pr.get("qty", 1)
                pr["qty"] = max(0, curr_q - qty)
                if pr["qty"] <= 1:
                    low_stock_alerts.append(f"⚠️ *{pr['name']} Low:* Only {pr['qty']} left!")
                break
                
    pantry.setdefault("consumption_history", []).append({
        "timestamp": now_str,
        "meal_type": meal_type,
        "dish_title": dish_title,
        "deducted_items": detected_items,
        "photo_file": os.path.basename(photo_path) if photo_path else None
    })
    
    save_pantry_memory(pantry, user_id=user_id)
    
    text = (
        f"✅ *Real Meal Logged & Deducted!*\n"
        f"🍽️ *Dish:* {dish_title} ({meal_type.capitalize()})\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️ *Exact Quantities Deducted From Stock:*\n"
    )
    for u in used_summary:
        text += f"• `{u}`\n"
        
    if low_stock_alerts:
        text += "\n🚨 *Inventory Stock Alerts:*\n"
        for alert in low_stock_alerts:
            text += f"{alert}\n"
            
    text += f"\n📦 *Virtual Pantry Updated:* View remaining stock with `/pantry`."
    return text, used_summary
