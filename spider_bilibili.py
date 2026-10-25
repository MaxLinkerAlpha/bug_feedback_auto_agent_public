#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spider_bilibili.py - bilibili 战区：官方号「消息中心-回复我的」爬虫
====================================================================
技术方案（2026-09-05 探针验证通过）：
  1. Cookie 加载：bilibili_cookies.json（浏览器导出格式，用户手动更新，已加入 .gitignore）
  2. GET /x/web-interface/nav   → 取 WBI key（img_key/sub_key）+ 登录态校验
  3. GET /x/msgfeed/reply（WBI 签名）→ 消息中心「回复我的」列表（cursor 游标，单页 20 条）
  4. 每条 item.item 结构（已实测）：
     - source_id       = 回复 rpid（去重键）
     - subject_id      = 评论区 oid
     - business_id     = 评论区 type（12=文章/专栏）
     - business        = 类型中文名（"文章"）
     - source_content  = 玩家回复全文（与评论区 API 逐字节一致，无截断！
                         前端看到的截断是 CSS max_line=6 行为，API 层完整）
     - target_reply_content / root_reply_content = 楼中楼对话上下文
     - title           = 来源帖子标题（如【测试服】2.0x1 更新）
     - uri             = 原帖链接（opus URL）
     - user.nickname   = 回复者昵称
     - reply_time      = 秒级时间戳（飞书需毫秒）
  5. 请求节流设计（官方号，低频 + 拟人节奏）：
     - 每轮任务仅 1~2 个 API 请求（nav + msgfeed），默认不翻页
     - nav 与 msgfeed 之间随机休眠 3~6 秒
     - 请求带完整浏览器 UA/Referer/Origin
     - 失败不重试，等下一轮定时任务
     - code=-101 → Cookie 过期（状态 "bili_cookie_expired"）
     - code=-352/-412 → 风控（状态 "bili_risk_control"）
平台字段映射：business="文章" → "[bilibili] 专栏动态"（飞书总表选项，
  其他类型动态映射为 f"[bilibili] {business}"，由飞书自动建选项）
历史去重：bilibili_history.json，键 = rpid（消息流无翻墙概念，全量去重）
"""

import os
import json
import time
import random
import asyncio
import hashlib
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple

import requests

from base_utils import (
    setup_windows_console,
    analyze_micro_feedback,
    write_to_feishu,
    build_feishu_record,
    rate_limit,
    trim_history_map,
    HISTORY_MAX_SIZE,
)

setup_windows_console()

# ================= 配置读取 =================
BILI_COOKIES_FILE = os.getenv("BILI_COOKIES_FILE", "bilibili_cookies.json")
BILI_MSGFETCH_LIMIT = int(os.getenv("BILI_MSGFETCH_LIMIT", 20))
HISTORY_FILE = "bilibili_history.json"

# business(内容类型中文名) -> 飞书「平台」字段选项名
# 2026-09-05 用户确认命名；未收录类型自动生成 "[bilibili] {business}"（飞书自动建选项）
PLATFORM_MAP = {
    "文章": "[bilibili] 专栏动态",
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# ================= WBI 签名（公开算法，纯 hashlib） =================
_MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27,
              43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48,
              7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54,
              21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]


def _sign_wbi(params: dict, img_key: str, sub_key: str) -> dict:
    """对请求参数附加 wts + w_rid 签名"""
    mixin = "".join((img_key + sub_key)[i] for i in _MIXIN_TAB)[:32]
    p = dict(params)
    p["wts"] = int(time.time())
    p = {k: "".join(ch for ch in str(v) if ch not in "!'()*")
         for k, v in sorted(p.items())}
    p["w_rid"] = hashlib.md5((urllib.parse.urlencode(p) + mixin).encode()).hexdigest()
    return p


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


# ================= Cookie 加载 =================
def _load_bili_cookies() -> Dict[str, str]:
    """
    从 json 文件加载 bilibili Cookie，兼容两种格式：
    1. 浏览器 Cookie 导出插件格式: [{"name": ..., "value": ..., "domain": ...}, ...]
    2. 简单字典格式: {"SESSDATA": "...", "bili_jct": "..."}
    """
    if not os.path.exists(BILI_COOKIES_FILE):
        return {}
    try:
        with open(BILI_COOKIES_FILE, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        cookies: Dict[str, str] = {}
        if isinstance(raw, list):
            for c in raw:
                name = c.get("name")
                value = c.get("value")
                if name and value is not None and "bilibili.com" in (c.get("domain") or ".bilibili.com"):
                    cookies[name] = str(value)
        elif isinstance(raw, dict):
            cookies = {str(k): str(v) for k, v in raw.items()}
        return cookies
    except Exception as e:
        print(f"[ERR] Cookie 文件解析失败: {e}")
        return {}


# ================= 核心抓取（同步函数，供线程池调用） =================
def _fetch_msgfeed_items(max_pages: int = 1, since_ts: int = 0) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    拉取消息中心「回复我的」。
    max_pages=1 为日常巡逻（仅最新一页 20 条）；补抓模式传大值（如 30）+ since_ts
    （窗口起点秒级时间戳），翻页直到 is_end / 早于窗口 / 页数上限。
    翻页参数（2026-09-05 实测确认）: id + reply_time(=cursor.time) + like_time=0，
    传 id+time 无效（返回重复数据）。
    返回 (错误状态字符串, items 列表)：状态为 None 表示成功。
    状态值: bili_no_cookie / bili_cookie_expired / bili_risk_control / bili_api_error
    """
    cookies = _load_bili_cookies()
    if not cookies or "SESSDATA" not in cookies:
        print("[ERR] 缺少 bilibili Cookie，请将浏览器导出的 Cookie 存入 "
              f"{BILI_COOKIES_FILE}（须含 SESSDATA/bili_jct/buvid3）")
        return "bili_no_cookie", []

    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://message.bilibili.com/",
        "Origin": "https://message.bilibili.com",
    })
    for k, v in cookies.items():
        s.cookies.set(k, v, domain=".bilibili.com")

    # --- 步骤 1: nav 取 WBI key + 登录态校验 ---
    try:
        r = s.get("https://api.bilibili.com/x/web-interface/nav", timeout=30)
        d = r.json()
    except Exception as e:
        print(f"[ERR] nav 请求异常: {e}")
        return "bili_api_error", []

    if d.get("code") in (-101,):
        print("[ERR] bilibili Cookie 已过期（nav -101 未登录）。"
              "请在浏览器重新导出 Cookie 更新 bilibili_cookies.json")
        return "bili_cookie_expired", []
    if d.get("code") in (-352, -412):
        print(f"[ERR] 触发 bilibili 风控（nav code={d.get('code')}），本轮中止")
        return "bili_risk_control", []
    if d.get("code") != 0:
        print(f"[ERR] nav 异常: code={d.get('code')} msg={d.get('message')}")
        return "bili_api_error", []

    data = d.get("data") or {}
    if not data.get("isLogin"):
        print("[ERR] bilibili Cookie 未登录态（isLogin=False），请更新 Cookie")
        return "bili_cookie_expired", []
    print(f"[OK] bilibili 登录态有效 | 账号: {data.get('uname')}")

    wbi = data.get("wbi_img") or {}
    img_key = (wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".")[0]
    sub_key = (wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".")[0]

    # --- 拟人抖动（探针验证过的 3~6s 节奏） ---
    time.sleep(random.uniform(3, 6))

    # --- 步骤 2: msgfeed/reply（支持 cursor 翻页补抓） ---
    all_items: List[Dict[str, Any]] = []
    cursor: Optional[Dict[str, Any]] = None  # None = 第一页
    try:
        for page_no in range(1, max_pages + 1):
            base = {"build": 0, "mobi_app": "web", "platform": "web"}
            if cursor is not None:
                # 翻页参数实测: id + reply_time(=cursor.time) + like_time=0
                base.update({"id": cursor.get("id"), "reply_time": cursor.get("time"),
                             "like_time": 0})
            params = _sign_wbi(base, img_key, sub_key)
            r2 = s.get("https://api.bilibili.com/x/msgfeed/reply", params=params, timeout=30)
            d2 = r2.json()

            if d2.get("code") == -101:
                print("[ERR] bilibili Cookie 已过期（msgfeed -101），请更新 Cookie")
                return "bili_cookie_expired", []
            if d2.get("code") in (-352, -412):
                print(f"[ERR] 触发 bilibili 风控（msgfeed code={d2.get('code')}），本轮中止")
                return "bili_risk_control", []
            if d2.get("code") != 0:
                print(f"[ERR] msgfeed 异常: code={d2.get('code')} msg={d2.get('message')}")
                return "bili_api_error", []

            data2 = d2.get("data") or {}
            items = data2.get("items") or []
            all_items.extend(items)
            cursor = data2.get("cursor") or {}
            is_end = bool(cursor.get("is_end"))

            oldest = min((int(it.get("reply_time") or 0) for it in items), default=0)
            print(f"[OK] msgfeed 第 {page_no}/{max_pages} 页: +{len(items)} 条 "
                  f"(累计 {len(all_items)}) | 本页最旧 "
                  f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(oldest)) if oldest else 'N/A'}"
                  f"{' | is_end' if is_end else ''}")

            # 停止条件：到底 / 已覆盖窗口起点 / 空页
            if is_end or not items:
                break
            if since_ts and oldest and oldest < since_ts:
                break
            if page_no < max_pages:
                time.sleep(random.uniform(4, 7))  # 页间拟人间隔（官方号敏感）
    except Exception as e:
        print(f"[ERR] msgfeed 请求异常: {e}")
        return "bili_api_error", []

    print(f"[OK] 消息中心「回复我的」拉取完成: {len(all_items)} 条")
    return None, all_items


def _parse_item(it: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    将 msgfeed item 解析为统一 post 结构。
    item 结构（实测）: {id, user:{mid,nickname}, item:{...}, counts, is_multi, reply_time}
    内层 item.item 键: subject_id/root_id/source_id/business_id/business/
                       title/source_content/target_reply_content/uri...
    """
    try:
        inner = it.get("item") or {}
        user = it.get("user") or {}
        rpid = str(inner.get("source_id") or "").strip()
        if not rpid:
            return None
        content = (inner.get("source_content") or "").strip()
        if not content:
            return None

        title = (inner.get("title") or "").strip()
        # 组装带上下文的反馈正文（title 作 AI context，不塞正文）
        full_text = content
        target_reply = (inner.get("target_reply_content") or "").strip()
        if target_reply:
            full_text = f"{full_text}\n\n【回复对象】{target_reply}"

        business = (inner.get("business") or "").strip() or "专栏动态"
        platform = PLATFORM_MAP.get(business, f"[bilibili] {business}")

        uri = (inner.get("uri") or "").strip()
        # 评论锚点：#reply{rpid} 对根评论与楼中楼均有效
        if uri:
            uri = f"{uri}#reply{rpid}"

        reply_time_s = int(it.get("reply_time") or 0)
        return {
            "comment_id": rpid,
            "author": (user.get("nickname") or "匿名玩家").strip(),
            "content": full_text,
            "post_time_ms": reply_time_s * 1000,
            "url": uri,
            "platform": platform,
            "title": title,
        }
    except Exception as e:
        print(f"[WARN] msgfeed item 解析失败: {e}")
        return None


# ================= AI 与 写入 =================
async def async_process_post(post: Dict[str, Any], idx: int, total: int,
                             semaphore: asyncio.Semaphore):
    async with semaphore:
        loop = asyncio.get_event_loop()
        try:
            # 来源帖子标题作为上下文（评论针对公告内容的反馈）
            context = (post.get("title") or "")[:150]
            ai_res = await asyncio.wait_for(
                loop.run_in_executor(None, analyze_micro_feedback,
                                     post["content"], context),
                timeout=60
            )
            fields = build_feishu_record(post, ai_res, remark="bilibili msgfeed")
            await loop.run_in_executor(None, write_to_feishu, fields, None)
            return post["comment_id"]
        except Exception as e:
            print(f"\n[ERR] 处理回复 {post.get('comment_id')} 时异常: {e}")
            return None


async def main_pipeline(catchup: bool = False, catchup_days: int = 30):
    """
    统一入口：由 main.py 调度。返回状态字符串供运行摘要显示。
    catchup=True 时翻页补抓 catchup_days 天窗口内的全部回复（AI 逐条入库，
    已入库 rpid 自动去重），用于首次接入或断档补漏。
    """
    history = load_history()
    mode_txt = f"补抓模式({catchup_days}天)" if catchup else "日常巡逻(1页)"
    print(f"\n[bilibili] 消息中心巡逻兵启动[{mode_txt}] | Cookie: {BILI_COOKIES_FILE} | 缓存: {len(history)} 条")

    max_pages = 30 if catchup else 1
    since_ts = int(time.time()) - catchup_days * 86400 if catchup else 0
    status, items = await asyncio.get_event_loop().run_in_executor(
        None, lambda: _fetch_msgfeed_items(max_pages=max_pages, since_ts=since_ts))
    if status:
        return status

    # 解析 + rpid 去重（消息流无"触墙"概念，全量比对历史）
    new_posts: List[Dict[str, Any]] = []
    seen_rpid = set()
    for it in items:
        p = _parse_item(it)
        if not p:
            continue
        if catchup and p["post_time_ms"] < since_ts * 1000:
            continue  # 补抓窗口外
        if p["comment_id"] in history or p["comment_id"] in seen_rpid:
            continue
        seen_rpid.add(p["comment_id"])
        new_posts.append(p)

    if not catchup:
        new_posts = new_posts[:BILI_MSGFETCH_LIMIT]
    if not new_posts:
        print("[SKIP] 消息中心无新回复，巡逻结束。")
        return None

    print(f"[OK] 发现 {len(new_posts)} 条新回复，开始 AI 诊断 (3 并发)...")
    sem = asyncio.Semaphore(3)
    tasks = [async_process_post(p, i, len(new_posts), sem)
             for i, p in enumerate(new_posts, 1)]
    results = await asyncio.gather(*tasks)

    # 历史落盘：仅记录成功写入的（含 title 前缀供排查）
    for rid in results:
        if rid:
            p = next((x for x in new_posts if x["comment_id"] == rid), None)
            if p:
                history[rid] = {"timestamp": p["post_time_ms"],
                                "title_prefix": (p.get("title") or p["author"])[:6]}
    save_history(history)
    print(f"\n[bilibili] 增量抓取完成！新增 {sum(1 for r in results if r)} 条。")
    return None


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
