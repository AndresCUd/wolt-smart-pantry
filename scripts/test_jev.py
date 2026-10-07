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
    if api_key:
        os.environ["JEV_AI_API_KEY"] = api_key
        os.environ["TYPESAFE_API_KEY"] = api_key
    
    key = os.getenv("JEV_AI_API_KEY") or os.getenv("TYPESAFE_API_KEY")
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
        print(f"💬 Input: \"{phrase}\"")
        res = parse_natural_language_intent(phrase)
        classifier = res.get("classifier", "gemini/regex")
        intent = res.get("intent")
        params = res.get("parameters", {})
        print(f"   ⚡ Result -> Intent: [{intent}] | Classifier: [{classifier}] | Params: {params}\n")

if __name__ == "__main__":
    passed_key = sys.argv[1] if len(sys.argv) > 1 else None
    test_jev(passed_key)
