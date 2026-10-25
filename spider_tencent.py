#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
import time
import asyncio
import sys
from typing import Dict, Any, List
from playwright.async_api import async_playwright, Page

# 引入强大的通用基建 (注意：文件名已去除版本号)
from base_utils import (
    setup_windows_console, 
    analyze_micro_feedback, 
    write_to_feishu,
    build_feishu_record,
    AI_MODEL_CHEAP,
    AI_MODEL_HEAVY,
    trim_history_map,
    HISTORY_MAX_SIZE
)

setup_windows_console()

# ================= 配置读取 =================
TENCENT_CHANNEL_URL = os.getenv("TENCENT_CHANNEL_URL")
TENCENT_MAX_POSTS = int(os.getenv("TENCENT_MAX_POSTS", 3))
HEADLESS_MODE = os.getenv("HEADLESS_MODE", "False").lower() == "true"
HISTORY_FILE = "tencent_history.json" 
# ==========================================

def load_history() -> dict:
    if not os.path.exists(HISTORY_FILE): return {}
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f: 
            data = json.load(f)
        if isinstance(data, dict):
            result = {}
            for k, v in data.items():
                if isinstance(v, dict):
                    result[str(k)] = {"timestamp": int(v.get("timestamp", 0)), "title_prefix": v.get("title_prefix", "")}
                else:
                    result[str(k)] = {"timestamp": int(v) if isinstance(v, (int, float)) else 0, "title_prefix": ""}
            return result
        if isinstance(data, list):
            return {str(i): {"timestamp": 0, "title_prefix": ""} for i in data}
        return {}
    except Exception as e:
        print(f"\n[WARN] 历史记录读取异常: {e}")
        return {}

def save_history(history_map: dict) -> None:
    try:
        trimmed = trim_history_map({k: v["timestamp"] for k, v in history_map.items()}, HISTORY_MAX_SIZE)
        migrated = {}
        for k, ts in trimmed.items():
            if k in history_map:
                migrated[k] = {"timestamp": ts, "title_prefix": history_map[k].get("title_prefix", "")}
            else:
                migrated[k] = {"timestamp": ts, "title_prefix": ""}
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f: 
            json.dump(migrated, f, ensure_ascii=False)
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

# --- 核心工具函数 ---

def decode_timestamp_from_post_id(post_id: str) -> int:
    """
    从帖子 ID 解码精确时间戳。
    腾讯频道帖子 ID 格式如 B_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
    时间戳编码在 ID 的第 3-10 位（8 位 16 进制），需反转字节序。
    """
    if not post_id or not post_id.startswith('B_'):
        return 0
    try:
        hex_str = post_id[2:10]
        if len(hex_str) != 8:
            return 0
        be_hex = hex_str[6:8] + hex_str[4:6] + hex_str[2:4] + hex_str[0:2]
        timestamp = int(be_hex, 16)
        if 1500000000 < timestamp < 2500000000:
            return timestamp * 1000
    except:
        pass
    return 0

# --- Playwright 核心爬取逻辑 ---

async def extract_post_detail(page: Page) -> str:
    """【防御性修复】：只负责在弹窗中提取正文与评论详情，不再提取容易报错的作者"""
    title = ""
    title_loc = page.locator('.modal-overlay-show .long-feed-title').first
    if await title_loc.count() > 0:
        title = f"【{await title_loc.inner_text()}】"
        
    content_nodes = await page.locator('.modal-overlay-show .feed-detail-text, .modal-overlay-show .long-feed-content').all()
    # 防御性读取，避免超时
    content_texts = []
    for n in content_nodes:
        if await n.count() > 0:
            content_texts.append(await n.inner_text())
            
    main_content = f"{title}\n{' '.join(content_texts)}".strip()

    # 提取评论区
    comments = []
    comment_items = await page.locator('.modal-overlay-show .comment-list-item').all()
    for item in comment_items:
        try:
            c_author_loc = item.locator('.comment-list-item__info__title-name').first
            c_text_loc = item.locator('.comment-richcontent').first
            
            # 使用 count() > 0 判断元素是否存在，防止 inner_text() 抛出 Timeout
            c_author = await c_author_loc.inner_text() if await c_author_loc.count() > 0 else "匿名"
            c_text = await c_text_loc.inner_text() if await c_text_loc.count() > 0 else ""
            
            if c_text:
                comments.append(f"[{c_author.strip()}]: {c_text.strip()}")
        except: continue
        
    replies_text = " ｜ ".join(comments) if comments else "无评论"
    
    return f"{main_content}\n\n【评论区】: {replies_text}"

async def fetch_tencent_posts() -> List[Dict[str, Any]]:
    history = load_history()
    parsed_data = []
    
    if not TENCENT_CHANNEL_URL:
        print("[ERR] 缺少 TENCENT_CHANNEL_URL 环境变量，请在 .env 中配置！")
        return []

    print(f"\n[🚀] 启动 Playwright 无头浏览器...")
    if not HEADLESS_MODE:
        print(f"[⚠️] 注意：当前为有头模式。如果是首次运行，请在弹出的浏览器中手动扫码登录 QQ！")
        
    async with async_playwright() as p:
        user_data_dir = os.path.join(os.getcwd(), "playwright_tencent_data")
        browser = await p.chromium.launch_persistent_context(
            user_data_dir,
            headless=HEADLESS_MODE,
            args=["--disable-blink-features=AutomationControlled"]
        )
        page = await browser.new_page()
        
        try:
            print(f"[🔎] 正在访问腾讯频道: {TENCENT_CHANNEL_URL}")
            await page.goto(TENCENT_CHANNEL_URL, wait_until="networkidle")
            # 1. 切换到"全部"标签页（默认按时间降序，最新在前）
            #    原方案通过 .feeds-arrange 排序按钮切换，但该按钮已被腾讯频道移除
            #    "全部"标签页本身就按发布时间降序排列，无需额外排序操作
            print("[标签] 正在切换至'全部'标签以确保按时间排序...")
            try:
                all_tab = page.locator('.tab-bar__item__text:has-text("全部")').first
                await all_tab.click()
                await page.wait_for_timeout(3000)  # 等待 Vue 渲染列表
                print("[OK] 已切换至'全部'标签（默认按发布时间降序）。")
            except Exception as e:
                print(f"[WARN] 切换'全部'标签失败，将按当前标签顺序抓取: {e}")

            # 2. 停止逻辑提示
            print(f"[提示] 脚本将在抓满 {TENCENT_MAX_POSTS} 篇或触碰历史记录时自动停止。")
            if not HEADLESS_MODE:
                await page.wait_for_timeout(10000) 
            else:
                await page.wait_for_timeout(3000)

            processed_ids = set()
            scroll_attempts = 0
            
            while len(parsed_data) < TENCENT_MAX_POSTS and scroll_attempts < 15:
                # 动态获取当前屏幕内的所有帖子数量
                posts_count = await page.locator('.msgBox').count()
                
                for i in range(posts_count):
                    if len(parsed_data) >= TENCENT_MAX_POSTS: break
                    
                    # 每次循环重新定位，防止弹窗开闭导致的 DOM 刷新失效 (Stale Element)
                    post = page.locator('.msgBox').nth(i)
                    if await post.count() == 0: continue
                    
                    post_id = await post.get_attribute('data-index')
                    if not post_id or post_id in processed_ids: continue
                    processed_ids.add(post_id)
                    
                    author_loc = post.locator('.nick').first
                    post_author = await author_loc.inner_text() if await author_loc.count() > 0 else "未知玩家"

                    # 触碰历史墙，二重验证
                    if post_id in history:
                        title_prefix = post_author[:6] if post_author else ""
                        stored = history[post_id]
                        if stored.get("title_prefix") and title_prefix and stored["title_prefix"] == title_prefix:
                            print(f"  -> 触碰历史边界 [{post_id}]（标题验证通过），停止深入。")
                            return parsed_data
                        elif not stored.get("title_prefix"):
                            print(f"  -> 触碰历史边界 [{post_id}]（旧版无标题），停止深入。")
                            return parsed_data
                        else:
                            print(f"  -> ID命中但标题不符 [{post_id}]，继续。")
                            continue
                    
                    print(f"  [↓] 发现新帖子: {post_author}，正在展开详情...")
                    
                    click_target = post.locator('a').first
                    if await click_target.count() > 0:
                        try:
                            actual_url = await asyncio.wait_for(
                                click_target.get_attribute('href'),
                                timeout=3
                            )
                        except asyncio.TimeoutError:
                            actual_url = f"https://pd.qq.com/s/{post_id}"
                        if not actual_url:
                            actual_url = f"https://pd.qq.com/s/{post_id}"
                        await click_target.evaluate("node => node.click()")
                        
                        try:
                            await page.wait_for_selector('.modal-overlay-show', timeout=5000)
                            await page.wait_for_timeout(1000) 
                            
                            detail_content = await extract_post_detail(page)
                            
                            parsed_data.append({
                                "comment_id": post_id,
                                "author": post_author.strip(),
                                "content": detail_content,
                                "post_time_ms": decode_timestamp_from_post_id(post_id) or int(time.time() * 1000), 
                                "url": actual_url, 
                                "platform": "腾讯频道"
                            })
                            
                        except Exception as e:
                            print(f"  [WARN] 提取帖子 {post_id} 详情失败，可能是卡顿: {e}")
                        
                        # 强力关闭弹窗
                        close_btn = page.locator('.modal-overlay-show .close-icon').first
                        if await close_btn.count() > 0:
                            await close_btn.click()
                        else:
                            await page.keyboard.press("Escape")
                            
                        await page.wait_for_timeout(500)
                
                # ==========================================
                # 【核心修复】：注入原生 JS，精准强制滚动容器
                # ==========================================
                print("  [↕] 自动向下滚动，加载更多帖子...")
                await page.evaluate("""
                    () => {
                        let scroller = document.querySelector('.vue-recycle-scroller');
                        if (!scroller) scroller = document.querySelector('.game-guild-main__waterfalls-scroller');
                        if (scroller) {
                            scroller.scrollTop += 1200;
                        } else {
                            window.scrollBy(0, 1200);
                        }
                    }
                """)
                await page.wait_for_timeout(2000) # 给 Vue 留出渲染新 DOM 的时间
                scroll_attempts += 1
                    
        except Exception as e:
            print(f"[ERR] 爬虫调度异常: {e}")
        finally:
            # 无头模式下腾讯频道页面有长连接(websocket实时推送)，browser.close() 会被阻塞挂起
            # 给关闭操作加超时保护，避免触碰历史边界后整站卡死
            try:
                await asyncio.wait_for(browser.close(), timeout=10)
            except asyncio.TimeoutError:
                print("[WARN] 浏览器关闭超时，强制结束。")
            except Exception as e:
                print(f"[WARN] 浏览器关闭异常: {e}")

    return parsed_data

# ================= 业务流水线 =================

async def async_process_post(post: Dict[str, Any], idx: int, total: int, semaphore: asyncio.Semaphore):
    async with semaphore:
        stop_event = asyncio.Event()
        spinner_task = asyncio.create_task(progress_spinner(idx, total, f"AI 处理 {post['author']}", stop_event))
        loop = asyncio.get_event_loop()
        try:
            # 提取主帖标题作为上下文，帮助AI理解评论背景
            content = post['content']
            context = ""
            # 如果内容包含【】标记的标题部分，提取前200字作为上下文
            if '【' in content and '】' in content:
                title_end = content.find('】')
                context = content[:min(title_end + 50, 200)]  # 提取标题+少量正文
            
            ai_res = await asyncio.wait_for(
                loop.run_in_executor(None, analyze_micro_feedback, content, context),
                timeout=60
            )
            
            # 【完美利用统一基建】：使用 build_feishu_record 组装飞书字段
            fields = build_feishu_record(post, ai_res, remark="Playwright 抓取")

            await loop.run_in_executor(None, write_to_feishu, fields)
            return post["comment_id"]
        except Exception as e:
            print(f"\n[ERR] 处理帖子 {post.get('comment_id')} 时异常: {e}")
            return None
        finally:
            stop_event.set()
            await spinner_task

async def main_pipeline():
    history = load_history()
    print(f"\n[🌐] 腾讯频道巡逻兵 | 缓存: {len(history)} 条")
    
    new_batch = await fetch_tencent_posts()
    
    if not new_batch:
        print("[SKIP] 频道无新动态，结束巡逻。")
        return

    print(f"[⚡] 正在并发诊断 {len(new_batch)} 篇帖子及其评论 (AI双并发)...")
    sem = asyncio.Semaphore(3)
    tasks = [async_process_post(p, i, len(new_batch), sem) for i, p in enumerate(new_batch, 1)]
    results = await asyncio.gather(*tasks)
    
    new_ts_map = {}
    for p in new_batch:
        new_ts_map[p["comment_id"]] = {"timestamp": p["post_time_ms"], "title_prefix": p.get("author", "")[:6]}
    for rid in results:
        if rid and isinstance(rid, str) and rid in new_ts_map:
            history[rid] = new_ts_map[rid]
    save_history(history)
    print(f"\n[🎉] 腾讯频道增量任务已完成！")

if __name__ == "__main__":
    try:
        asyncio.run(main_pipeline())
    except KeyboardInterrupt:
        print("\n[STOP] 用户强制停止。")
