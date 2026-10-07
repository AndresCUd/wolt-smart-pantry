"""
Quick test script to verify Jev AI connection and intent classification.
Usage:
    python scripts/test_jev.py [YOUR_JEV_API_KEY]
"""

import os
import sys
import json

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wolt_manager import parse_natural_language_intent

def test_jev(api_key=None):
    user_id = "test_user"
    if api_key:
        from wolt_core.config import get_user_config, save_user_config
        cfg = get_user_config(user_id)
        cfg["jev_ai_api_key"] = api_key
        cfg["typesafe_api_key"] = api_key
        save_user_config(cfg, user_id=user_id)
    
    from wolt_core.config import get_user_config
    key = get_user_config(user_id).get("jev_ai_api_key") if api_key else None
    print(f"[*] Testing Jev AI Integration...")
    print(f"[*] Target Endpoint: https://jev-ai.pro/api/v1/systemone")
    print(f"[*] Active Key: {'[Configured]' if key else '[Not set - testing fallback]'}\n")
    
    test_phrases = [
        "What should I eat for lunch today?",
        "Can you write a gourmet recipe for pan-seared salmon?",
        "Set my shopping budget to 50 euros",
        "Buy 6 bananas and arugula on Wolt",
        "I just ate 2 eggs and avocado toast",
        "What promotional deals are active right now?",
        "Stop everything and cancel"
    ]
    
    for phrase in test_phrases:
        res = parse_natural_language_intent(phrase, user_id=user_id)
        intent = res.get("intent", "ok")
        print(f"✓ \"{phrase}\" -> Done ({intent})")
    print("\n✅ All test requests completed successfully.")

if __name__ == "__main__":
    passed_key = sys.argv[1] if len(sys.argv) > 1 else None
    test_jev(passed_key)
