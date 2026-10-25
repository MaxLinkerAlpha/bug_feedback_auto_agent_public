# -*- coding: utf-8 -*-
"""
AI 总结失败记录重试脚本

功能：
1. 从飞书多维表格读取所有"AI 总结与提炼"为"AI 处理失败"或"处理异常"的记录
2. 重新调用 analyze_micro_feedback 进行 AI 分析
3. 通过飞书 API 更新记录的 AI 总结、分类、优先级、情感倾向字段

使用方法：
    python retry_ai_summary.py

前提条件：
    - SiliconFlow API 账户余额充足（否则会再次返回 403 错误）
    - .env 文件配置正确

运行模式：
    - 默认 dry-run 模式：只打印会更新的记录，不实际写入飞书
    - 加 --execute 参数：实际执行更新操作
    示例: python retry_ai_summary.py --execute
"""

import sys
import os
import json
import time
import requests
import argparse
from typing import List, Dict, Optional
from dotenv import load_dotenv

# 加载环境变量
load_dotenv(override=True)

# 引入核心基建
from base_utils import (
    setup_windows_console,
    analyze_micro_feedback,
    get_feishu_token,
    FEISHU_APP_TOKEN,
    FEISHU_TABLE_ID,
)

setup_windows_console()

# 飞书分表配置（如果有）
REDDIT_FEISHU_TABLE_ID = os.getenv("REDDIT_FEISHU_TABLE_ID")
DISCORD_FEISHU_TABLE_ID = os.getenv("DISCORD_FEISHU_TABLE_ID")

# AI 处理失败的标识文本
FAILED_MARKERS = ["AI 处理失败", "处理异常", "AI处理异常"]


def fetch_all_records(token: str, table_id: str) -> List[Dict]:
    """分页获取表格所有记录"""
    all_records = []
    page_token = None

    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{table_id}/records"
    headers = {"Authorization": f"Bearer {token}"}

    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token

        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)
            data = response.json()

            if data.get("code") != 0:
                print(f"[ERR] 获取记录失败: {data.get('msg')}")
                break

            items = data.get("data", {}).get("items", [])
            all_records.extend(items)
            print(f"[INFO] 已获取 {len(all_records)} 条记录...")

            has_more = data.get("data", {}).get("has_more", False)
            if not has_more:
                break

            page_token = data.get("data", {}).get("page_token")
            if not page_token:
                break
        except Exception as e:
            print(f"[ERR] 请求异常: {e}")
            break

    return all_records


def is_failed_record(fields: Dict) -> bool:
    """判断该记录的 AI 总结是否为失败状态"""
    ai_summary = fields.get("AI 总结与提炼", "")
    if not ai_summary:
        return True

    for marker in FAILED_MARKERS:
        if marker in str(ai_summary):
            return True

    return False


def update_record(token: str, table_id: str, record_id: str, new_fields: Dict) -> bool:
    """更新单条记录的 AI 相关字段"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{table_id}/records/{record_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload = {"fields": new_fields}

    try:
        response = requests.put(url, headers=headers, json=payload, timeout=15)
        data = response.json()
        return data.get("code") == 0
    except Exception as e:
        print(f"  [ERR] 更新记录 {record_id} 失败: {e}")
        return False


def process_table(table_id: str, table_name: str, execute: bool = False):
    """处理单个表格的失败记录"""
    print(f"\n{'='*60}")
    print(f"[INFO] 开始处理表格: {table_name} (ID: {table_id})")
    print(f"{'='*60}")

    token = get_feishu_token()
    if not token:
        print("[ERR] 获取飞书 Token 失败")
        return 0, 0

    # 获取所有记录
    print("[INFO] 正在获取所有记录...")
    records = fetch_all_records(token, table_id)
    print(f"[INFO] 共获取 {len(records)} 条记录")

    # 筛选失败记录
    failed_records = []
    for rec in records:
        fields = rec.get("fields", {})
        if is_failed_record(fields):
            failed_records.append(
                {
                    "record_id": rec.get("record_id"),
                    "content": fields.get("原始反馈内容", ""),
                    "author": fields.get("玩家昵称", "未知"),
                    "source": fields.get("反馈来源", "未知"),
                    "url": fields.get("原文链接", {}).get("link", "")
                    if isinstance(fields.get("原文链接"), dict)
                    else "",
                    "current_summary": fields.get("AI 总结与提炼", ""),
                }
            )

    print(f"[INFO] 找到 {len(failed_records)} 条 AI 处理失败的记录")

    if not failed_records:
        print("[INFO] 没有需要重试的记录")
        return 0, 0

    # 逐条重试
    success_count = 0
    fail_count = 0

    for i, rec in enumerate(failed_records, 1):
        print(f"\n--- [{i}/{len(failed_records)}] 处理记录 ---")
        print(f"  来源: {rec['source']}")
        print(f"  作者: {rec['author']}")
        print(f"  内容长度: {len(rec['content'])} 字")
        print(f"  当前总结: {rec['current_summary'][:50]}...")

        if not rec["content"]:
            print("  [SKIP] 内容为空，跳过")
            fail_count += 1
            continue

        # 调用 AI 重新分析
        try:
            ai_res = analyze_micro_feedback(rec["content"])

            print(f"  AI 分析结果: {ai_res}")

            if execute:
                # 构建更新字段
                update_fields = {
                    "AI 总结与提炼": ai_res.get("ai_summary", "重试后仍异常"),
                    "分类": ai_res.get("category", "反馈&建议"),
                    "优先级": ai_res.get("priority", "P2"),
                    "情感倾向": ai_res.get("sentiment", "中立"),
                }

                if update_record(token, table_id, rec["record_id"], update_fields):
                    print(f"  [OK] 飞书更新成功")
                    success_count += 1
                else:
                    print(f"  [ERR] 飞书更新失败")
                    fail_count += 1
            else:
                print(f"  [DRY-RUN] 若执行 --execute，将更新以下字段:")
                print(f"    AI 总结: {ai_res.get('ai_summary', 'N/A')}")
                print(f"    分类: {ai_res.get('category', 'N/A')}")
                print(f"    优先级: {ai_res.get('priority', 'N/A')}")
                print(f"    情感: {ai_res.get('sentiment', 'N/A')}")
                success_count += 1  # dry-run 模式下计数

            # 限速，防止 API 限流
            if execute:
                time.sleep(1)

        except Exception as e:
            print(f"  [ERR] AI 分析异常: {e}")
            fail_count += 1

    print(f"\n[INFO] 表格 {table_name} 处理完成:")
    print(f"  成功: {success_count} 条")
    print(f"  失败: {fail_count} 条")

    return success_count, fail_count


def main():
    parser = argparse.ArgumentParser(description="AI 总结失败记录重试脚本")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="实际执行更新操作（默认仅预览，不写入飞书）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("AI 总结失败记录重试工具")
    print("=" * 60)

    if not args.execute:
        print("\n[WARN] 当前为预览模式 (DRY-RUN)")
        print("       仅打印会更新的记录，不会实际写入飞书")
        print("       如需实际执行，请添加 --execute 参数")

    # 检查 API 可用性
    print("\n[INFO] 正在检查 SiliconFlow API 可用性...")
    try:
        test_res = analyze_micro_feedback("测试")
        if test_res.get("ai_summary") == "AI 处理失败":
            print(
                "[WARN] API 调用返回默认值，可能是余额不足或服务异常"
            )
            print("       请检查 SiliconFlow 账户余额后再执行重试")
            retry = input("是否继续？(y/n): ")
            if retry.lower() != "y":
                print("[INFO] 用户取消操作")
                return
        else:
            print("[OK] API 可用")
    except Exception as e:
        print(f"[WARN] API 检测异常: {e}")
        retry = input("是否继续？(y/n): ")
        if retry.lower() != "y":
            print("[INFO] 用户取消操作")
            return

    total_success = 0
    total_fail = 0

    # 处理主表
    s, f = process_table(FEISHU_TABLE_ID, "主表", execute=args.execute)
    total_success += s
    total_fail += f

    # 处理 Reddit 分表（如果配置了）
    if REDDIT_FEISHU_TABLE_ID:
        s, f = process_table(
            REDDIT_FEISHU_TABLE_ID, "Reddit 分表", execute=args.execute
        )
        total_success += s
        total_fail += f

    # 处理 Discord 分表（如果配置了）
    if DISCORD_FEISHU_TABLE_ID:
        s, f = process_table(
            DISCORD_FEISHU_TABLE_ID, "Discord 分表", execute=args.execute
        )
        total_success += s
        total_fail += f

    print("\n" + "=" * 60)
    if args.execute:
        print(f"[OK] 所有表格处理完成")
        print(f"  总计成功: {total_success} 条")
        print(f"  总计失败: {total_fail} 条")
    else:
        print(f"[INFO] 预览模式完成")
        print(f"  将更新: {total_success} 条记录")
        print(f"  运行 python retry_ai_summary.py --execute 执行实际更新")
    print("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOP] 用户强制停止。")
