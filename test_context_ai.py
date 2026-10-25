#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from base_utils import analyze_micro_feedback, setup_windows_console

setup_windows_console()

def test_with_context():
    """测试带上下文的 AI 分析"""
    print("=" * 60)
    print("测试：带上下文的消息分析")
    print("=" * 60)

    context = """【主帖标题】Steam游戏经常崩溃
【主帖内容】游戏在加载到一半的时候会突然崩溃，错误代码是0x0005。有时候玩2-3个小时就会崩溃一次，我已经更新了显卡驱动但问题依旧。"""

    # 测试1：没有上下文的简短回复
    comment1 = "同上，我也经常崩溃"
    print(f"\n[测试1] 没有上下文的回复：")
    print(f"内容：{comment1}")
    result1 = analyze_micro_feedback(comment1)
    print(f"AI分析结果：{result1}")

    # 测试2：带上下文的简短回复
    print(f"\n[测试2] 带上下文的回复：")
    print(f"内容：{comment1}")
    print(f"上下文：{context[:50]}...")
    result2 = analyze_micro_feedback(comment1, context)
    print(f"AI分析结果：{result2}")

    # 测试3：带上下文的情绪回复
    comment3 = "顶顶顶！"
    print(f"\n[测试3] 带上下文的情绪回复：")
    print(f"内容：{comment3}")
    print(f"上下文：{context[:50]}...")
    result3 = analyze_micro_feedback(comment3, context)
    print(f"AI分析结果：{result3}")

    # 测试4：对比测试 - 正面主帖下的负面回复
    context_positive = """【主帖标题】新版本平衡性调整很棒
【主帖内容】这次更新后游戏体验好多了，各个职业的伤害都很平衡。"""

    comment4 = "确实不错"
    print(f"\n[测试4] 正面主帖下的正面回复：")
    print(f"内容：{comment4}")
    print(f"上下文：{context_positive[:50]}...")
    result4 = analyze_micro_feedback(comment4, context_positive)
    print(f"AI分析结果：{result4}")

    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)

if __name__ == "__main__":
    test_with_context()
