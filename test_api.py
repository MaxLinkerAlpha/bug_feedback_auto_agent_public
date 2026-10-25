import os
import json
from dotenv import load_dotenv
load_dotenv(override=True)

from base_utils import analyze_micro_feedback, analyze_macro_summary

print("=== Test 1: 微观分析 (DeepSeek-V3.2) ===")
try:
    result = analyze_micro_feedback("游戏闪退，严重影响体验，希望官方尽快修复")
    print("SUCCESS:", json.dumps(result, ensure_ascii=False, indent=2))
except Exception as e:
    print("ERROR:", str(e)[:300])

print("\n=== Test 2: 宏观分析 (DeepSeek-V3.2) ===")
try:
    content = "游戏闪退，非常影响体验\n新版本数值不平衡，机甲太弱\n建议增加新手教程"
    result = analyze_macro_summary(content)
    print("SUCCESS:", json.dumps(result, ensure_ascii=False, indent=2))
except Exception as e:
    print("ERROR:", str(e)[:300])
