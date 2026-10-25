#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Reddit 爬虫模块 - 使用 requests + BeautifulSoup 抓取 old.reddit.com

技术方案说明：
  old.reddit.com 为服务端渲染 HTML，无需 JS 执行，无需浏览器。
  
  方案对比（v2.4.0 结论）：
  ┌──────────────┬──────────┬──────────┬──────────────────────────┐
  │ 方案          │ 评论     │ 代理冲突  │ 结论                      │
  ├──────────────┼──────────┼──────────┼──────────────────────────┤
  │ .json API    │ 有       │ 403 封禁 │ 已废弃（Reddit 封锁 API） │
  │ RSS          │ 无       │ 无       │ 数据不完整，仅作备选       │
  │ Playwright   │ 有       │ 有       │ 代理与 Chromium 冲突      │
  │ requests+BS4 │ 有       │ 无       │ ✅ 最优方案（当前采用）   │
  └──────────────┴──────────┴──────────┴──────────────────────────┘

  为什么海外平台默认走代理：
    - Reddit/Discord 等海外站点在部分网络（尤其国内）直连不稳定甚至不可达
    - 因此统一默认走 PROXIES（SELECTED_PROXY_URL 智能代理），
      代理不可用时（PROXIES=None）自动回退直连
    - 新版 Reddit (www) 使用大量 JS/AJAX，依赖 Google/DoubleClick 等被封域，
      故抓取仍用 old.reddit.com 的纯 HTML 页面

抓取流程：
  1. GET old.reddit.com/r/{subreddit}/new/ → 解析 .thing 列表
  2. 对每个帖子 GET 详情页 → 提取正文 + 评论
  3. 翻页 via span.next-button a (after= token)
  4. 碰历史边界停止
"""

import os
import json
import time
import asyncio
import sys
import re
import requests
from typing import Dict, Any, List
from bs4 import BeautifulSoup

# 引入核心基建
from base_utils import (
    setup_windows_console, 
    analyze_micro_feedback, 
    write_to_feishu,
    build_feishu_record,
    SELECTED_PROXY_URL,
    AI_MODEL_HEAVY,
    rate_limit,
    trim_history_map,
    HISTORY_MAX_SIZE
)

setup_windows_console()

# 模块级风控标记：403=节点被封禁；CAPTCHA=IP 被 Reddit 人机验证风控拦截
_blocked_flag = False
_captcha_flag = False

# Reddit CAPTCHA 页面特征（old.reddit 被风控时返回 "Welcome to Reddit" 验证页）
CAPTCHA_MARKERS = ["Welcome to Reddit", "captcha", "verify you're human", "verification"]

# ================= 配置读取 =================
REDDIT_SUBREDDIT = os.getenv("REDDIT_SUBREDDIT", "your_subreddit")
REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
REDDIT_FETCH_LIMIT = int(os.getenv("REDDIT_FETCH_LIMIT", 15))
# 登录态 cookie 文件（EditThisCookie 导出格式，含 reddit_session/token_v2 等）
# 带有效 cookie 可显著降低 CAPTCHA 触发率，有效期约 6 个月，需定期更新
REDDIT_COOKIE_FILE = "reddit_cookies.json"

# 完整自洽的浏览器请求头集合（仅换 UA 无意义，TLS 指纹 + 头自洽性才是关键）
REDDIT_HEADERS = {
    "User-Agent": REDDIT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-CH-UA": '"Chromium";v="131", "Google Chrome";v="131", "Not.A/Brand";v="24"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# old.reddit.com 不需要代理（国内可直连），所以不使用 SELECTED_PROXY_URL
# 保留 PROXY_URL 变量供其他场景使用，但在 old.reddit 请求中不传入 proxies
PROXY_URL = SELECTED_PROXY_URL
PROXIES = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None
HISTORY_FILE = "reddit_history.json"
# ==========================================


def load_reddit_cookies(session) -> None:
    """加载 reddit_cookies.json 登录态 cookie 到会话，降低 CAPTCHA 触发率"""
    if not os.path.exists(REDDIT_COOKIE_FILE):
        print(f"  [INFO] 未找到 {REDDIT_COOKIE_FILE}，将无 cookie 匿名抓取")
        return
    try:
        with open(REDDIT_COOKIE_FILE, 'r', encoding='utf-8') as f:
            cookie_list = json.load(f)
        count = 0
        for c in cookie_list:
            # 仅注入 .reddit.com 域 cookie（hostOnly=www.reddit.com 的键值对 old.reddit 用不上）
            domain = c.get("domain", "")
            name = c.get("name", "")
            value = c.get("value", "")
            if not domain.startswith(".reddit.com") or not name:
                continue
            session.cookies.set(name, value, domain=".reddit.com", path=c.get("path", "/"))
            count += 1
        print(f"  [INFO] 已加载 {count} 个 Reddit 登录态 cookie（{REDDIT_COOKIE_FILE}）")
    except Exception as e:
        print(f"  [WARN] cookie 加载失败: {e}，将以匿名方式抓取")


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


async def progress_spinner(
    current_idx: int, total: int, msg: str, stop_event: asyncio.Event
):
    """
    进度提示：仅在任务开始时打一行标记，结束时不擦除。
    之前的实现使用 \\r\\033[K 行内更新会**覆盖**掉 AI 完成后打印的"写入成功"日志，
    导致用户在终端看不到任何反馈（虽然日志文件里是有的）。
    现在简化为"开始提示 → 等待 → 自动结束"，保证所有 print 行都能正常显示。
    """
    sys.stdout.write(
        f"  [..] [进度 {current_idx}/{total}] 开始 {msg}\n"
    )
    sys.stdout.flush()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    # 不再做任何输出清理，避免覆盖其他 print


def clean_text_noise(text: str) -> str:
    """物理除杂器：剥离 http/https 链接，防止污染 AI 总结"""
    if not text:
        return ""
    return re.sub(r'https?://\S+|www\.\S+', '', text).strip()


# ==========================================
#  核心：old.reddit.com HTML 抓取
# ==========================================

def _safe_get(session: requests.Session, url: str, max_retries: int = 5) -> requests.Response:
    """
    带重试的 GET 请求，处理 429 限流与网络超时。
    - 海外平台（old.reddit.com）默认走代理 PROXIES，代理不可用时回退直连
    - 连接超时 15s，读取超时 30s（放宽，避免海外直连波动导致频繁失败）
    - 指数退避 + 随机抖动（2s → 4s → 8s → 16s → 30s），避免集中冲击服务器
    - 429 同样走退避重试
    """
    import random
    for attempt in range(max_retries):
        try:
            r = session.get(url, timeout=(15, 30), proxies=PROXIES)
            if r.status_code == 429:
                wait = 2 ** (attempt + 1)
                print(f"    [429 限流] 等待 {wait}s 后重试...")
                time.sleep(wait)
                continue
            return r
        except requests.RequestException as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1) + random.uniform(0, 1)
                print(f"    [请求失败] {e}，{wait:.1f}s 后重试...")
                time.sleep(wait)
            else:
                raise
    # 理论不可达，但保留兜底
    raise RuntimeError(f"请求 {url} 重试 {max_retries} 次后仍失败")


def _parse_post_time(datetime_str: str) -> int:
    """将 ISO 时间字符串转为毫秒时间戳"""
    if not datetime_str:
        return 0
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _extract_detail(session: requests.Session, comments_url: str) -> tuple:
    """
    从 old.reddit.com 帖子详情页提取正文和评论。
    返回 (post_body: str, comments_text: str)
    """
    post_body = ""
    comments_text = ""

    try:
        r = _safe_get(session, comments_url)
        if r.status_code != 200:
            return post_body, comments_text

        soup = BeautifulSoup(r.text, 'html.parser')

        # --- 帖子正文 ---
        body_el = soup.select_one(
            ".expando .usertext-body .md, "
            ".usertext-body .md, "
            ".sitetable .usertext-body .md"
        )
        if body_el:
            post_body = body_el.text.strip()

        # --- 评论 ---
        # old.reddit.com 评论结构:
        # <div class="comment"> <div class="entry unvoted">
        #   <p class="tagline"><a class="author">...</a></p>
        #   <form class="usertext"><div class="usertext-body"><div class="md">text</div></div></form>
        # </div></div>
        comment_entries = soup.select(".comment .entry")
        comments_list = []
        for ce in comment_entries[:50]:  # 最多 50 条评论
            try:
                author_el = ce.select_one("a.author")
                body_el = ce.select_one(".usertext-body .md, .md")
                author = author_el.text.strip() if author_el else "Anonymous"
                body = body_el.text.strip() if body_el else ""

                if body and body not in ("[deleted]", "[removed]"):
                    clean_body = clean_text_noise(body).replace('\n', ' ')
                    comments_list.append(f"[{author}]: {clean_body}")
            except Exception:
                continue

        if comments_list:
            comments_text = (
                "\n\n【评论区讨论】\n- " + "\n- ".join(comments_list[:30])
            )

    except Exception:
        pass

    return post_body, comments_text


async def fetch_reddit_posts(
    catch_up_mode: bool = False, 
    catch_up_days: int = 45
) -> List[Dict[str, Any]]:
    """
    使用 requests + BeautifulSoup 抓取 old.reddit.com。
    
    参数:
        catch_up_mode: 追补模式，忽略历史边界，改为按日期截止
        catch_up_days: 追补模式下，只抓取最近 N 天的帖子
    """
    history = load_history()
    parsed_posts = []
    hit_history = False
    page_num = 0
    after_token = None

    # 海外平台默认走代理（PROXIES 由 _safe_get 统一传入），代理不可用时回退直连
    # 🛡️ trust_env=False 防止 base_utils 设置的全局 HTTP_PROXY 环境变量干扰，
    #    但同时保留显式 proxies=PROXIES 传递，确保代理生效
    session = requests.Session()
    session.trust_env = False
    # 完整自洽浏览器 headers（TLS 指纹 + 头一致性是规避 CAPTCHA 的关键）
    session.headers.update(REDDIT_HEADERS)
    # 注入登录态 cookie（reddit_session 等），显著降低 CAPTCHA 触发率
    load_reddit_cookies(session)

    print(f"[🔎] 正在通过 old.reddit.com 抓取 r/{REDDIT_SUBREDDIT}")
    if catch_up_mode:
        print(f"     [追补模式] 截止 {catch_up_days} 天内，跳过历史边界检查")

    # 预热：先访问首页建立会话信任（模拟回访用户，避免"新访客打深链"触发风控）
    try:
        _safe_get(session, f"https://old.reddit.com/")
    except Exception as e:
        print(f"  [WARN] 首页预热失败（不影响主流程）: {e}")

    # 正常模式：最多 REDDIT_FETCH_LIMIT 篇，最多 10 页
    # 追补模式：不限数量，最多 80 页（约 2000 帖），按日期截止
    fetch_limit = REDDIT_FETCH_LIMIT
    max_pages = 10
    if catch_up_mode:
        fetch_limit = 99999  # 追补模式不限制数量
        max_pages = 80

    while len(parsed_posts) < fetch_limit and page_num < max_pages:
        page_num += 1

        # 构建 URL
        if after_token:
            url = (
                f"https://old.reddit.com/r/{REDDIT_SUBREDDIT}/new/"
                f"?count={25*(page_num-1)}&after={after_token}"
            )
        else:
            url = f"https://old.reddit.com/r/{REDDIT_SUBREDDIT}/new/"

        if page_num > 1:
            print(f"  [翻页] 第 {page_num} 页...")

        # --- 请求列表页 ---
        try:
            r = _safe_get(session, url)
            if r.status_code != 200:
                global _blocked_flag
                if r.status_code == 403:
                    _blocked_flag = True
                    print(f"  [WARN] Reddit 返回 403，当前代理节点可能被封禁！")
                else:
                    print(f"  [WARN] 第 {page_num} 页 HTTP {r.status_code}")
                break
        except Exception as e:
            print(f"  [WARN] 第 {page_num} 页请求失败: {e}")
            break

        soup = BeautifulSoup(r.text, 'html.parser')
        entries = soup.select(".thing")

        if not entries:
            low_text = (r.text or "").lower()
            if any(m.lower() in low_text for m in CAPTCHA_MARKERS):
                global _captcha_flag
                _captcha_flag = True
                print(f"  [WARN] Reddit 返回 CAPTCHA 人机验证页，当前节点 IP 被风控拦截，建议切换节点")
            else:
                print(f"  [WARN] 未找到帖子元素 (.thing)，可能被限流")
            break

        for entry in entries:
            if len(parsed_posts) >= fetch_limit:
                break

            try:
                # --- Post ID ---
                post_id = (entry.get("data-fullname") or "").replace("t3_", "")
                if not post_id:
                    continue

                # --- 标题 ---
                title_el = entry.select_one("a.title")
                raw_title = title_el.text.strip() if title_el else ""
                if not raw_title:
                    continue

                # --- 历史边界检查（正常模式） ---
                if not catch_up_mode and post_id in history:
                    title_prefix = raw_title[:6]
                    stored = history[post_id]
                    stored_prefix = stored.get("title_prefix", "")
                    if stored_prefix and title_prefix and stored_prefix == title_prefix:
                        print(f"  -> 触碰历史边界 [{post_id}]（标题验证通过），停止。")
                        hit_history = True
                        break
                    elif not stored_prefix:
                        print(f"  -> 触碰历史边界 [{post_id}]（旧版无标题），停止。")
                        hit_history = True
                        break
                    else:
                        # 标题不符，继续抓取（可能是 Reddit 显示的标题变了）
                        continue

                # --- 追补模式：日期截止 ---
                if catch_up_mode:
                    time_el = entry.select_one("time")
                    datetime_str = time_el.get("datetime", "") if time_el else ""
                    post_time_ms = _parse_post_time(datetime_str)
                    cutoff_ms = int(time.time() * 1000) - catch_up_days * 86400000
                    if post_time_ms > 0 and post_time_ms < cutoff_ms:
                        print(f"  -> 追补截止日期已达，停止。")
                        hit_history = True
                        break
                    # 追补模式下也跳过已存在的
                    if post_id in history:
                        continue

                clean_title = clean_text_noise(raw_title)

                # --- 作者 ---
                author_el = entry.select_one("a.author")
                author_name = author_el.text.strip() if author_el else "Unknown"

                # --- 时间 ---
                time_el = entry.select_one("time")
                datetime_str = time_el.get("datetime", "") if time_el else ""
                post_time_ms = _parse_post_time(datetime_str)

                # --- 评论链接 ---
                comments_el = entry.select_one("a.comments")
                comments_url = comments_el.get("href", "") if comments_el else ""
                if not comments_url or not comments_url.startswith("http"):
                    pl = title_el.get("href", "") if title_el else ""
                    comments_url = (
                        pl
                        if pl
                        else f"https://old.reddit.com/r/{REDDIT_SUBREDDIT}/comments/{post_id}/"
                    )

                if not comments_url.startswith("https://old.reddit.com"):
                    comments_url = comments_url.replace(
                        "www.reddit.com", "old.reddit.com"
                    )

                print(f"  [NEW] {clean_title[:40]}...  ({author_name})")

                # --- 详情页：正文 + 评论 ---
                await asyncio.sleep(0.8)  # 防止请求过快被封
                post_body, comments_text = _extract_detail(session, comments_url)

                # --- 组装内容 ---
                full_body = clean_text_noise(post_body) if post_body else ""
                clean_selftext = f"\n{full_body}" if full_body else ""
                main_content = f"【Title】{clean_title}{clean_selftext}".strip()
                full_content = main_content + comments_text

                # 生成 www.reddit.com 链接（用户友好）
                www_url = comments_url.replace("old.reddit.com", "www.reddit.com")

                parsed_posts.append({
                    "comment_id": post_id,
                    "author": author_name,
                    "content": full_content,
                    "post_time_ms": post_time_ms,
                    "url": www_url,
                    "platform": f"Reddit ({REDDIT_SUBREDDIT})",
                    "title_prefix": raw_title[:6] if raw_title else ""
                })

            except Exception as e:
                print(f"    [WARN] 条目解析异常: {e}")
                continue

        if hit_history:
            break

        # --- 翻页 ---
        next_btn = soup.select_one("span.next-button a")
        if next_btn:
            href = next_btn.get("href", "")
            if "after=" in href:
                after_token = href.split("after=")[1].split("&")[0]
            else:
                after_token = None
        else:
            after_token = None

        if not after_token:
            print(f"  [INFO] 已到最后一页")
            break

        await asyncio.sleep(1.5)  # 翻页间隔

    print(f"[OK] old.reddit 抓取完成，共 {len(parsed_posts)} 篇新帖")
    return parsed_posts


# ==========================================
#  AI 处理管线（微观）
# ==========================================

async def async_process_post(
    post: Dict[str, Any],
    idx: int,
    total: int,
    semaphore: asyncio.Semaphore
):
    """并发 AI 处理单条帖子并写入飞书"""
    async with semaphore:
        stop_event = asyncio.Event()
        spinner_task = asyncio.create_task(
            progress_spinner(idx, total, f"AI 同传 {post['author']}", stop_event)
        )

        loop = asyncio.get_event_loop()
        try:
            content = post["content"]
            # 提取标题作为 AI 上下文
            context = ""
            if "【Title】" in content:
                title_start = content.find("【Title】") + len("【Title】")
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

            fields = build_feishu_record(post, ai_res, remark="Reddit old.reddit 直抓")
            # 统一写入总表（REDDIT_FEISHU_TABLE_ID 已废弃，write_to_feishu 默认回退到 FEISHU_TABLE_ID）
            await loop.run_in_executor(
                None, write_to_feishu, fields, None
            )
            stop_event.set()
            await spinner_task
            # 显式打印完成提示（独立成行，不依赖 spinner 刷新）
            print(
                f"  [OK] [进度 {idx}/{total}] 完成 {post['author']} "
                f"| 分类: {fields.get('分类', '?')} | 优先级: {fields.get('优先级', '?')}",
                flush=True
            )
            return post["comment_id"]

        except Exception as e:
            stop_event.set()
            await spinner_task
            print(
                f"  [FAIL] [进度 {idx}/{total}] 失败 {post.get('author', '?')}: {e}",
                flush=True
            )
            return None


async def main_pipeline():
    """Reddit 主管线：抓取 → 微观并发 → 更新历史。返回状态字符串供 main.py 生成摘要"""
    global _blocked_flag, _captcha_flag
    _blocked_flag = False   # 每次运行重置封禁标记
    _captcha_flag = False   # 每次运行重置 CAPTCHA 标记

    history = load_history()
    print(f"\n[🌐] Reddit 海外哨兵启动 | 缓存: {len(history)} 条")

    new_batch = await fetch_reddit_posts(catch_up_mode=False)

    if not new_batch:
        print("[SKIP] Reddit 无新动态，结束巡逻。")
        if _captcha_flag:
            return "captcha_blocked"
        return "blocked_403" if _blocked_flag else "ok"

    print(f"[⚡] 正在并发处理 {len(new_batch)} 篇海外推文 (AI 3并发)...")
    sem = asyncio.Semaphore(3)
    tasks = [
        async_process_post(p, i, len(new_batch), sem)
        for i, p in enumerate(new_batch, 1)
    ]
    results = await asyncio.gather(*tasks)

    # 更新历史记录
    new_ts_map = {}
    for p in new_batch:
        new_ts_map[p["comment_id"]] = {
            "timestamp": p["post_time_ms"],
            "title_prefix": p.get("title_prefix", "")
        }
    for rid in results:
        if rid and isinstance(rid, str) and rid in new_ts_map:
            history[rid] = new_ts_map[rid]
    save_history(history)
    print(f"\n[🎉] Reddit 增量抓取完成！")
    if _captcha_flag:
        return "captcha_blocked"
    return "blocked_403" if _blocked_flag else "ok"


async def catch_up_pipeline():
    """
    追补管线：抓取最近 45 天内遗漏的帖子并写入飞书。
    """
    history = load_history()
    print(f"\n[🏃] Reddit 追补模式启动 | 现有缓存: {len(history)} 条")

    new_batch = await fetch_reddit_posts(catch_up_mode=True, catch_up_days=45)

    if not new_batch:
        print("[SKIP] Reddit 无需追补，无新帖。")
        return

    print(f"\n[⚡] 追补处理 {len(new_batch)} 篇...")
    sem = asyncio.Semaphore(3)
    tasks = [
        async_process_post(p, i, len(new_batch), sem)
        for i, p in enumerate(new_batch, 1)
    ]
    results = await asyncio.gather(*tasks)

    # 更新历史记录
    new_ts_map = {}
    for p in new_batch:
        new_ts_map[p["comment_id"]] = {
            "timestamp": p["post_time_ms"],
            "title_prefix": p.get("title_prefix", "")
        }
    for rid in results:
        if rid and isinstance(rid, str) and rid in new_ts_map:
            history[rid] = new_ts_map[rid]
    save_history(history)
    print(f"\n[🎉] Reddit 追补完成！新入库 {len([r for r in results if r])} 篇。")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Reddit 爬虫")
    parser.add_argument(
        "--catch-up", action="store_true",
        help="追补模式：抓取最近 45 天内遗漏的帖子"
    )
    args = parser.parse_args()

    try:
        if args.catch_up:
            asyncio.run(catch_up_pipeline())
        else:
            asyncio.run(main_pipeline())
    except KeyboardInterrupt:
        print("\n[STOP] 用户强制停止。")
