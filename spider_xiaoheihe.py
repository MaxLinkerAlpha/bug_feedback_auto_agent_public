#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spider_xiaoheihe.py - 小黑盒战区：官方号动态评论区爬虫（Playwright 拦截方案）
================================================================================
技术方案（2026-09-05 浏览器探查确认）：
  小黑盒所有数据走 api.xiaoheihe.cn，请求必须带 hkey+_time+nonce 签名（页面 JS 生成，
  含 jsencrypt，无法简单复刻）→ 采用「页面自然发请求 + Playwright 响应拦截」方案，
  不触碰签名算法，与 spider_tencent.py 同一技术栈。

  数据链路：
  1. 打开官方号主页 https://www.xiaoheihe.cn/app/user/profile/{userid}
     → 拦截 GET /bbs/app/profile/events 响应 → result.moments[]（30条/页，时间倒序）
  2. 对每条目标动态 goto 详情页 https://www.xiaoheihe.cn/app/bbs/link/{linkid}
     → 拦截 GET /bbs/app/link/tree 响应 → result.comments[]

  ★ 入库粒度（2026-09-05 用户指定）：每条玩家评论一条记录，忽略动态正文
    （动态是官方自己发的，不重要；玩家的评论才是反馈）。
    - comment_id = "c_{commentid}"（c_ 前缀防与旧动态 linkid 历史键碰撞）
    - 动态标题仅作为 AI 分析的 context 注入（不写入记录内容）
    - 楼中楼子评论内容加 "[回复 {主评论者}]" 前缀保留最小上下文
    - 评论级去重的好处：老动态来了新评论也能增量抓到（动态合并粒度做不到）

  处理范围：仅处理发布时间在 XIAOHEIHE_WINDOW_DAYS（默认30）天内的动态
  （老动态的新评论概率极低，不值得进详情页）。

  关键字段（实测确认，时间戳均为秒级）：
  - 动态: linkid / title / description / create_at / comment_num / imgs[]
  - 评论: result.comments[i].comment[] 为扁平数组（[0]主评论，其余子评论楼中楼）
          commentid(唯一ID) / text(正文) / create_at / up / user.username / ip_location
  - 未登录状态评论完整可见（探查确认，无登录墙）

  运行稳定性：详情页间随机等待 2~4s 降低请求密度；持久化用户数据目录
  (playwright_xiaoheihe_data) 保持会话状态；统一 UA / zh-CN locale / 视口；
  遇 show_captcha 挑战时重试；
  浏览器关闭加 10s 超时保护（腾讯频道同款教训）。
  评论仅抓详情页首屏 20 楼（反馈采集场景足够，不做"加载更多"翻页）。
"""

import os
import json
import time
import random
import asyncio
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, List, Optional

from playwright.async_api import async_playwright, Page, Response

from base_utils import (
    setup_windows_console,
    analyze_micro_feedback,
    write_to_feishu,
    build_feishu_record,
    trim_history_map,
    HISTORY_MAX_SIZE,
)

setup_windows_console()

# ================= 配置读取 =================
XIAOHEIHE_USER_ID = os.getenv("XIAOHEIHE_USER_ID", "")
XIAOHEIHE_MAX_POSTS = int(os.getenv("XIAOHEIHE_MAX_POSTS", 30))  # 每轮进详情页的动态数上限
XIAOHEIHE_WINDOW_DAYS = int(os.getenv("XIAOHEIHE_WINDOW_DAYS", 30))  # 动态处理窗口（天）
XIAOHEIHE_HEADLESS_MODE = os.getenv("XIAOHEIHE_HEADLESS_MODE", "True").lower() == "true"
XIAOHEIHE_PROFILE_URL = os.getenv(
    "XIAOHEIHE_PROFILE_URL",
    f"https://www.xiaoheihe.cn/app/user/profile/{XIAOHEIHE_USER_ID}" if XIAOHEIHE_USER_ID else "")
HISTORY_FILE = "xiaoheihe_history.json"
PLATFORM_NAME = "小黑盒"

# ================= 历史记录 =================
def load_history() -> dict:
    if not os.path.exists(HISTORY_FILE):
        return {}
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            result = {}
            for k, v in data.items():
                if isinstance(v, dict):
                    result[str(k)] = {"timestamp": int(v.get("timestamp", 0)),
                                      "title_prefix": v.get("title_prefix", "")}
                else:
                    result[str(k)] = {"timestamp": int(v) if isinstance(v, (int, float)) else 0,
                                      "title_prefix": ""}
            return result
        return {}
    except Exception as e:
        print(f"[WARN] 历史记录读取异常: {e}")
        return {}


def save_history(history_map: dict) -> None:
    try:
        trimmed = trim_history_map({k: v["timestamp"] for k, v in history_map.items()},
                                   HISTORY_MAX_SIZE)
        migrated = {}
        for k, ts in trimmed.items():
            if k in history_map:
                migrated[k] = {"timestamp": ts,
                               "title_prefix": history_map[k].get("title_prefix", "")}
            else:
                migrated[k] = {"timestamp": ts, "title_prefix": ""}
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(migrated, f, ensure_ascii=False)
    except Exception as e:
        print(f"[WARN] 历史记录保存失败: {e}")


# ================= 响应拦截工具 =================
class ApiSniffer:
    """
    挂在 page 上的响应监听器：捕获 events / link/tree 的 JSON 响应。
    只存 Response 对象引用，body 由主流程按需 await json()（Playwright 会缓存响应体）。
    events_pages 按到达顺序保留所有分页响应（补抓模式滚动加载翻页时逐页追加）。
    """
    def __init__(self):
        self.events_pages: List[Response] = []         # 所有 events 分页（到达顺序）
        self.tree_responses: Dict[str, Response] = {}  # link_id(str) -> Response

    def attach(self, page: Page) -> None:
        def _handler(response: Response):
            try:
                url = response.url
                if "/bbs/app/profile/events" in url:
                    self.events_pages.append(response)
                elif "/bbs/app/link/tree" in url:
                    qs = parse_qs(urlparse(url).query)
                    link_id = (qs.get("link_id") or [""])[0]
                    if link_id:
                        self.tree_responses[link_id] = response
            except Exception:
                pass
        page.on("response", _handler)

    async def wait_events(self, timeout_s: float = 20.0) -> Optional[dict]:
        """等待第一页 events 响应并解析 JSON"""
        return await self._wait_page(0, timeout_s)

    async def wait_more_events(self, known_count: int, timeout_s: float = 20.0) -> Optional[dict]:
        """等待第 known_count+1 页 events 响应（滚动加载翻页），返回该页 JSON"""
        return await self._wait_page(known_count, timeout_s)

    async def _wait_page(self, index: int, timeout_s: float) -> Optional[dict]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if len(self.events_pages) > index:
                try:
                    return await self.events_pages[index].json()
                except Exception:
                    return None
            await asyncio.sleep(0.5)
        return None

    async def wait_tree(self, link_id: str, timeout_s: float = 15.0) -> Optional[dict]:
        """等待指定动态的评论树响应并解析 JSON"""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            resp = self.tree_responses.get(str(link_id))
            if resp is not None:
                try:
                    return await resp.json()
                except Exception:
                    return None
            await asyncio.sleep(0.5)
        return None


# ================= 数据组装 =================
def _extract_comments(tree_data: dict) -> List[Dict[str, Any]]:
    """
    将 link/tree 响应的 comments 提取为结构化评论列表。
    comments[i].comment[] 为扁平数组：[0]=主评论，其余=楼中楼子评论
    （子评论的 reply_to 取主评论者昵称，保留最小上下文）。
    """
    out: List[Dict[str, Any]] = []
    try:
        groups = ((tree_data.get("result") or {}).get("comments")) or []
        for group in groups:
            root_user = ""
            for i, c in enumerate((group.get("comment") or [])):
                text = (c.get("text") or "").strip()
                cid = str(c.get("commentid") or "").strip()
                if not text or not cid:
                    continue
                uname = ((c.get("user") or {}).get("username") or "匿名玩家").strip()
                if i == 0:
                    root_user = uname
                out.append({
                    "commentid": cid,
                    "username": uname,
                    "text": text.replace("\r", " ").replace("\n", " "),
                    "reply_to": root_user if i > 0 else "",
                    "create_at": int(c.get("create_at") or 0),
                })
    except Exception as e:
        print(f"[WARN] 评论解析异常: {e}")
    return out


def _build_comment_posts(moment: Dict[str, Any],
                         comments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将一条动态的评论列表转为多条评论级 post（每条玩家评论一条记录）。
    动态正文不入库（官方自建内容，非玩家反馈）；标题仅留作 AI context。
    """
    linkid = str(moment.get("linkid") or "").strip()
    if not linkid or not comments:
        return []
    title = (moment.get("title") or "").strip()
    base_url = f"https://www.xiaoheihe.cn/app/bbs/link/{linkid}"

    posts: List[Dict[str, Any]] = []
    for c in comments:
        text = c["text"]
        if c["reply_to"]:
            text = f"[回复 {c['reply_to']}] {text}"
        if len(text) > 2000:
            text = text[:2000] + "...(过长截断)"
        posts.append({
            "comment_id": f"c_{c['commentid']}",  # c_ 前缀防与旧动态 linkid 历史键碰撞
            "author": c["username"],
            "content": text,
            "post_time_ms": c["create_at"] * 1000,
            "url": base_url,
            "platform": PLATFORM_NAME,
            "title": title,
        })
    return posts


# ================= Playwright 核心爬取 =================
async def fetch_xiaoheihe_posts(catchup_since_ts: int = 0) -> List[Dict[str, Any]]:
    """
    打开官方号主页与各动态详情页，拦截 API 响应收集玩家评论（评论级入库）。
    动态处理窗口：catchup 模式为 catchup_since_ts（翻页至窗口起点，最多 6 页，
    不受 XIAOHEIHE_MAX_POSTS 限制）；日常模式为近 XIAOHEIHE_WINDOW_DAYS 天
    （只扫列表第一页）。    窗口外动态不进详情页。
    """
    if not XIAOHEIHE_PROFILE_URL:
        raise RuntimeError(
            "缺少 XIAOHEIHE_USER_ID 或 XIAOHEIHE_PROFILE_URL，请在 .env 中配置目标官方号")
    history = load_history()
    parsed_data: List[Dict[str, Any]] = []
    catchup = catchup_since_ts > 0
    window_since_ts = catchup_since_ts if catchup else int(time.time()) - XIAOHEIHE_WINDOW_DAYS * 86400
    max_moments = 200 if catchup else XIAOHEIHE_MAX_POSTS

    print(f"\n[🚀] 启动 Playwright 无头浏览器 (小黑盒 | headless={XIAOHEIHE_HEADLESS_MODE})")

    async with async_playwright() as p:
        user_data_dir = os.path.join(os.getcwd(), "playwright_xiaoheihe_data")
        browser = await p.chromium.launch_persistent_context(
            user_data_dir,
            headless=XIAOHEIHE_HEADLESS_MODE,
            # 浏览器上下文：统一 UA / locale / 视口，保证页面渲染一致
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            locale="zh-CN",
            viewport={"width": 1536, "height": 864},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = await browser.new_page()
        sniffer = ApiSniffer()
        sniffer.attach(page)

        # 初始化脚本：补齐 headless 环境下缺失的浏览器属性，避免页面兼容问题
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = window.chrome || {runtime: {}};"
            "Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});"
        )

        try:
            # --- 步骤 0: 首页预热 —— 建立正常的会话 cookie（全新的 user_data_dir
            #     直接请求接口会返回 show_captcha）---
            print("[🔎] 预热小黑盒首页（建立会话 cookie）...")
            try:
                await page.goto("https://www.xiaoheihe.cn/", wait_until="networkidle",
                                timeout=30000)
            except Exception:
                pass  # networkidle 超时不致命，cookie 通常已下发
            await asyncio.sleep(random.uniform(2, 3))

            # --- 步骤 1: 主页动态列表（含 1 次 captcha 重试）---
            print(f"[🔎] 正在访问小黑盒主页: {XIAOHEIHE_PROFILE_URL}")
            events_data = None
            for attempt in (1, 2):
                await page.goto(XIAOHEIHE_PROFILE_URL, wait_until="domcontentloaded")
                events_data = await sniffer.wait_events(timeout_s=25)
                if events_data and (events_data.get("status") or "") == "ok":
                    break
                if events_data and events_data.get("status") == "show_captcha" and attempt == 1:
                    print("[WARN] 首次请求返回验证码挑战 (show_captcha)，预热重试...")
                    await asyncio.sleep(3)
                    continue
                break
            if not events_data:
                print("[ERR] 未捕获到 events API 响应（页面可能改版或加载失败）")
                return []
            if (events_data.get("status") or "") != "ok":
                print(f"[ERR] events API 返回异常: {str(events_data)[:200]}")
                return []

            moments = ((events_data.get("result") or {}).get("moments")) or []
            print(f"[OK] 动态列表获取成功: {len(moments)} 条 (窗口上限 {max_moments} 条)")

            # --- 补抓模式：滚动加载翻页，直到最旧动态早于窗口起点（最多 6 页）---
            if catchup:
                MAX_SCROLL_PAGES = 6
                empty_rounds = 0
                for page_no in range(2, MAX_SCROLL_PAGES + 1):
                    newest_count = len(sniffer.events_pages)
                    oldest = min((int(m.get("create_at") or 0) for m in moments), default=0)
                    if oldest and oldest < catchup_since_ts:
                        print(f"[OK] 已覆盖窗口起点（最旧动态 "
                              f"{time.strftime('%Y-%m-%d', time.localtime(oldest))}），停止翻页")
                        break
                    print(f"[↓] 滚动加载第 {page_no - 1} 页动态（当前最旧 "
                          f"{time.strftime('%Y-%m-%d', time.localtime(oldest))}）...")
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    more = await sniffer.wait_more_events(newest_count, timeout_s=20)
                    await asyncio.sleep(random.uniform(2, 4))
                    if not more:
                        empty_rounds += 1
                        if empty_rounds >= 2:
                            print("[OK] 连续两次滚动无新数据，判定已到列表底部")
                            break
                        continue
                    empty_rounds = 0
                    if (more.get("status") or "") != "ok":
                        print(f"[WARN] 第 {page_no} 页 events 异常: {str(more)[:120]}")
                        break
                    more_moments = ((more.get("result") or {}).get("moments")) or []
                    if not more_moments:
                        print("[OK] 无更多动态，判定已到列表底部")
                        break
                    moments.extend(more_moments)
                    print(f"[OK] 第 {page_no} 页: +{len(more_moments)} 条 (累计 {len(moments)})")

            # --- 步骤 2: 逐条进详情页抓评论区（评论级去重；只处理窗口内动态） ---
            # 入库粒度 2026-09-05 改为"每条玩家评论一条记录"，忽略动态正文；
            # 触历史墙逻辑废弃（评论去重足够），动态按时间窗口过滤。
            detail_visited = 0
            for idx, moment in enumerate(moments[:max_moments]):
                if detail_visited >= max_moments:
                    break
                linkid = str(moment.get("linkid") or "").strip()
                title = (moment.get("title") or "").strip()
                create_s = int(moment.get("create_at") or 0)

                # 动态时间窗口过滤（日常与补抓一致：只看近 XIAOHEIHE_WINDOW_DAYS 天的动态）
                if create_s and create_s < window_since_ts:
                    continue

                detail_visited += 1
                try:
                    await page.goto(f"https://www.xiaoheihe.cn/app/bbs/link/{linkid}",
                                    wait_until="domcontentloaded")
                    tree_data = await sniffer.wait_tree(linkid, timeout_s=15)
                    if tree_data is None:
                        print(f"  [WARN] 动态 {linkid} 未捕获到评论响应，跳过")
                    else:
                        comments = _extract_comments(tree_data)
                        new_cmts = [c for c in comments
                                    if f"c_{c['commentid']}" not in history]
                        if comments:
                            print(f"  [✓] ({idx + 1}) {title[:20]}... "
                                  f"评论 {len(comments)} 条（新 {len(new_cmts)}）")
                        else:
                            print(f"  [✓] ({idx + 1}) {title[:20]}... 无评论")
                        parsed_data.extend(_build_comment_posts(moment, new_cmts))
                except Exception as e:
                    print(f"  [WARN] 动态 {linkid} 抓取失败: {e}")

                # 降低请求密度：详情页间随机等待
                await asyncio.sleep(random.uniform(2, 4))

        except Exception as e:
            print(f"[ERR] 小黑盒爬虫调度异常: {e}")
        finally:
            # 浏览器关闭超时保护（腾讯频道教训：SPA 长连接会阻塞 close）
            try:
                await asyncio.wait_for(browser.close(), timeout=10)
            except asyncio.TimeoutError:
                print("[WARN] 浏览器关闭超时，强制结束。")
            except Exception as e:
                print(f"[WARN] 浏览器关闭异常: {e}")

    return parsed_data


# ================= 业务流水线 =================
async def async_process_post(post: Dict[str, Any], idx: int, total: int,
                             semaphore: asyncio.Semaphore):
    async with semaphore:
        loop = asyncio.get_event_loop()
        try:
            # 动态标题作为上下文，帮助 AI 理解评论区讨论背景
            context = (post.get("title") or "")[:150]
            ai_res = await asyncio.wait_for(
                loop.run_in_executor(None, analyze_micro_feedback,
                                     post["content"], context),
                timeout=60
            )
            fields = build_feishu_record(post, ai_res, remark="小黑盒 Playwright 抓取")
            await loop.run_in_executor(None, write_to_feishu, fields, None)
            return post["comment_id"]
        except Exception as e:
            print(f"\n[ERR] 处理评论 {post.get('comment_id')} 时异常: {e}")
            return None


async def main_pipeline(catchup: bool = False, catchup_days: int = 30):
    """
    统一入口：由 main.py 调度。入库粒度 = 每条玩家评论一条记录（动态正文不入库）。
    catchup=True 时滚动翻页补抓 catchup_days 天窗口内所有动态的评论区
    （评论 ID 级去重），用于首次接入或断档补漏。
    """
    history = load_history()
    mode_txt = f"补抓模式({catchup_days}天)" if catchup else \
        f"日常巡逻({XIAOHEIHE_WINDOW_DAYS}天窗口)"
    print(f"\n[📦] 小黑盒巡逻兵启动[{mode_txt} | 评论粒度] | 目标: 官方号 {XIAOHEIHE_USER_ID} | 缓存: {len(history)} 条")

    since_ts = int(time.time()) - catchup_days * 86400 if catchup else 0
    new_batch = await fetch_xiaoheihe_posts(catchup_since_ts=since_ts)
    if not new_batch:
        print("[SKIP] 评论区无新玩家评论，结束巡逻。")
        return

    print(f"[⚡] 正在并发诊断 {len(new_batch)} 条玩家评论 (AI 3 并发)...")
    sem = asyncio.Semaphore(3)
    tasks = [async_process_post(p, i, len(new_batch), sem)
             for i, p in enumerate(new_batch, 1)]
    results = await asyncio.gather(*tasks)

    # 仅成功写入的评论记录历史（键 = c_{commentid}，含动态标题前缀供排查）
    for rid in results:
        if rid:
            p = next((x for x in new_batch if x["comment_id"] == rid), None)
            if p:
                history[rid] = {"timestamp": p["post_time_ms"],
                                "title_prefix": (p.get("title") or "")[:6]}
    save_history(history)
    print(f"\n[🎉] 小黑盒增量任务已完成！新增 {sum(1 for r in results if r)} 条玩家评论。")


if __name__ == "__main__":
    import sys
    try:
        days = 30
        if "--catchup" in sys.argv:
            idx = sys.argv.index("--catchup")
            if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit():
                days = int(sys.argv[idx + 1])
        asyncio.run(main_pipeline(catchup="--catchup" in sys.argv, catchup_days=days))
    except KeyboardInterrupt:
        print("\n[STOP] 用户强制停止。")
