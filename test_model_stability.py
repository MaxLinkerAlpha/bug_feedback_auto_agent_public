#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SiliconFlow 模型稳定性测试脚本
测试多个模型的响应速度和成功率
"""

import os
import sys
import time
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from console_utils import setup_windows_console
setup_windows_console()

from dotenv import load_dotenv
load_dotenv()

from openai import OpenAI

API_KEY = os.getenv("AI_API_KEY")
BASE_URL = os.getenv("AI_BASE_URL", "https://api.siliconflow.cn/v1")

# 候选模型列表（按性价比排序）
CANDIDATE_MODELS = [
    # 免费模型
    ("Qwen/Qwen3.5-4B", "Qwen3.5-4B", "免费"),
    ("THUDM/glm-4-9b-chat", "GLM-4-9B", "免费"),
    
    # 低价模型
    ("Qwen/Qwen3.5-9B", "Qwen3.5-9B", "¥1.5/M"),
    ("deepseek-ai/DeepSeek-V3.2", "DeepSeek-V3.2", "¥0.27/M"),
    ("StepFun/Step-3.5-Flash", "Step-3.5-Flash", "¥0.1/M"),
    
    # 备选
    ("Qwen/Qwen3-8B", "Qwen3-8B", "¥2/M"),
    ("Qwen/Qwen3.5-27B", "Qwen3.5-27B", "¥1.8/M"),
]

TEST_PROMPT = """分析以下游戏反馈，返回JSON格式：
{"category": "玩法建议", "priority": "P2", "sentiment": "正面"}

反馈内容：游戏很好玩，但是有时候会卡顿，希望能优化一下。"""

def test_model(client: OpenAI, model_id: str, model_name: str) -> dict:
    """测试单个模型"""
    start_time = time.time()
    try:
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            lambda: client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": TEST_PROMPT}],
                response_format={"type": "json_object"}
            )
        )
        response = future.result(timeout=30)
        elapsed = time.time() - start_time
        
        result = json.loads(response.choices[0].message.content)
        return {
            "model": model_name,
            "status": "SUCCESS",
            "elapsed": round(elapsed, 2),
            "result": result
        }
    except FutureTimeoutError:
        return {"model": model_name, "status": "TIMEOUT", "elapsed": 30.0}
    except Exception as e:
        return {"model": model_name, "status": "ERROR", "error": str(e)[:100]}

def main():
    print("=" * 60)
    print("SiliconFlow 模型稳定性测试")
    print("=" * 60)
    
    client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=30.0)
    
    results = []
    
    # 每个模型测试3次
    for model_id, model_name, price in CANDIDATE_MODELS:
        print(f"\n[测试] {model_name} ({price})")
        
        success_count = 0
        total_time = 0
        
        for i in range(3):
            result = test_model(client, model_id, model_name)
            print(f"  第{i+1}次: {result['status']} ({result.get('elapsed', 0)}s)")
            
            if result["status"] == "SUCCESS":
                success_count += 1
                total_time += result["elapsed"]
            time.sleep(0.5)  # 避免限流
        
        avg_time = total_time / success_count if success_count > 0 else 999
        success_rate = success_count / 3 * 100
        
        results.append({
            "model": model_name,
            "price": price,
            "success_rate": success_rate,
            "avg_time": round(avg_time, 2)
        })
        
        print(f"  成功率: {success_rate:.0f}%, 平均耗时: {avg_time:.2f}s")
    
    # 输出排名
    print("\n" + "=" * 60)
    print("测试结果排名（按成功率+速度）")
    print("=" * 60)
    
    # 排序：成功率优先，然后速度
    sorted_results = sorted(results, key=lambda x: (-x["success_rate"], x["avg_time"]))
    
    for i, r in enumerate(sorted_results, 1):
        status = "[OK]" if r["success_rate"] == 100 else "[WARN]" if r["success_rate"] > 0 else "[FAIL]"
        print(f"{i}. {status} {r['model']} ({r['price']})")
        print(f"   成功率: {r['success_rate']:.0f}%, 平均耗时: {r['avg_time']}s")
    
    # 推荐
    print("\n" + "=" * 60)
    best = sorted_results[0]
    print(f"推荐模型: {best['model']} ({best['price']})")
    print("=" * 60)

if __name__ == "__main__":
    main()
