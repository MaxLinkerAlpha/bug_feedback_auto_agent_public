#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
百度贴吧爬虫模块 - 使用 Playwright 真实浏览器抓取 tieba.baidu.com

技术方案说明（v2，2026-08-31 由 requests 切换为 Playwright）：
  - 贴吧为国内站点、服务端渲染 HTML。百度对非浏览器流量会启用「百度安全验证」，
    requests/curl 请求返回 403，故改用 Playwright 驱动 Chromium 抓取。
  - 复用腾讯频道 spider_tencent.py 的 launch_persistent_context 模式：
    持久化登录态目录（playwright_tieba_data），首次可用有头模式扫码登录。
  - 贴吧国内站默认走系统网络（由 代理软件 的 baidu→Domestic 直连规则路由），代码内不额外配置代理。

抓取流程（参考 Steam 论坛的三层验证架构）：
  1. 列表页 f?kw=... → 点「最新」标签（按最后回复时间降序，贴吧没有按发帖时间排序的标签页）
     → 解析 div.thread-card 的 tid/标题/「回复于」时间
  2. 列表层预过滤：卡片回复时间早于 30 天窗口 → OP 必然更早，直接跳过不进详情页；
     列表底部卡片回复时间早于窗口 → 深处全是旧帖，提前停止滚动
  3. 详情页 p/{tid} → JS 提取楼主（正文 + 发帖时间，做 30 天窗口权威过滤）+ 固定数量楼层回复
  4. 跳过已抓 tid（去重）
  5. 复用 analyze_micro_feedback + build_feishu_record + write_to_feishu

排序语义说明（2026-09-03 确认）：
  - 贴吧列表「最新」= 按最后回复时间降序，旧帖有人回复就浮到前面（平台无发帖时间排序）
  - 因此详情页会遇到"旧帖新回复"（OP 55-69 天前但回复是昨天的），30 天 OP 过滤会正确拦截
  - 新发无回复的帖子，最后回复时间 = 发帖时间，天然排在列表高位，不会被漏掉
"""

import os
import json
import time
import asyncio
import sys
import re
from typing import Dict, Any, List
from playwright.async_api import async_playwright, Page

# 引入核心基建（不修改 base_utils 函数签名，直接复用）
from base_utils import (
    setup_windows_console,
    analyze_micro_feedback,
    write_to_feishu,
    build_feishu_record,
    trim_history_map,
    HISTORY_MAX_SIZE
)

setup_windows_console()

# ================= 配置读取 =================
TIEBA_KW = os.getenv("TIEBA_KW", "你的游戏名")
TIEBA_FETCH_LIMIT = int(os.getenv("TIEBA_FETCH_LIMIT", 15))
TIEBA_MAX_REPLIES = int(os.getenv("TIEBA_MAX_REPLIES", 10))
TIEBA_MAX_DAYS = int(os.getenv("TIEBA_MAX_DAYS", 30))
TIEBA_HEADLESS = os.getenv("TIEBA_HEADLESS", "True").lower() == "true"
HISTORY_FILE = "tieba_history.json"
PLATFORM_NAME = f"[贴吧] {TIEBA_KW}"
USER_DATA_DIR = os.path.join(os.getcwd(), "playwright_tieba_data")
# ==========================================


def load_history() -> dict:
    """加载历史记录，兼容旧版格式"""
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            result = {}
            for k, v in data.items():
                if isinstance(v, dict):
                    result[str(k)] = {
                        "timestamp": int(v.get("timestamp", 0)),
                        "title_prefix": v.get("title_prefix", "")
                    }
                else:
                    result[str(k)] = {
                        "timestamp": int(v) if isinstance(v, (int, float)) else 0,
                        "title_prefix": ""
                    }
            return result
        if isinstance(data, list):
            return {str(i): {"timestamp": 0, "title_prefix": ""} for i in data}
        return {}
    except Exception as e:
        print(f"[WARN] 历史记录读取异常: {e}")
        return {}


def save_history(history_map: dict) -> None:
    """保存历史记录"""
    try:
        trimmed = trim_history_map(
            {k: v["timestamp"] for k, v in history_map.items()},
            HISTORY_MAX_SIZE
        )
        migrated = {}
        for k, ts in trimmed.items():
            if k in history_map:
                migrated[k] = {
                    "timestamp": ts,
                    "title_prefix": history_map[k].get("title_prefix", "")
                }
            else:
                migrated[k] = {"timestamp": ts, "title_prefix": ""}
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(migrated, f, ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] 历史记录保存失败: {e}")


def parse_tieba_time(date_str: str) -> int:
    """
    解析贴吧时间文本，返回毫秒时间戳（失败返回 0）。
    支持格式：
      - 绝对：2024-01-01 12:00 / 2024-01-01 12:00:00 / 2024-01-01
      - 中文：2024年1月1日 12:00 / 1月1日 12:00 / 1月1日
      - 相对：今天 12:00 / 昨天 12:00 / 前天 12:00 / X分钟前 / X小时前 / X天前
      - 纯时间：12:00（默认今天）
    """
    if not date_str:
        return 0

    from datetime import datetime, timedelta
    now = datetime.now()
    text = date_str.strip()

    # 相对时间（分钟/小时/天前）
    rel_patterns = [
        (r'(\d+)\s*秒前', 'seconds'),
        (r'(\d+)\s*分钟前', 'minutes'),
        (r'(\d+)\s*小时前', 'hours'),
        (r'(\d+)\s*天前', 'days'),
    ]
    for pattern, unit in rel_patterns:
        m = re.search(pattern, text)
        if m:
            value = int(m.group(1))
            delta_map = {
                'seconds': timedelta(seconds=value),
                'minutes': timedelta(minutes=value),
                'hours': timedelta(hours=value),
                'days': timedelta(days=value),
            }
            return int((now - delta_map[unit]).timestamp() * 1000)

    # 今天/昨天/前天 + 时间
    day_offset = None
    for label, off in [("前天", 2), ("昨天", 1), ("今天", 0)]:
        if text.startswith(label):
            day_offset = off
            text = text[len(label):].strip()
            break
    if day_offset is not None and text:
        m = re.match(r'^(\d{1,2})[:：](\d{2})(?::(\d{2}))?$', text)
        if m:
            dt = datetime(now.year, now.month, now.day,
                          int(m.group(1)), int(m.group(2))) - timedelta(days=day_offset)
            return int(dt.timestamp() * 1000)

    # 绝对时间：2024-01-01 12:00(:ss)
    m = re.match(
        r'^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?\s+(\d{1,2})[:：](\d{2})(?::(\d{2}))?$',
        text
    )
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)),
                          int(m.group(4)), int(m.group(5)))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass

    # 绝对日期：2024-01-01（无时间）
    m = re.match(r'^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?$', text)
    if m:
        try:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 0, 0)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass

    # 短日期：MM-DD（无年份，默认当前年；贴吧精华帖格式，如 05-14 / 08-27）
    m = re.match(r'^(\d{1,2})-(\d{1,2})$', text)
    if m:
        try:
            dt = datetime(now.year, int(m.group(1)), int(m.group(2)), 0, 0)
            if dt > now:  # 未来日期（如当前 08-31 而日期是 12-25），回退一年
                dt = dt.replace(year=now.year - 1)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass

    # 中文短绝对：1月1日 12:00（无年份，默认当前年）
    m = re.match(r'^(\d{1,2})月(\d{1,2})日?\s+(\d{1,2})[:：](\d{2})$', text)
    if m:
        try:
            dt = datetime(now.year, int(m.group(1)), int(m.group(2)),
                          int(m.group(3)), int(m.group(4)))
            if dt > now:
                dt = dt.replace(year=now.year - 1)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass

    # 纯时间 HH:mm（今天）
    m = re.match(r'^(\d{1,2})[:：](\d{2})$', text)
    if m:
        try:
            dt = datetime(now.year, now.month, now.day,
                          int(m.group(1)), int(m.group(2)))
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass

    return 0


# --- Playwright 核心抓取逻辑 ---

_JS_EXTRACT_TOPICS = """
() => {
    const result = [];
    document.querySelectorAll('div.thread-card').forEach(card => {
        const link = card.querySelector('a[href*="/p/"]');
        const href = link ? link.getAttribute('href') : '';
        let tid = '';
        try { tid = String(href.split('/p/')[1].split(/[?&]/)[0]); } catch(e) {}
        const titleEl = card.querySelector('.thread-title');
        const title = titleEl ? titleEl.textContent.trim() : '';
        // 卡片上的「回复于X」= 最后回复时间（「最新」标签按此降序排列）
        let replyTime = '';
        const txt = card.textContent;
        let m = txt.match(/回复于\\s*(20[0-9][0-9]-[0-9]{1,2}-[0-9]{1,2})/);
        if (!m) m = txt.match(/回复于\\s*([0-9]{1,2}-[0-9]{1,2})/);
        if (!m) m = txt.match(/回复于\\s*((?:今天|昨天|前天)\\s*[0-9]{1,2}[:：][0-9]{2}|[0-9]+分钟前|[0-9]+小时前|[0-9]+天前)/);
        if (!m) m = txt.match(/回复于\\s*([0-9]{1,2}[:：][0-9]{2})/);
        if (m) replyTime = m[1];
        if (tid) result.push({tid, title, reply_time: replyTime});
    });
    return result;
}
"""

_JS_EXTRACT_OP = """
() => {
    const result = {op_author: '', op_content: '', op_time: '', op_title: ''};
    // 帖子标题（详情页 <title> 去掉「-百度贴吧」后缀；媒体帖在列表页无标题，从这里兜底）
    result.op_title = (document.title || '').replace(/-百度贴吧$/, '').trim();
    // 楼主作者 + 发帖时间：从第一个 .head-info（楼主头部）提取
    const firstHeadInfo = document.querySelector('.head-info');
    if (firstHeadInfo) {
        const link = firstHeadInfo.querySelector('a');
        if (link) result.op_author = link.textContent.trim().split(/\\s+/)[0];
        const txt = firstHeadInfo.textContent;
        let m = txt.match(/20[0-9][0-9]-[0-9]{2}-[0-9]{2}/);
        if (!m) m = txt.match(/(今天|昨天|前天)[\\s]*[0-9]{1,2}[:：][0-9]{2}|[0-9]+分钟前|[0-9]+小时前|[0-9]+天前/);
        if (!m) m = txt.match(/[0-9]{1,2}-[0-9]{1,2}/);
        if (m) result.op_time = m[0];
    }
    // 楼主正文（页面第一个 .pb-text-wrapper，位于楼主卡片 .richtext-item 内）
    const opEl = document.querySelector('.pb-text-wrapper');
    if (opEl) result.op_content = opEl.textContent.trim();
    return result;
}
"""

_JS_EXTRACT_REPLIES = """
() => {
    const replies = [];
    document.querySelectorAll('.pb-comment-item').forEach((item, index) => {
        const authorEl = item.querySelector('.head-info a');
        let author = '';
        if (authorEl) author = authorEl.textContent.trim().split(/\\s+/)[0];
        const contentEl = item.querySelector('.pb-text-wrapper');
        let content = contentEl ? contentEl.textContent.trim() : '';
        content = content.replace(/(收起回复|来自[^\\s]*客户端|来自[^\\s]*贴吧)/g, '').trim();
        const desc = item.querySelector('.comment-desc-left');
        let floor = '';
        if (desc) {
            desc.querySelectorAll('span').forEach(s => {
                const t = s.textContent.trim();
                if (t.indexOf('第') === 0 && t.indexOf('楼') > 0) floor = t.replace(/第|楼/g, '');
            });
        }
        if (!floor) floor = String(index + 2);  // 无楼层号标记时用序号近似（精华帖无「第X楼」）
        if (content) replies.push({floor, author: author || '匿名', content});
    });
    return replies;
}
"""


async def _is_captcha_page(page: Page) -> bool:
    """检测页面是否为百度安全验证页"""
    try:
        title = await page.title()
        if "安全验证" in title or "百度安全验证" in title:
            return True
        content = await page.content()
        if "百度安全验证" in content and "j_thread_list" not in content and "j_p_postlist" not in content:
            return True
    except Exception:
        pass
    return False


async def fetch_tieba_posts(history: dict) -> List[Dict[str, Any]]:
    """Playwright 抓取贴吧列表 + 详情，返回组装好的帖子列表"""
    parsed = []

    print(f"\n[🚀] 启动 Playwright 真实浏览器 (headless={TIEBA_HEADLESS})...")
    if not TIEBA_HEADLESS:
        print(f"[⚠️] 有头模式：若遇「百度安全验证」，请在浏览器中完成验证或登录后再重试。")

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            USER_DATA_DIR,
            headless=TIEBA_HEADLESS,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = await browser.new_page()

        try:
            # 0. 先访问百度首页，种下 BAIDUID 匿名 cookie，避免触发「百度安全验证」
            try:
                await page.goto("https://www.baidu.com/", wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1500)
                print("[🍪] 已访问百度首页获取匿名 cookie")
            except Exception as e:
                print(f"  [WARN] 百度首页预热失败（不影响主流程）: {e}")

            # 1. 列表页滚动加载，收集候选帖子（跳过已抓 tid）
            candidates: List[Dict[str, str]] = []
            list_url = f"https://tieba.baidu.com/f?kw={TIEBA_KW}&ie=utf-8&pn=0"
            print(f"[🔎] 访问贴吧列表: {list_url}")

            try:
                await page.goto(list_url, wait_until="domcontentloaded", timeout=30000)
                # 先等待排序标签出现，切换到「最新」后帖子卡片才会渲染
                await page.wait_for_selector("a.tab-item", timeout=15000)
                try:
                    await page.locator("a.tab-item", has_text="最新").first.click()
                    await page.wait_for_timeout(2000)
                    print("[🕐] 已切换「最新」排序（按最后回复时间降序）")
                except Exception as e:
                    print(f"  [WARN] 切换「最新」排序失败，将按默认排序抓取: {e}")
                await page.wait_for_selector("div.thread-card", timeout=15000)
            except Exception as e:
                print(f"  [WARN] 列表页加载失败: {e}")
                if await _is_captcha_page(page):
                    print(f"  [WARN] 触发「百度安全验证」，请改用有头模式(TIEBA_HEADLESS=False)手动验证后重试。")
                return parsed

            seen_tids = set()
            scroll_attempts = 0
            # 「最新」标签按【最后回复时间】降序（贴吧没有按发帖时间排序的标签页）。
            # 预过滤：卡片回复时间已在窗口外 → OP 时间必然更早（发帖 ≤ 最后回复），无需进详情页。
            # 早期停滚：本页最底部卡片的回复时间已早于阈值 → 更深处不可能有窗口内帖子。
            candidate_target = TIEBA_FETCH_LIMIT * 3
            cutoff_ms = int((time.time() - TIEBA_MAX_DAYS * 24 * 3600) * 1000)
            prefiltered = 0
            while len(candidates) < candidate_target and scroll_attempts < 20:
                topics = await page.evaluate(_JS_EXTRACT_TOPICS)
                for t in topics:
                    if t["tid"] in seen_tids:
                        continue
                    seen_tids.add(t["tid"])
                    if t["tid"] in history:
                        continue  # 已抓过，跳过
                    rt = parse_tieba_time(t.get("reply_time", ""))
                    if rt > 0 and rt < cutoff_ms:
                        prefiltered += 1
                        continue
                    candidates.append({"tid": t["tid"], "title": t["title"]})
                    if len(candidates) >= candidate_target:
                        break
                if len(candidates) >= candidate_target:
                    break
                # 早期停滚：最后一张（最深）可见卡片的回复时间早于窗口 → 深处全是旧帖
                if topics:
                    last_rt = parse_tieba_time(topics[-1].get("reply_time", ""))
                    if last_rt > 0 and last_rt < cutoff_ms and candidates:
                        print(f"[🛑] 列表底部帖子最后回复已早于 {TIEBA_MAX_DAYS} 天窗口，停止滚动")
                        break
                # 滚动加载更多帖子（新版贴吧为虚拟列表懒加载，滚动容器为 .frs-page-wrap）
                await page.evaluate("""() => {
                    const scroller = document.querySelector('.frs-page-wrap') || document.querySelector('.left-content');
                    if (scroller) scroller.scrollTop += 3000;
                    else window.scrollBy(0, 3000);
                }""")
                await page.wait_for_timeout(1500)
                scroll_attempts += 1

            print(f"[📋] 候选新帖 {len(candidates)} 篇（回复时间预过滤 {prefiltered} 篇 | 历史 {len(history)} 条）")

            if not candidates:
                print("[SKIP] 贴吧无新帖。")
                return parsed

            # 2. 逐个详情页下潜 + 时间过滤
            post_time_threshold_ms = int((time.time() - TIEBA_MAX_DAYS * 24 * 3600) * 1000)
            skipped_old = 0

            for topic in candidates:
                tid = topic["tid"]
                url = f"https://tieba.baidu.com/p/{tid}"
                print(f"  [↓] 下潜详情页: {topic.get('title', '')[:30]}...")

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_selector(".head-info", timeout=10000)
                except Exception as e:
                    print(f"    [WARN] 详情页加载失败: {e}")
                    if await _is_captcha_page(page):
                        print(f"    [WARN] 触发「百度安全验证」，中断抓取。")
                        break
                    continue

                # 先提取楼主信息（此时不滚动，避免虚拟列表回收 .head-info）
                op = await page.evaluate(_JS_EXTRACT_OP)

                # 滚动加载楼层（虚拟列表懒加载，滚动容器为 .left-content）
                for _ in range(3):
                    await page.evaluate("""() => {
                        const scroller = document.querySelector('.left-content') || document.querySelector('.frs-page-wrap');
                        if (scroller) scroller.scrollTop += 2500;
                        else window.scrollBy(0, 2500);
                    }""")
                    await page.wait_for_timeout(800)

                replies = await page.evaluate(_JS_EXTRACT_REPLIES)
                detail = {**op, "replies": replies}
                op_time_ms = parse_tieba_time(detail.get("op_time", ""))

                # 时间过滤（宁可漏抓不可错抓）
                if op_time_ms <= 0:
                    print(f"    [⏭️] 无法解析楼主发帖时间，保守跳过")
                    continue
                if op_time_ms < post_time_threshold_ms:
                    days_old = (time.time() * 1000 - op_time_ms) / (24 * 3600 * 1000)
                    print(f"    [⏭️] 楼主发布于 {days_old:.0f} 天前，超过 {TIEBA_MAX_DAYS} 天限制，跳过")
                    skipped_old += 1
                    continue

                # 组装内容：标题 + 楼主正文 + 固定楼层回复
                # 标题优先取列表页；媒体帖列表无标题，用详情页 <title> 兜底
                title = topic.get("title", "") or detail.get("op_title", "") or ""
                op_content = detail.get("op_content", "")
                op_author = detail.get("op_author", "") or "匿名"
                replies = detail.get("replies", [])[:TIEBA_MAX_REPLIES]

                parts = [f"【标题】{title}"]
                parts.append(f"【楼主 {op_author}】: {op_content}")
                if replies:
                    reply_lines = [
                        f"  [{r['floor']}楼 {r['author']}]: {r['content']}"
                        for r in replies
                    ]
                    parts.append("【楼内回复】\n" + "\n".join(reply_lines))

                full_content = "\n".join(parts)
                if len(full_content) > 5000:
                    full_content = full_content[:5000] + "\n...(内容过长，已截断)"

                parsed.append({
                    "comment_id": tid,
                    "author": op_author,
                    "content": full_content,
                    "post_time_ms": op_time_ms,
                    "url": url,
                    "platform": PLATFORM_NAME,
                    "title_prefix": title[:6]
                })

                if len(parsed) >= TIEBA_FETCH_LIMIT:
                    break  # 已收集够 30 天内的新帖

                await page.wait_for_timeout(300)  # 详情页间隔

            if skipped_old > 0:
                print(f"[🛡️] 时间过滤：已跳过 {skipped_old} 篇超过 {TIEBA_MAX_DAYS} 天的旧帖")

        except Exception as e:
            print(f"[ERR] 贴吧爬虫调度异常: {e}")
        finally:
            try:
                await asyncio.wait_for(browser.close(), timeout=10)
            except asyncio.TimeoutError:
                print("[WARN] 浏览器关闭超时，强制结束。")
            except Exception as e:
                print(f"[WARN] 浏览器关闭异常: {e}")

    return parsed


# --- AI 与 数据处理 ---

async def async_process_post(post: Dict[str, Any], idx: int, total: int, semaphore: asyncio.Semaphore):
    """并发 AI 处理单条帖子并写入飞书"""
    async with semaphore:
        loop = asyncio.get_event_loop()
        try:
            content = post["content"]
            context = ""
            if "【标题】" in content:
                title_start = content.find("【标题】") + len("【标题】")
                title_end = content.find("\n", title_start)
                if title_end == -1:
                    title_end = len(content)
                context = content[title_start:title_end].strip()[:150]

            ai_res = await asyncio.wait_for(
                loop.run_in_executor(None, analyze_micro_feedback, content, context),
                timeout=90
            )

            if not ai_res:
                raise ValueError("AI 无响应或返回为空")

            fields = build_feishu_record(post, ai_res, remark="百度贴吧 Playwright 直抓")
            await loop.run_in_executor(None, write_to_feishu, fields, None)

            print(
                f"  [OK] [进度 {idx}/{total}] 完成 {post['author']} "
                f"| 分类: {fields.get('分类', '?')} | 优先级: {fields.get('AI 优先级', '?')}",
                flush=True
            )
            return post["comment_id"]

        except Exception as e:
            print(f"  [FAIL] [进度 {idx}/{total}] 失败 {post.get('author', '?')}: {e}", flush=True)
            return None


async def main_pipeline():
    """贴吧主管线：Playwright 抓取 → 时间过滤 → AI 并发 → 更新历史"""
    history = load_history()
    print(f"\n[贴吧] 百度贴吧哨兵启动 | 目标: {TIEBA_KW} | 缓存: {len(history)} 条")

    new_batch = await fetch_tieba_posts(history)

    if not new_batch:
        print("[SKIP] 贴吧无新帖，结束巡逻。")
        return

    print(f"[⚡] 正在并发处理 {len(new_batch)} 篇贴吧帖子 (AI 3并发)...")
    sem = asyncio.Semaphore(3)
    tasks = [
        async_process_post(p, i, len(new_batch), sem)
        for i, p in enumerate(new_batch, 1)
    ]
    results = await asyncio.gather(*tasks)

    new_ts_map = {
        p["comment_id"]: {
            "timestamp": p["post_time_ms"],
            "title_prefix": p.get("title_prefix", "")
        }
        for p in new_batch
    }
    for rid in results:
        if rid and isinstance(rid, str) and rid in new_ts_map:
            history[rid] = new_ts_map[rid]
    save_history(history)
    print(f"\n[🎉] 贴吧增量抓取完成！新入库 {len([r for r in results if r])} 篇。")


if __name__ == "__main__":
    try:
        asyncio.run(main_pipeline())
    except KeyboardInterrupt:
        print("\n[STOP] 用户强制停止。")
