#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
中央司令部：调度四大战区爬虫
优化：四平台并行执行，总时间 ≈ 最慢平台时间
渠道失败自动告警：写入飞书表格，确保用户知情
"""

import asyncio
import time
import sys
import logging
import traceback
from console_utils import setup_windows_console, FlushingStreamHandler

setup_windows_console()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('daily_log.txt', encoding='utf-8'),
        FlushingStreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

from spider_tencent import main_pipeline as tencent_pipeline
from spider_steam import main_pipeline as steam_pipeline
from spider_reddit import main_pipeline as reddit_pipeline
from spider_discord import main_pipeline as discord_pipeline
from spider_tieba import main_pipeline as tieba_pipeline
from spider_xiaoheihe import main_pipeline as xiaoheihe_pipeline
from spider_bilibili import main_pipeline as bilibili_pipeline
from base_utils import report_channel_failure

# 全局失败记录，用于汇总告警
_failure_log: list = []

async def run_mission(name: str, pipeline_func) -> dict:
    """执行单个平台的抓取任务，返回结构化结果供摘要使用"""
    global _failure_log
    platform_prefix = f"[{name[:4]}]"
    logger.info(f"{platform_prefix} 开始巡逻...")

    start_time = time.time()
    status = "ok"
    try:
        result = await pipeline_func()
        if result:  # spider 可返回状态字符串（如 "blocked_403"）
            status = str(result)
        elapsed = time.time() - start_time
        logger.info(f"{platform_prefix} 完成，耗时: {elapsed:.2f}s")
        return {"channel": name, "success": True, "status": status, "elapsed": elapsed}
    except Exception as e:
        elapsed = time.time() - start_time
        tb = traceback.format_exc()
        logger.error(f"{platform_prefix} 失败: {e}，耗时: {elapsed:.2f}s\n{tb}")
        _failure_log.append({
            "channel": name,
            "error": str(e),
            "traceback": tb,
            "elapsed": elapsed
        })
        return {"channel": name, "success": False, "status": str(e), "elapsed": elapsed}

async def main():
    global _failure_log
    _failure_log = []

    logger.info("=" * 50)
    logger.info("【全自动化游戏社区舆情监控中心】 v3.0 启动")
    logger.info("优化：七平台并行执行 + 失败飞书告警")
    logger.info("=" * 50)

    global_start = time.time()

    missions = [
        ("腾讯频道", tencent_pipeline),
        ("Steam", steam_pipeline),
        ("Reddit", reddit_pipeline),
        ("Discord", discord_pipeline),
        ("百度贴吧", tieba_pipeline),
        ("小黑盒", xiaoheihe_pipeline),
        # bilibili 登录态敏感（官方号 Cookie），放最后；风控/Cookie 过期以状态字符串进摘要
        ("bilibili", bilibili_pipeline)
    ]

    # 四平台并行执行
    tasks = [run_mission(name, func) for name, func in missions]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 规范化结果（防御：任何意外异常都视为失败）
    normalized = []
    for r in results:
        if isinstance(r, dict):
            normalized.append(r)
        else:
            normalized.append({"channel": "未知", "success": False,
                               "status": str(r), "elapsed": 0})

    success_count = sum(1 for r in normalized if r["success"])

    global_elapsed = time.time() - global_start
    logger.info("=" * 50)
    logger.info(f"[全盘任务结束] 成功 {success_count}/{len(missions)} | 总耗时: {global_elapsed:.2f}s")
    logger.info("=" * 50)

    # === 生成四平台运行摘要（UTF-8，供 bat 结束后展示） ===
    try:
        import datetime
        summary_lines = []
        summary_lines.append("=" * 44)
        summary_lines.append("Game Feedback AI Agent - 运行摘要")
        summary_lines.append(f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        summary_lines.append("-" * 44)
        for r in normalized:
            name = r["channel"]
            elapsed_s = f"{r['elapsed']:.1f}s"
            if r["success"]:
                detail = f"成功 | 耗时 {elapsed_s}"
                if r.get("status") == "blocked_403":
                    detail += " | ⚠️ Reddit 节点被封禁(403)，请切换代理"
                elif r.get("status") == "captcha_blocked":
                    detail += " | ⚠️ Reddit 被 CAPTCHA 人机验证拦截(IP 被风控)，建议切换节点"
                summary_lines.append(f"[{name}] OK   {detail}")
            else:
                err = str(r.get("status", ""))[:60]
                summary_lines.append(f"[{name}] FAIL {err}")
        summary_lines.append("-" * 44)
        summary_lines.append(f"总览: 成功 {success_count}/{len(missions)} | 总耗时 {global_elapsed:.1f}s")
        summary_lines.append("=" * 44)
        with open("run_summary.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(summary_lines))
    except Exception as e:
        logger.error(f"生成 run_summary.txt 失败: {e}")

    # === 渠道失败飞书告警 ===
    if _failure_log:
        logger.warning(f"[告警] {len(_failure_log)} 个渠道抓取失败，正在写入飞书通知...")
        for failure in _failure_log:
            logger.warning(f"  - {failure['channel']}: {failure['error'][:80]}")
            try:
                report_channel_failure(
                    channel=failure["channel"],
                    error_msg=failure["error"],
                    traceback_text=failure["traceback"]
                )
            except Exception as alert_err:
                logger.error(f"  飞书告警写入失败: {alert_err}")
        logger.info(f"[告警] 失败渠道已通知飞书，请检查 🚨系统告警 记录")

    return 0 if success_count == len(missions) else 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.warning("[STOP] 用户手动中止")
