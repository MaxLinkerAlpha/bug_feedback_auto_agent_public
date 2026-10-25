#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import json
import time
import asyncio
import sys
import requests
from bs4 import BeautifulSoup
from typing import Dict, Any, List
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning) # 禁用烦人的安全警告

# Plan B: Playwright 兜底导入
try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    print("[WARN] Playwright 未安装，API 失败时将无法使用浏览器兜底")

# ... 其他 import 保持不变 ...

from base_utils import (
    setup_windows_console, 
    analyze_micro_feedback, 
    write_to_feishu, 
    build_feishu_record,
    check_proxy_availability,
    SELECTED_PROXY_URL,
    rate_limit,
    trim_history_map,
    HISTORY_MAX_SIZE
)
setup_windows_console()

# ================= 配置读取 =================
STEAM_APP_ID = os.getenv("STEAM_APP_ID", "")
STEAM_MAX_ANNOUNCEMENTS = int(os.getenv("STEAM_MAX_ANNOUNCEMENTS", 2))
STEAM_MAX_DISCUSSIONS = int(os.getenv("STEAM_MAX_DISCUSSIONS", 20))
STEAM_FETCH_MODE = os.getenv("STEAM_FETCH_MODE", "all").lower()
STEAM_IGNORE_HISTORY = os.getenv("STEAM_IGNORE_HISTORY", "False").lower() == "true"
FEISHU_TABLE_ID = os.getenv("FEISHU_TABLE_ID")

# 使用智能选择的代理（从 base_utils 导入）
PROXY_URL = SELECTED_PROXY_URL
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
HISTORY_FILE = "steam_history.json"

# 打造防弹级 Session (仅保留重试逻辑，去掉死板的全局代理绑定)
session = requests.Session()

retry_strategy = Retry(
    total=3,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["HEAD", "GET", "OPTIONS"],
    backoff_factor=1
)
adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=100, pool_maxsize=100)
session.mount("https://", adapter)
session.mount("http://", adapter)
# ==========================================

# 【核心拓展】：将 limit 阈值与板块深度绑定
TARGET_SUBFORUMS = {
    "eventcomments/": {
        "name": "官方公告评论 (Event Comments)", 
        "limit": STEAM_MAX_ANNOUNCEMENTS
    },
    "discussions/1/": {
        "name": "平衡与建议 (Balancing & Suggesting)", 
        "limit": STEAM_MAX_DISCUSSIONS
    },
    "discussions/2/": {
        "name": "Bug与故障排查 (Bugs & Troubleshooting)", 
        "limit": STEAM_MAX_DISCUSSIONS
    }
}
# ==========================================

def select_subforums() -> dict:
    if STEAM_FETCH_MODE == "announcements":
        return {"eventcomments/": TARGET_SUBFORUMS["eventcomments/"]}
    if STEAM_FETCH_MODE == "discussions":
        return {k: v for k, v in TARGET_SUBFORUMS.items() if k != "eventcomments/"}
    return TARGET_SUBFORUMS

def load_history() -> tuple:
    if not os.path.exists(HISTORY_FILE): 
        return {}, {}
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f: 
            data = json.load(f)
        if isinstance(data, dict) and "ids" in data:
            ids = data.get("ids", {})
            latest_ts = data.get("latest_ts", {})
        elif isinstance(data, dict):
            ids = data
            latest_ts = {}
        elif isinstance(data, list):
            ids = {str(i): 0 for i in data}
            latest_ts = {}
        else:
            ids = {}
            latest_ts = {}
        ids = {str(k): int(v) if isinstance(v, (int, float)) else 0 for k, v in ids.items()}
        latest_ts = {str(k): int(v) if isinstance(v, (int, float)) else 0 for k, v in latest_ts.items()}
        return ids, latest_ts
    except Exception as e:
        print(f"\n[WARN] 历史记录读取异常: {e}")
        return {}, {}

def save_history(history_ids: dict, latest_ts: dict) -> None:
    try:
        trimmed_ids = trim_history_map(history_ids, HISTORY_MAX_SIZE)
        payload = {"ids": trimmed_ids, "latest_ts": latest_ts}
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f: 
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        print(f"\n[WARN] 历史记录保存失败: {e}")

async def progress_spinner(current_idx: int, total: int, msg: str, stop_event: asyncio.Event):
    """
    进度提示：仅在任务开始时打一行标记，结束时不擦除。
    之前的实现使用 \\r\\033[K 行内更新会**覆盖**掉 AI 完成后打印的"写入成功"日志，
    导致用户在终端看不到任何反馈（虽然日志文件里是有的）。
    现在简化为"开始提示 -> 等待 -> 自动结束"，保证所有 print 行都能正常显示。
    """
    sys.stdout.write(f"  [..] [进度 {current_idx}/{total}] 开始 {msg}\n")
    sys.stdout.flush()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    # 不再做任何输出清理，避免覆盖其他 print

def get_latest_forum_topics(appid: str, subforum: str, limit: int, time_threshold_ms: int = None, max_pages: int = 30) -> List[Dict[str, Any]]:
    """
    获取板块帖子列表，同时提取每个帖子的**真实发帖时间**（非最后活动时间）。

    返回格式：[{"url": "...", "created_time_ms": ..., "last_activity_ms": ...}, ...]

    关键设计：
    - Steam 论坛列表按"最后活动"（lastpost）排序，**不是**按发帖时间
    - 旧帖有新回复时会浮到顶部，导致 first page 混杂大量旧帖（陷阱！）
    - 必须从父容器的 `data-tooltip-forum` 属性中解析"Posted by: ..., <时间>"

    分页模式（time_threshold_ms 不为 None 时自动启用，2026-08-18 新增）：
    - 使用 ?fp=N 翻页，枚举整个时间窗口内的帖子（用于 60 天补抓）
    - **停页条件**：当某一页所有帖子的最后活动时间都早于阈值时停止枚举。
      依据：发帖时间 ≤ 最后活动时间，因此更深的页面不可能再有窗口内帖子。
    - 正常模式（time_threshold_ms=None）保持原有"单页 + limit 截断"行为，向后兼容。

    提取不到发帖时间时 created_time_ms = 0，由 main_pipeline 决定如何处理。
    """
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9"}
    paginate = time_threshold_ms is not None
    max_page = max_pages if paginate else 1
    all_topics = []

    for page_num in range(1, max_page + 1):
        if paginate and page_num > 1:
            url = f"https://steamcommunity.com/app/{appid}/{subforum}?fp={page_num}"
        else:
            url = f"https://steamcommunity.com/app/{appid}/{subforum}"
        print(f"\n[🔎] 正在扫描板块: {url}")

        try:
            res = session.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(res.text, 'html.parser')

            # 分页模式下不按 limit 截断（要枚举窗口内全部）；正常模式保留 limit 语义
            overlay_items = soup.select('a.forum_topic_overlay')
            if not paginate:
                overlay_items = overlay_items[:limit]

            page_topics = []
            for item in overlay_items:
                topic_url = item.get('href')
                if not topic_url:
                    continue

                created_time_ms = 0
                last_activity_ms = 0

                # ========== 方法1: 从父容器的 hover 提示中提取"Posted by"时间 ==========
                # Steam 列表中: <div class="forum_topic" data-tooltip-forum="...Posted by: <author>, <15 May, 2023 @ 12:39pm>...">
                parent = item.find_parent(['div', 'li', 'tr'], class_='forum_topic')
                if parent is None:
                    parent = item.find_parent(['div', 'li', 'tr'])

                if parent:
                    tooltip_attr = parent.get('data-tooltip-forum', '')
                    if tooltip_attr:
                        # 查找发帖时间，支持中英文两种格式
                        # 注意：HTML 实体 &quot; 需要处理
                        tooltip_text = tooltip_attr.replace('&quot;', '"').replace('&amp;', '&')

                        # 中文格式（2026-07-29 起 Steam 页面变为中文）：
                        #   发帖人：<span class="topic_hover_data">Roenie</span>，发帖于 <span class="">上午 4:34</span>
                        #   发帖人：<span class="topic_hover_data">i do games</span>，发帖于 <span class="">7 月 22 日 下午 8:12</span>
                        posted_match_cn = re.search(
                            r'发帖于[^<]*<span[^>]*>([^<]+)',
                            tooltip_text
                        )
                        # 英文格式（旧版 Steam 页面）：
                        #   Posted by: <author>, <15 May, 2023 @ 12:39pm>
                        posted_match_en = re.search(
                            r'Posted\s+by:.*?,\s*([^<>"&]+(?:&[^;]+;[^<>"&]*)*?)(?:\s*</span>|\s*&|$)',
                            tooltip_text
                        )

                        posted_match = posted_match_cn or posted_match_en
                        if posted_match:
                            posted_time_str = posted_match.group(1).strip()
                            created_time_ms = _parse_steam_time(posted_time_str)

                    # ========== 方法2: 从 lastpost 提取最后活动时间 ==========
                    # ⚠️ 注意：这是"最后活动"时间，不是发帖时间
                    # 2026-08-18 修复：不再被 created_time==0 包裹，
                    # 无条件提取，供分页枚举时作为"停页水位线"（发帖时间 ≤ 最后活动时间）
                    lastpost_el = parent.select_one('.forum_topic_lastpost')
                    if lastpost_el:
                        if lastpost_el.has_attr('data-timestamp'):
                            try:
                                last_activity_ms = int(lastpost_el['data-timestamp']) * 1000
                            except (ValueError, TypeError):
                                pass
                        if last_activity_ms == 0:
                            time_text = lastpost_el.get_text(strip=True)
                            last_activity_ms = _parse_steam_time(time_text)

                page_topics.append({
                    "url": topic_url,
                    "created_time_ms": created_time_ms,       # 0 表示未解析到发帖时间
                    "last_activity_ms": last_activity_ms,     # 最后活动（仅参考/停页水位线）
                })

            if not page_topics:
                print(f"[OK] 第 {page_num} 页无更多帖子，枚举结束")
                break

            all_topics.extend(page_topics)

            # ========== 分页停页判断 ==========
            # 仅当本页所有帖子的最后活动时间都解析成功且早于阈值时才停，
            # 避免个别解析失败导致过早停止（漏掉窗口内帖子）。
            if paginate:
                valid_last = [t["last_activity_ms"] for t in page_topics if t["last_activity_ms"] > 0]
                if len(valid_last) == len(page_topics) and max(valid_last) < time_threshold_ms:
                    print(f"[🛑] 第 {page_num} 页所有帖子最后活动均早于阈值，停止枚举")
                    break

        except Exception as e:
            print(f"[ERR] 第 {page_num} 页获取失败: {e}")
            break

    if not all_topics:
        print("[WARN] 列表页未解析到任何帖子")
        return []

    parsed_count = sum(1 for t in all_topics if t["created_time_ms"] > 0)
    unknown_count = len(all_topics) - parsed_count
    print(f"[OK] 共枚举 {len(all_topics)} 篇帖子（{parsed_count} 篇解析到发帖时间，{unknown_count} 篇待详情页验证）")
    return all_topics

def _parse_steam_time(time_str: str) -> int:
    """
    解析 Steam 的时间文本，返回毫秒时间戳。
    支持多种格式（Steam 中英文界面均兼容）：

    相对时间：
    - "2 hours ago" / "6 hours ago" / "15 minutes ago"
    - "3 days ago" / "2 weeks ago" / "1 months ago"
    - "刚刚" / "6 小时以前" / "5 天以前"

    绝对时间：
    - "15 May, 2023 @ 12:39pm" (英文绝对)
    - "29 May @ 2:48pm"       (英文短绝对)
    - "6 月 3 日 上午 3:32"   (中文绝对)
    - "2023年5月15日 12:39"   (中文长绝对)

    解析失败返回 0（让调用方决定如何处理，宁可漏抓不可错抓）
    """
    if not time_str:
        return 0

    from datetime import datetime, timedelta
    now = datetime.now()
    text = time_str.strip()
    text_lower = text.lower()

    # ==================== 相对时间解析 ====================
    relative_patterns = [
        # 英文
        (r'(\d+)\s*seconds?\s*ago', 'seconds'),
        (r'(\d+)\s*minutes?\s*ago', 'minutes'),
        (r'(\d+)\s*hours?\s*ago', 'hours'),
        (r'(\d+)\s*days?\s*ago', 'days'),
        (r'(\d+)\s*weeks?\s*ago', 'weeks'),
        (r'(\d+)\s*months?\s*ago', 'months'),
        (r'(\d+)\s*years?\s*ago', 'years'),
        # 中文
        (r'(\d+)\s*秒前', 'seconds'),
        (r'(\d+)\s*分钟前', 'minutes'),
        (r'(\d+)\s*小时前', 'hours'),
        (r'(\d+)\s*天前', 'days'),
        (r'(\d+)\s*周前', 'weeks'),
        (r'(\d+)\s*个月前', 'months'),
        (r'(\d+)\s*年前', 'years'),
        (r'(\d+)\s*小时以前', 'hours'),
        (r'(\d+)\s*天以前', 'days'),
    ]

    for pattern, unit in relative_patterns:
        match = re.search(pattern, text_lower)
        if match:
            value = int(match.group(1))
            delta_map = {
                'seconds': timedelta(seconds=value),
                'minutes': timedelta(minutes=value),
                'hours':   timedelta(hours=value),
                'days':    timedelta(days=value),
                'weeks':   timedelta(weeks=value),
                'months':  timedelta(days=value * 30),  # 近似值
                'years':   timedelta(days=value * 365),
            }
            result_dt = now - delta_map[unit]
            return int(result_dt.timestamp() * 1000)

    # ==================== 绝对时间解析 ====================
    # 英文绝对: "15 May, 2023 @ 12:39pm" / "29 May @ 2:48pm" / "4 June, 2026 @ 2:22:12 pm PDT"
    month_map_en = {
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
        'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    }

    # 模式1: "15 May, 2023 @ 12:39pm" 或 "4 June, 2026 @ 2:22:12 pm"
    m = re.search(
        r'(\d{1,2})\s+([A-Za-z]+),?\s*(\d{4})?\s*@\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?',
        text, re.IGNORECASE
    )
    if m:
        try:
            day = int(m.group(1))
            month = month_map_en.get(m.group(2).lower()[:3])
            year = int(m.group(3)) if m.group(3) else now.year
            hour = int(m.group(4))
            minute = int(m.group(5))
            second = int(m.group(6)) if m.group(6) else 0
            ampm = (m.group(7) or '').lower()
            if ampm == 'pm' and hour < 12:
                hour += 12
            elif ampm == 'am' and hour == 12:
                hour = 0
            dt = datetime(year, month, day, hour, minute, second)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError, KeyError):
            pass

    # 模式2: "29 May @ 2:48pm"（无年份，默认当前年）
    m = re.search(
        r'(\d{1,2})\s+([A-Za-z]+)\s*@\s*(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?',
        text, re.IGNORECASE
    )
    if m:
        try:
            day = int(m.group(1))
            month = month_map_en.get(m.group(2).lower()[:3])
            year = now.year
            hour = int(m.group(3))
            minute = int(m.group(4))
            second = int(m.group(5)) if m.group(5) else 0
            ampm = (m.group(6) or '').lower()
            if ampm == 'pm' and hour < 12:
                hour += 12
            elif ampm == 'am' and hour == 12:
                hour = 0
            dt = datetime(year, month, day, hour, minute, second)
            # 如果算出的日期在未来（说明是去年的），回退一年
            if dt > now:
                dt = dt.replace(year=year - 1)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError, KeyError):
            pass

    # 模式3: 中文绝对 "6 月 3 日 上午 3:32" / "5 月 22 日 上午 7:22" / "6 月 1 日 下午 1:50"
    m = re.search(
        r'(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*(上午|下午|凌晨|晚上|早上)?\s*(\d{1,2})[::](\d{2})',
        text
    )
    if m:
        try:
            month = int(m.group(1))
            day = int(m.group(2))
            period = m.group(3) or ''
            hour = int(m.group(4))
            minute = int(m.group(5))
            if '下午' in period and hour < 12:
                hour += 12
            elif '凌晨' in period and hour == 12:
                hour = 0
            dt = datetime(now.year, month, day, hour, minute, 0)
            # 如果算出的日期在未来（说明是去年的），回退一年
            if dt > now:
                dt = dt.replace(year=now.year - 1)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass

    # 模式4: 中文无日期 "上午 4:34" / "下午 8:12" / "凌晨 3:00"（今天的帖子，默认今天）
    # Steam 列表页中今天的帖子只显示时间不显示日期
    m = re.search(
        r'^(上午|下午|凌晨|晚上|早上)?\s*(\d{1,2})[::](\d{2})$',
        text.strip()
    )
    if m:
        try:
            period = m.group(1) or ''
            hour = int(m.group(2))
            minute = int(m.group(3))
            if '下午' in period and hour < 12:
                hour += 12
            elif '凌晨' in period and hour == 12:
                hour = 0
            elif '晚上' in period and hour < 12:
                hour += 12
            dt = datetime(now.year, now.month, now.day, hour, minute, 0)
            # 如果算出的时间在未来（如当前 15:33，解析出 "下午 8:12"），
            # 说明可能是昨天的帖子（Steam 偶尔在跨天后仍显示时间格式）
            if dt > now:
                dt = dt - timedelta(days=1)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass

    # 解析失败，返回 0
    return 0


def _extract_op_post_time_ms(soup) -> int:
    """
    从帖子详情页的 BeautifulSoup 对象中提取 OP（楼主）发帖时间戳（毫秒）。

    优先级：
    1. .forum_op 容器中的 .commentthread_comment_timestamp[data-timestamp]（最准确，OP 楼主专用容器）
    2. .forum_op 容器中的 .commentthread_comment_timestamp（无 data-timestamp 时回退）
    3. 页面中第一个 .commentthread_comment_timestamp[data-timestamp]（兜底，OP 通常是第一个）
    4. 页面中第一个 .commentthread_comment_timestamp（最后兜底）

    返回值：
    - int > 0: 成功提取的时间戳（毫秒）
    - 0: 找到时间元素但无有效 data-timestamp
    - None: 完全找不到任何时间元素（页面结构异常）
    """
    # 方法1: .forum_op 容器中的时间戳（OP 楼主专用）
    op_container = soup.select_one('.forum_op')
    if op_container:
        # 2026-07-29 修复：评论容器内有 2 个 .commentthread_comment_timestamp 元素，
        # 第一个为空（无 data-timestamp），必须优先选择带 data-timestamp 的那个
        op_ts_el = op_container.select_one('.commentthread_comment_timestamp[data-timestamp]')
        if op_ts_el is None:
            op_ts_el = op_container.select_one('.commentthread_comment_timestamp')
        if op_ts_el:
            if op_ts_el.has_attr('data-timestamp'):
                try:
                    return int(op_ts_el['data-timestamp']) * 1000
                except (ValueError, TypeError):
                    pass
            # 没有 data-timestamp，尝试解析 title 属性的绝对时间
            title_attr = op_ts_el.get('title', '')
            if title_attr:
                ts = _parse_steam_time(title_attr)
                if ts > 0:
                    return ts
            # 都没有，回退到文本内容
            text_time = op_ts_el.get_text(strip=True)
            ts = _parse_steam_time(text_time)
            if ts > 0:
                return ts

    # 方法2: 页面中第一个时间戳（兜底）
    all_ts_els = soup.select('.commentthread_comment_timestamp')
    if all_ts_els:
        first_ts_el = all_ts_els[0]
        if first_ts_el.has_attr('data-timestamp'):
            try:
                return int(first_ts_el['data-timestamp']) * 1000
            except (ValueError, TypeError):
                pass
        title_attr = first_ts_el.get('title', '')
        if title_attr:
            ts = _parse_steam_time(title_attr)
            if ts > 0:
                return ts
        text_time = first_ts_el.get_text(strip=True)
        ts = _parse_steam_time(text_time)
        if ts > 0:
            return ts
        # 找到了元素但解析不出有效时间 → 返回 0
        return 0

    # 完全找不到时间元素
    return None

async def fetch_comments_by_playwright(url: str, subforum_name: str, time_threshold_ms: int = None) -> List[Dict[str, Any]]:
    """
    Plan B: 使用 Playwright 渲染页面抓取评论
    适用于 API 嗅探失败的场景
    """
    parsed = []
    browser = None
    context = None
    page = None
    
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
                viewport={'width': 1920, 'height': 1080}
            )
            
            page = await context.new_page()
            
            if PROXY_URL:
                print(f"    [🌐] Playwright 使用代理: {PROXY_URL}")
            
            print(f"    [🎭] 正在加载页面: {url}")
            await page.goto(url, wait_until='networkidle', timeout=30000)
            
            # ========== 主楼时间验证（与 HTML 路径一致的二次验证）==========
            # OP 楼主在 .forum_op 容器中（不是 .commentthread_comment）
            # 提取不到 OP 时间戳时，**保守跳过此帖**（宁可漏抓不可错抓）
            if time_threshold_ms is not None:
                try:
                    op_ts = await page.evaluate('''() => {
                        // 优先查找 .forum_op 容器中的 OP 楼主时间戳
                        const opContainer = document.querySelector('.forum_op');
                        const opSelector = opContainer
                            ? '.forum_op .commentthread_comment_timestamp'
                            : '.commentthread_comment_timestamp';
                        const tsEl = document.querySelector(opSelector);
                        if (!tsEl) return -1;  // 没找到元素
                        const ts = tsEl.getAttribute('data-timestamp');
                        if (ts) return parseInt(ts) * 1000;
                        // 没有 data-timestamp，返回 0
                        return 0;
                    }''')
                    if op_ts < 0:
                        # 完全找不到 OP 时间元素 → 保守跳过
                        print(f"    [⏭️] Playwright 路径：详情页无 OP 时间元素，跳过此帖")
                        await browser.close()
                        return []
                    if op_ts == 0:
                        # 元素存在但无 data-timestamp → 保守跳过
                        print(f"    [⏭️] Playwright 路径：OP 时间戳无效，跳过此帖")
                        await browser.close()
                        return []
                    if op_ts < time_threshold_ms:
                        days_old = (time.time() * 1000 - op_ts) / (24 * 3600 * 1000)
                        print(f"    [⏭️] Playwright 检测：主楼发布于 {days_old:.0f} 天前，超过限制，跳过此帖")
                        await browser.close()
                        return []
                    else:
                        days_old = (time.time() * 1000 - op_ts) / (24 * 3600 * 1000)
                        print(f"    [✅] Playwright 检测：主楼发布于 {days_old:.1f} 天前，通过过滤")
                except Exception:
                    pass  # 如果 JS 执行失败，忽略并继续抓取
            # =============================================================
            
            # 等待评论区加载（最长等待10秒）
            try:
                await page.wait_for_selector('.commentthread_comment', timeout=10000)
                print(f"    [✅] 评论区已加载")
            except:
                print(f"    [⚠️] 等待评论超时，尝试继续...")
            
            # 尝试滚动加载更多评论（Steam 是懒加载的）
            last_comment_count = 0
            scroll_attempts = 0
            max_scroll_attempts = 10  # 最多滚动10次
            
            while scroll_attempts < max_scroll_attempts:
                # 获取当前评论数量
                current_count = await page.evaluate('() => document.querySelectorAll(".commentthread_comment").length')
                
                if current_count > last_comment_count:
                    print(f"    [📄] 已加载 {current_count} 条评论，继续滚动...")
                    last_comment_count = current_count
                    scroll_attempts = 0  # 重置计数器
                else:
                    scroll_attempts += 1
                
                # 滚动到页面底部
                await page.evaluate('() => window.scrollTo(0, document.body.scrollHeight)')
                await asyncio.sleep(1)  # 等待懒加载
                
                # 检查是否有"加载更多"按钮并点击
                try:
                    load_more = await page.query_selector('.commentthread_loadmore a, .forum_paging_pagelink')
                    if load_more:
                        await load_more.click()
                        await asyncio.sleep(1.5)
                        scroll_attempts = 0
                except:
                    pass
            
            print(f"    [📦] 页面滚动完成，共发现 {last_comment_count} 条评论")
            
            # 提取所有评论数据
            comments_data = await page.evaluate('''() => {
                const comments = [];
                document.querySelectorAll('.commentthread_comment').forEach(el => {
                    const id = el.getAttribute('id') || '';
                    const authorEl = el.querySelector('.commentthread_author_link');
                    const author = authorEl ? authorEl.innerText.trim() : '匿名';
                    
                    const contentEl = el.querySelector('.commentthread_comment_text');
                    const content = contentEl ? contentEl.innerText.trim() : '';
                    
                    const timeEl = el.querySelector('.commentthread_comment_timestamp');
                    let timestamp = 0;
                    if (timeEl) {
                        const ts = timeEl.getAttribute('data-timestamp');
                        if (ts) timestamp = parseInt(ts) * 1000;
                    }
                    
                    if (content && id) {
                        comments.push({
                            comment_id: id,
                            author: author,
                            content: content,
                            post_time_ms: timestamp || Date.now()
                        });
                    }
                });
                return comments;
            }''')
            
            print(f"    [✅] Playwright 成功提取 {len(comments_data)} 条评论")
            
            # 添加 URL 和平台信息
            for c in comments_data:
                c['url'] = f"{url}#{c['comment_id']}"
                c['platform'] = f"Steam ({subforum_name})"
                parsed.append(c)
            
            await browser.close()
            
    except Exception as e:
        print(f"    [❌] Playwright 抓取失败: {e}")
        if browser:
            try:
                await browser.close()
            except:
                pass
    
    return parsed

async def fetch_steam_comments(url: str, subforum_name: str, time_threshold_ms: int = None) -> List[Dict[str, Any]]:
    """
    新版：使用 ?ctp=N 分页参数直接翻页抓取 HTML 中的评论
    默认设置每页50条，减少请求次数
    适用于 Steam 公告/活动评论页面
    自动提取主帖内容作为上下文，帮助 AI 理解回复的语境
    
    注意：时间过滤已移至 main_pipeline() 的帖子级别
    此函数的 time_threshold_ms 参数仅用于特殊场景（如手动指定）
    默认不过滤评论时间，返回所有评论
    """
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN"}
    all_comments = []
    topic_context = None  # 存储主帖上下文
    op_record_extracted = False  # 标记 OP 楼主正文是否已提取（只提取一次）
    
    # 注意：不再设置默认30天阈值
    # 帖子级别的时间过滤已在 main_pipeline() 中完成
    
    try:
        page_num = 1
        max_pages = 50  # 安全上限，防止死循环
        replies_per_page = 50  # 每页50条，减少请求次数
        
        while page_num <= max_pages:
            # 构造分页 URL，第一页附带每页条数参数
            if page_num == 1:
                # 第一页尝试设置每页50条
                separator = '&' if '?' in url else '?'
                page_url = f"{url}{separator}replies_per_page={replies_per_page}"
            else:
                separator = '&' if '?' in url else '?'
                page_url = f"{url}{separator}ctp={page_num}&replies_per_page={replies_per_page}"
            
            print(f"    [📄] 正在抓取第 {page_num} 页: {page_url}")
            await rate_limit(0.1)  # 请求间隔
            
            res = session.get(page_url, headers=headers, timeout=15)
            if res.status_code != 200:
                print(f"    [⚠️] 第 {page_num} 页返回状态码 {res.status_code}，停止翻页")
                break
            
            soup = BeautifulSoup(res.text, 'html.parser')
            
            # 在第一页提取主帖上下文（标题+内容）
            if page_num == 1 and topic_context is None:
                topic_title = ""
                topic_content = ""
                
                # 尝试多种选择器提取主帖标题
                title_el = soup.select_one('.forum_topic_title, div.topic_title, h1.topic_title, .apphub_topic_title')
                if title_el:
                    topic_title = title_el.text.strip()
                
                # 尝试多种选择器提取主帖内容
                content_el = soup.select_one('.forum_description, div.topic_content, .topic_content, .apphub_body')
                if content_el:
                    topic_content = content_el.text.strip()
                
                # 如果找到了主帖信息，组装上下文
                if topic_title or topic_content:
                    topic_context = f"【主帖标题】{topic_title}\n【主帖内容】{topic_content}" if topic_content else f"【主帖标题】{topic_title}"
                    print(f"    [📌] 已提取主帖上下文: {topic_title[:50]}...")
            
            # ==================== 主楼发布时间二次验证（核心防线）====================
            # 列表页的 hover 提示中可能没有"Posted by"时间，或者解析失败
            # 这里直接从帖子详情页第一页提取 OP（楼主）首条评论的时间戳
            #
            # 关键：OP 楼主在 <div class="forum_op"> 容器中，**不**是 .commentthread_comment
            # 如果 OP 发布时间超过阈值，直接返回空，完全跳过这个旧帖
            #
            # 保守策略：提取不到 OP 时间戳时，**跳过此帖**（宁可漏抓不可错抓）
            if page_num == 1 and time_threshold_ms is not None and all_comments == []:
                op_post_time_ms = _extract_op_post_time_ms(soup)

                if op_post_time_ms is None:
                    # 详情页完全找不到 OP 时间戳 → 保守跳过
                    print(f"    [⏭️] 详情页无法提取 OP 时间戳，宁可漏抓不可错抓，跳过此帖")
                    return []
                elif op_post_time_ms <= 0:
                    # 解析到的时间戳为 0 → 保守跳过
                    print(f"    [⏭️] OP 时间戳解析为 0（无效），跳过此帖")
                    return []
                elif op_post_time_ms < time_threshold_ms:
                    days_old = (time.time() * 1000 - op_post_time_ms) / (24 * 3600 * 1000)
                    print(f"    [⏭️] 主楼发布于 {days_old:.0f} 天前，超过 30 天限制，跳过此帖")
                    return []  # 返回空列表，完全跳过这个旧帖
                else:
                    days_old = (time.time() * 1000 - op_post_time_ms) / (24 * 3600 * 1000)
                    print(f"    [✅] OP 主楼发布于 {days_old:.1f} 天前，在 30 天范围内，继续抓取")
            # ========================================================================

            # ==================== OP 楼主正文提取（核心修复 2026-08-18）====================
            # 原逻辑只提取回复评论（.commentthread_comment / .forum_comment），
            # 楼主正文位于 .forum_op > .topic + .content，从未入库，
            # 导致纯 OP 帖（0 回复）整篇丢失、带回复帖的楼主反馈也全部遗漏。
            # 此处把楼主正文作为一条独立反馈记录提取（comment_id = 主题 id，天然去重）。
            if page_num == 1 and not op_record_extracted:
                op_el = soup.select_one('.forum_op')
                if op_el:
                    op_id_el = op_el.get('id', '')
                    op_cid = ""
                    m = re.search(r'forum_op_(\d+)', op_id_el)
                    if m:
                        op_cid = m.group(1)

                    op_author_el = op_el.select_one('.forum_op_author')
                    op_title_el = op_el.select_one('.topic')
                    op_content_el = op_el.select_one('.content')
                    op_ts_el = op_el.select_one('.commentthread_comment_timestamp[data-timestamp]')

                    op_ts = 0
                    if op_ts_el and op_ts_el.has_attr('data-timestamp'):
                        try:
                            op_ts = int(op_ts_el['data-timestamp']) * 1000
                        except (ValueError, TypeError):
                            pass

                    # 楼主正文 = 标题 + 正文，便于 AI 理解完整反馈
                    op_content = ""
                    if op_title_el:
                        op_content = op_title_el.get_text(strip=True)
                    if op_content_el:
                        op_body = op_content_el.get_text(strip=True)
                        if op_body:
                            op_content = (op_content + "\n" + op_body).strip()

                    if op_cid and op_content:
                        # 2026-08-18 修复：OP 时间兜底不再用当前时间（会污染反馈时间字段），
                        # 改为复用 _extract_op_post_time_ms 的丰富回退（0 = 交给决策层/保持诚实）
                        op_time = op_ts if op_ts else (_extract_op_post_time_ms(soup) or 0)
                        all_comments.append({
                            "comment_id": op_cid,
                            "author": op_author_el.get_text(strip=True) if op_author_el else "匿名",
                            "content": op_content,
                            "post_time_ms": op_time,
                            "url": url,
                            "platform": f"Steam ({subforum_name})",
                            "context": None
                        })
                        print(f"    [📌] 已提取楼主正文 (OP): id={op_cid} | {op_content[:40]}...")
                op_record_extracted = True
            # ========================================================================
            
            # 提取当前页的评论
            page_comments = []
            for el in soup.select('.commentthread_comment, .forum_comment'):
                cid = el.get('id', '')
                author_el = el.select_one('.commentthread_author_link, .forum_comment_author a')
                author = author_el.text.strip() if author_el else "匿名"
                
                content_el = el.select_one('.commentthread_comment_text, .forum_comment_content')
                content = content_el.text.strip() if content_el else ""
                
                # 2026-07-29 修复：评论容器内有 2 个 .commentthread_comment_timestamp 元素，
                # 第一个为空（无 data-timestamp），必须优先选择带 data-timestamp 的那个
                # 否则所有评论时间都会兜底为当前时间，导致"反馈时间"字段错误
                ts_el = el.select_one('.commentthread_comment_timestamp[data-timestamp]')
                if ts_el is None:
                    ts_el = el.select_one('.commentthread_comment_timestamp, .forum_comment_timestamp')
                ts = int(time.time() * 1000)
                if ts_el and ts_el.has_attr('data-timestamp'):
                    try:
                        ts = int(ts_el['data-timestamp']) * 1000
                    except (ValueError, TypeError):
                        pass
                
                if content and cid:
                    # 仅在明确指定时间阈值时才过滤评论（默认不过滤）
                    # 因为帖子级别已确保只抓取30天内的新帖
                    if time_threshold_ms is not None and ts < time_threshold_ms:
                        continue  # 跳过超过时间阈值的旧评论
                    
                    page_comments.append({
                        "comment_id": cid, 
                        "author": author, 
                        "content": content, 
                        "post_time_ms": ts, 
                        "url": f"{url}#{cid}", 
                        "platform": f"Steam ({subforum_name})",
                        "context": topic_context  # 添加主帖上下文
                    })
            
            if not page_comments:
                print(f"    [✅] 第 {page_num} 页无评论，抓取完成")
                break
            
            all_comments.extend(page_comments)
            filter_note = "（已过滤旧评论）" if time_threshold_ms is not None else ""
            print(f"    [✓] 第 {page_num} 页抓取到 {len(page_comments)} 条评论{filter_note}")
            
            # 性能优化：仅在使用时间过滤时，检测到全页被过滤可提前终止
            if time_threshold_ms is not None and len(page_comments) == 0 and page_num > 1:
                print(f"    [⏭️] 检测到后续均为历史评论，提前终止翻页")
                break
            
            # 检查是否有下一页
            # 方法1: 查找 "下一页" 链接
            next_link = soup.select_one('a.commentthread_pagelink[onclick*="Forum_PageChange"]:not(.active)')
            # 方法2: 查找页码链接，看是否有比当前页大的页码
            page_links = soup.select('a.commentthread_pagelink')
            has_next_page = False
            for link in page_links:
                try:
                    link_page = int(link.text.strip())
                    if link_page > page_num:
                        has_next_page = True
                        break
                except ValueError:
                    continue
            
            # 方法3: 检查是否有 "下一页" 文本的链接
            if not has_next_page:
                next_text_links = soup.find_all('a', string=lambda x: x and ('next' in x.lower() or '下一页' in x or '>' in x))
                has_next_page = len(next_text_links) > 0
            
            if not has_next_page and next_link is None:
                # 再检查一次，通过看当前页面评论数量是否达到满页（通常是10条或50条）
                # 如果满页，可能还有下一页
                if len(page_comments) >= 10:  # 保守估计
                    # 尝试访问下一页看是否有内容
                    test_url = f"{url}{'&' if '?' in url else '?'}ctp={page_num + 1}"
                    test_res = session.get(test_url, headers=headers, timeout=10)
                    if test_res.status_code == 200:
                        test_soup = BeautifulSoup(test_res.text, 'html.parser')
                        test_comments = test_soup.select('.commentthread_comment, .forum_comment')
                        if len(test_comments) > 0:
                            has_next_page = True
                            print(f"    [→] 检测到更多页面，继续翻页...")
            
            if not has_next_page:
                print(f"    [✅] 已到达最后一页，共 {page_num} 页")
                break
            
            page_num += 1
        
        print(f"    [📦] 共抓取 {len(all_comments)} 条评论 ({page_num - 1} 页)")
        return all_comments
        
    except Exception as e:
        print(f"  [WARN] 帖子提取异常 {url}: {e}")
        return []

async def async_process_comment(comment: Dict[str, Any], idx: int, total: int, semaphore: asyncio.Semaphore):
    """微观明细处理 - 带超时保护"""
    async with semaphore:
        stop_event = asyncio.Event()
        spinner_task = asyncio.create_task(progress_spinner(idx, total, f"AI 诊断 {comment['author']}", stop_event))
        
        loop = asyncio.get_event_loop()
        retries = 2 
        
        try:
            ai_res = None
            # 获取主帖上下文（如果有）
            context = comment.get('context')
            for attempt in range(retries):
                try:
                    ai_res = await asyncio.wait_for(
                        loop.run_in_executor(None, analyze_micro_feedback, comment['content'], context),
                        timeout=60
                    )
                    break
                except asyncio.TimeoutError:
                    if attempt == retries - 1:
                        print(f"\n  [⚠️] 评论 {idx} AI 诊断超时，跳过")
                        raise
                    print(f"\n  [⚠️] 评论 {idx} 第 {attempt+1} 次尝试超时，重试...")
                    await asyncio.sleep(1)
                except Exception as inner_e:
                    if attempt == retries - 1: 
                        raise inner_e
                    await asyncio.sleep(2)
            
            if not ai_res: 
                raise ValueError("AI 返回为空")

            # 写入飞书
            fields = build_feishu_record(comment, ai_res, remark="Steam API 直抓")
            await loop.run_in_executor(None, write_to_feishu, fields, FEISHU_TABLE_ID)
            
            return comment["comment_id"]
            
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            print(f"\n[ERR] 处理评论 {comment.get('comment_id')} 时异常: {e}")
            return None
        finally:
            # 确保旋转动画停止
            stop_event.set()
            try:
                await asyncio.wait_for(spinner_task, timeout=1)
            except:
                pass

# ================= 主程序流水线 =================
async def main_pipeline():
    history_ids, latest_ts_map = load_history()
    if STEAM_IGNORE_HISTORY:
        history_ids = {}
        latest_ts_map = {}
    
    # --- 智能代理探测开始 ---
    actual_proxies = None
    # 1. 优先尝试脚本自带的 37890 (Socks5)
    if check_proxy_availability(PROXY_URL):
        actual_proxies = {"http": PROXY_URL, "https": PROXY_URL}
        print(f"[🌐] 成功挂载脚本专属代理: {PROXY_URL}")
    # 2. 如果 37890 没开，尝试检测你常开启的 代理软件 (17890)
    elif check_proxy_availability("http://127.0.0.1:17890"):
        actual_proxies = {"http": "http://127.0.0.1:17890", "https": "http://127.0.0.1:17890"}
        print(f"[🌐] 专属代理未启动，自动切至 代理软件 (17890) 通道")
    else:
        print("[⚠️] 未检测到任何可用代理，将尝试直连（可能会失败）")
    
    # 更新全局 session
    if actual_proxies:
        session.proxies = actual_proxies
    # --- 智能代理探测结束 ---
    
    print(f"\n[🚀] Steam 全局巡逻兵 | 缓存: {len(history_ids)} 条")
    
    # 大循环：依次遍历三个板块，并动态解包专属 limit
    selected_subforums = select_subforums()
    for subforum_path, config in selected_subforums.items():
        subforum_name = config["name"]
        fetch_limit = config["limit"]
        
        topic_list = get_latest_forum_topics(STEAM_APP_ID, subforum_path, fetch_limit)
        if not topic_list:
            continue
        
        # ==================== 帖子级别30天过滤（核心修复）====================
        # 计算30天前的时间阈值（毫秒）
        post_time_threshold_ms = int((time.time() - 30 * 24 * 3600) * 1000)

        # 过滤出最近30天内发布的新帖子
        # 关键：列表页解析不到发帖时间（created_time_ms=0）时，不再"乐观保留"
        # 而是放进"待验证"列表，让 fetch_steam_comments 在详情页严格验证
        # 这样能确保即使列表页解析失败，也不会漏掉旧帖拦截
        recent_topics = []      # 已确认是新帖（列表页解析到时间且在 30 天内）
        pending_topics = []     # 待详情页验证（列表页未解析到时间）
        skipped_old_topics = 0  # 列表页解析到时间且已确认是旧帖

        for topic in topic_list:
            created_time = topic.get("created_time_ms", 0)

            if created_time > 0 and created_time >= post_time_threshold_ms:
                # 列表页解析到时间戳，且在 30 天内 → 确认为新帖
                recent_topics.append(topic)
            elif created_time > 0 and created_time < post_time_threshold_ms:
                # 列表页解析到时间戳，且超过 30 天 → 跳过
                skipped_old_topics += 1
            else:
                # 列表页未解析到时间戳（created_time == 0）→ 待详情页验证
                pending_topics.append(topic)

        if skipped_old_topics > 0:
            print(f"[🛡️] 帖子级时间过滤：已跳过 {skipped_old_topics} 篇超过30天的旧帖（列表页判定）")

        # 合并：recent_topics + pending_topics（pending 会在详情页被严格过滤）
        all_candidate_topics = recent_topics + pending_topics
        if not all_candidate_topics:
            print(f"[SKIP] 板块 {subforum_name} 无候选帖子。")
            continue

        if pending_topics:
            print(f"[🔍] {len(pending_topics)} 篇帖子列表页未解析到发帖时间，将在详情页严格验证 OP 时间戳")

        topic_urls = [t["url"] for t in all_candidate_topics]
        print(f"[📋] 待抓取帖子数: {len(topic_urls)} 篇（已确认新帖: {len(recent_topics)}，待验证: {len(pending_topics)}）")
        # =====================================================================

        latest_ts = latest_ts_map.get(subforum_name, 0)

        async def fetch_and_filter(url: str) -> tuple:
            """并发抓取单个帖子并过滤历史"""
            print(f"  [↓] 正在提取帖子内容: {url}")
            # 传入帖子发布时间阈值，fetch_steam_comments 会在首页验证主楼发布时间
            # 如果主楼超过30天，函数会立即返回空列表，不浪费请求
            comments = await fetch_steam_comments(url, subforum_name, time_threshold_ms=post_time_threshold_ms)
            
            # 只过滤已处理的评论（基于 comment_id 去重）
            # 注意：时间过滤已在帖子级别完成，这里不需要再次过滤
            new_batch = [c for c in reversed(comments) if c["comment_id"] not in history_ids]
            
            return new_batch, comments

        print(f"[🚀] 开始并发抓取 {len(topic_urls)} 个帖子...")
        results = await asyncio.gather(*[fetch_and_filter(url) for url in topic_urls])

        all_new_comments = []
        for new_batch, comments in results:
            all_new_comments.extend(new_batch)
            if comments:
                max_ts = max(c["post_time_ms"] for c in comments)
                if max_ts > latest_ts:
                    latest_ts = max_ts

        latest_ts_map[subforum_name] = latest_ts

        if not all_new_comments:
            print(f"[SKIP] 板块 {subforum_name} 无新消息。")
            continue

        # 微观轨道并发处理
        print(f"[⚡] 正在并发处理 {subforum_name} 的 {len(all_new_comments)} 条明细...")
        sem = asyncio.Semaphore(3)
        tasks = [async_process_comment(c, i, len(all_new_comments), sem) for i, c in enumerate(all_new_comments, 1)]
        results = await asyncio.gather(*tasks)

        new_ts_map = {c["comment_id"]: c["post_time_ms"] for c in all_new_comments}
        for rid in results:
            if rid and isinstance(rid, str) and rid in new_ts_map:
                history_ids[rid] = new_ts_map[rid]
        save_history(history_ids, latest_ts_map)

    print(f"\n[🎉] Steam 全量任务已完美执行！")

if __name__ == "__main__":
    try:
        asyncio.run(main_pipeline())
    except KeyboardInterrupt:
        print("\n[STOP] 用户强制停止。")
