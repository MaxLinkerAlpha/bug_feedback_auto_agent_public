#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""系统测试所有可能的免费模型"""
import os, time, json
os.chdir(os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv(override=True)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("AI_API_KEY"),
    base_url=os.getenv("AI_BASE_URL"),
)

# 1. 列出所有模型
print("=" * 70)
print("  步骤 1: 列出所有可用模型")
print("=" * 70)
try:
    all_models = client.models.list(timeout=15)
    print(f"  总共 {len(all_models.data)} 个模型")
    for m in all_models.data:
        print(f"  - {m.id}")
except Exception as e:
    print(f"  ❌ 列出失败: {e}")
    exit(1)

# 2. 找出可能免费的模型（关键词匹配 + 短文本名）
free_candidates = []
for m in all_models.data:
    mid = m.id.lower()
    # 排除付费标记
    if "/pro" in mid or mid.startswith("pro/"):
        continue
    # 排除太大（通常付费）
    if any(x in mid for x in ["70b", "72b", "120b", "235b", "397b", "122b", "32b"]):
        continue
    # 候选：包含 7b 8b 9b 4b 1.5b 等小模型关键词
    if any(x in mid for x in ["7b", "8b", "9b", "4b", "1.5b", "1_5b", "0.5b"]):
        free_candidates.append(m.id)

# 也加上一些常见的免费模型名
common_free = [
    "Qwen/Qwen2.5-7B-Instruct",
    "Qwen/Qwen2-7B-Instruct",
    "Qwen/Qwen3-8B",
    "Qwen/Qwen3.5-9B",
    "Qwen/Qwen3.5-4B",
    "Qwen/Qwen2.5-3B-Instruct",
    "THUDM/glm-4-9b-0414",
    "THUDM/chatglm3-6b",
    "THUDM/chatglm-6b",
    "01-ai/Yi-1.5-9B-Chat-16K",
    "01-ai/Yi-1.5-6B-Chat",
    "meta-llama/Meta-Llama-3-8B-Instruct",
    "internlm/internlm2_5-7b-chat",
    "google/gemma-2-9b-it",
    "mistralai/Mistral-7B-Instruct-v0.3",
]
for c in common_free:
    if c not in free_candidates and any(m.id == c for m in all_models.data):
        free_candidates.append(c)

# 去重
free_candidates = list(dict.fromkeys(free_candidates))
print()
print("=" * 70)
print(f"  步骤 2: 候选免费模型 ({len(free_candidates)} 个)")
print("=" * 70)
for c in free_candidates:
    print(f"  - {c}")

# 3. 实际测试每个候选
print()
print("=" * 70)
print("  步骤 3: 实际测试（JSON 模式）")
print("=" * 70)

PROMPT = """分析反馈并返回JSON: {"ai_summary": "一句话总结", "category": "分类", "priority": "P0/P1/P2/P3", "sentiment": "正面/负面/中立"}
反馈：游戏闪退错误码500"""

results = []
for model in free_candidates:
    t0 = time.time()
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": PROMPT}],
            response_format={"type": "json_object"},
            timeout=20,
        )
        t = time.time() - t0
        content = r.choices[0].message.content
        # 验证
        try:
            parsed = json.loads(content)
            has_summary = "ai_summary" in parsed
            ok = has_summary
        except:
            ok = False
            parsed = None

        results.append({
            "model": model,
            "ok": ok,
            "time": t,
            "raw": content[:100],
            "parsed": parsed,
        })
        print(f"  {'✅' if ok else '⚠️ '} {model:45s} {t:5.1f}s | {content[:60]}")
    except Exception as e:
        t = time.time() - t0
        err = str(e)[:80]
        results.append({"model": model, "ok": False, "time": t, "error": err})
        print(f"  ❌ {model:45s} {t:5.1f}s | {err}")

# 4. 汇总
print()
print("=" * 70)
print("  步骤 4: 结果汇总")
print("=" * 70)
ok_models = [r for r in results if r.get("ok")]
print(f"  可用模型: {len(ok_models)}/{len(results)}")
print()
for r in ok_models:
    p = r.get("parsed", {})
    print(f"  ✅ {r['model']:45s} {r['time']:5.1f}s | summary={p.get('ai_summary', '?')[:30]} | cat={p.get('category')} | pri={p.get('priority')}")
