# -*- coding: utf-8 -*-
"""
Steam 爬虫并发与限流测试脚本

测试目标：
1. 不同 rate_limit 值（0.5s / 0.3s / 0.2s / 0.1s）是否触发限流
2. 并发抓取多个帖子是否触发限流
3. 找到不被限流的最大并发数

运行方式：python test_steam_concurrency.py
"""
import asyncio
import time
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

STEAM_APP_ID = os.getenv("STEAM_APP_ID", "")
TEST_URL = f"https://steamcommunity.com/app/{STEAM_APP_ID}/discussions/1/"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9"
})

def get_topic_urls(limit: int = 5) -> list:
    """获取帖子列表"""
    print(f"[测试] 获取帖子列表...")
    try:
        res = session.get(TEST_URL, timeout=15)
        soup = BeautifulSoup(res.text, 'html.parser')
        links = [l.get('href') for l in soup.select('a.forum_topic_overlay')[:limit] if l.get('href')]
        print(f"[结果] 获取到 {len(links)} 个帖子")
        return links
    except Exception as e:
        print(f"[错误] 获取帖子列表失败: {e}")
        return []

async def fetch_single_page(url: str, page_num: int, delay: float) -> dict:
    """抓取单个页面"""
    start = time.time()
    separator = '&' if '?' in url else '?'
    page_url = f"{url}{separator}ctp={page_num}&replies_per_page=50"

    await asyncio.sleep(delay)

    try:
        res = session.get(page_url, timeout=15)
        elapsed = time.time() - start
        soup = BeautifulSoup(res.text, 'html.parser')
        comments = soup.select('.commentthread_comment, .forum_comment')

        return {
            "url": page_url,
            "status": res.status_code,
            "comments": len(comments),
            "elapsed": elapsed,
            "rate_limited": res.status_code == 429
        }
    except Exception as e:
        return {
            "url": page_url,
            "status": 0,
            "comments": 0,
            "elapsed": time.time() - start,
            "error": str(e)
        }

async def test_rate_limit(delay: float, concurrent: int = 1, pages_per_url: int = 3):
    """
    测试指定 delay 的限流情况
    concurrent: 同时并发多少个帖子
    pages_per_url: 每个帖子抓取多少页
    """
    urls = get_topic_urls(concurrent)
    if not urls:
        return None

    print(f"\n{'='*60}")
    print(f"[测试配置] delay={delay}s, concurrent={concurrent}, pages={pages_per_url}")
    print(f"{'='*60}")

    tasks = []
    for url in urls:
        for page in range(1, pages_per_url + 1):
            tasks.append(fetch_single_page(url, page, delay))

    results = await asyncio.gather(*tasks)

    success = sum(1 for r in results if r.get("status") == 200)
    rate_limited = sum(1 for r in results if r.get("rate_limited"))
    errors = sum(1 for r in results if r.get("error"))
    total_time = max(r["elapsed"] for r in results) if results else 0

    print(f"\n[结果汇总]")
    print(f"  - 成功 (200): {success}/{len(results)}")
    print(f"  - 限流 (429): {rate_limited}/{len(results)}")
    print(f"  - 错误: {errors}/{len(results)}")
    print(f"  - 总耗时: {total_time:.2f}s")

    if rate_limited > 0:
        print(f"  [!] 检测到限流，不建议使用此配置")
    else:
        print(f"  [OK] 未检测到限流")

    return {
        "delay": delay,
        "concurrent": concurrent,
        "success": success,
        "rate_limited": rate_limited,
        "errors": errors,
        "total_time": total_time
    }

async def test_concurrent_posts(min_delay: float = 0.1):
    """测试并发多个帖子的极限"""
    urls = get_topic_urls(10)
    if not urls:
        return

    print(f"\n{'='*60}")
    print(f"[并发测试] 同时抓取 {len(urls)} 个帖子，每页 delay={min_delay}s")
    print(f"{'='*60}")

    start = time.time()
    tasks = [fetch_single_page(url, 1, min_delay) for url in urls]
    results = await asyncio.gather(*tasks)
    total_time = time.time() - start

    success = sum(1 for r in results if r.get("status") == 200)
    rate_limited = sum(1 for r in results if r.get("rate_limited"))

    print(f"\n[并发测试结果]")
    print(f"  - 成功: {success}/{len(results)}")
    print(f"  - 限流: {rate_limited}/{len(results)}")
    print(f"  - 总耗时: {total_time:.2f}s")

    if rate_limited > 0:
        print(f"  [!] {len(urls)} 个并发触发了限流")
    else:
        print(f"  [OK] {len(urls)} 个并发无问题")

    return rate_limited == 0

async def main():
    print("="*60)
    print("Steam 爬虫并发与限流测试")
    print("="*60)

    results = []

    print("\n\n[阶段1] 测试不同 delay 下的单并发表现...")
    for delay in [0.5, 0.3, 0.2, 0.1]:
        r = await test_rate_limit(delay, concurrent=1, pages_per_url=3)
        if r:
            results.append(r)
        await asyncio.sleep(1)

    print("\n\n[阶段2] 测试 delay=0.1s 时不同并发数...")

    for concurrent in [3, 5, 8]:
        await test_rate_limit(0.1, concurrent=concurrent, pages_per_url=2)
        await asyncio.sleep(2)

    print("\n\n[阶段3] 极限并发测试...")
    await test_concurrent_posts(min_delay=0.1)

    print("\n\n" + "="*60)
    print("测试完成")
    print("="*60)

    print("\n[推荐配置]")
    safe = [r for r in results if r["rate_limited"] == 0]
    if safe:
        fastest = min(safe, key=lambda x: x["delay"])
        print(f"  - 最快且安全的 delay: {fastest['delay']}s")
    print(f"  - 建议并发帖子数: 3-5 个")
    print(f"  - 建议每个帖子内部翻页仍保持串行（避免同一帖子快速翻页触发限流）")

if __name__ == "__main__":
    asyncio.run(main())
