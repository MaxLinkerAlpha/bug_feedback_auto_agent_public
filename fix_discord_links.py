# -*- coding: utf-8 -*-
"""
临时脚本：修复飞书多维表格中 Discord 链接格式
- 错误格式: https://discord.com/channels/<DISCORD_GUILD_ID>/<CHANNEL_ID>/threads/<POST_ID>
- 正确格式: https://discord.com/channels/<DISCORD_GUILD_ID>/<POST_ID>

运行方式: python fix_discord_links.py
"""
import sys
import os
import re
import requests
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

def get_required_env(key: str) -> str:
    """获取必需的环境变量，缺失则抛异常"""
    value = os.getenv(key)
    if not value:
        raise ValueError(f"环境变量 {key} 未设置或为空")
    return value

# 飞书配置
FEISHU_APP_ID = get_required_env("FEISHU_APP_ID")
FEISHU_APP_SECRET = get_required_env("FEISHU_APP_SECRET")
FEISHU_APP_TOKEN = get_required_env("FEISHU_APP_TOKEN")
FEISHU_TABLE_ID = get_required_env("FEISHU_TABLE_ID")

# Discord 分表（如果有的话）
DISCORD_FEISHU_TABLE_ID = os.getenv("DISCORD_FEISHU_TABLE_ID", "")

def get_feishu_token() -> str:
    """获取飞书 tenant_access_token"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": FEISHU_APP_ID,
        "app_secret": FEISHU_APP_SECRET
    }
    headers = {"Content-Type": "application/json"}
    
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    data = response.json()
    
    if data.get("code") != 0:
        raise Exception(f"获取飞书 Token 失败: {data.get('msg')}")
    
    return data.get("tenant_access_token", "")

def fetch_all_records(token: str, table_id: str) -> list:
    """分页获取表格所有记录"""
    all_records = []
    page_token = None
    
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{table_id}/records"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token
        
        response = requests.get(url, headers=headers, params=params, timeout=30)
        data = response.json()
        
        if data.get("code") != 0:
            print(f"[ERR] 获取记录失败: {data.get('msg')}")
            break
        
        items = data.get("data", {}).get("items", [])
        all_records.extend(items)
        print(f"[INFO] 已获取 {len(all_records)} 条记录...")
        
        # 检查是否还有下一页
        has_more = data.get("data", {}).get("has_more", False)
        if not has_more:
            break
        
        page_token = data.get("data", {}).get("page_token")
        if not page_token:
            break
    
    return all_records

def fix_discord_url(url: str) -> str:
    """
    修复 Discord 链接格式
    错误: https://discord.com/channels/<DISCORD_GUILD_ID>/<CHANNEL_ID>/threads/<POST_ID>
    正确: https://discord.com/channels/<DISCORD_GUILD_ID>/<POST_ID>
    """
    if not url or "discord.com/channels" not in url:
        return url
    
    # 匹配 Discord 链接模式
    # 格式: https://discord.com/channels/{guild_id}/{channel_id}/{thread_id}
    # 或: https://discord.com/channels/{guild_id}/{channel_id}/threads/{thread_id}
    pattern = r'https://discord\.com/channels/(\d+)/(\d+)/(?:threads/)?(\d+)'
    match = re.match(pattern, url)
    
    if match:
        guild_id = match.group(1)
        # channel_id = match.group(2)  # 不需要
        thread_id = match.group(3)
        # 正确格式: https://discord.com/channels/{guild_id}/{thread_id}
        return f"https://discord.com/channels/{guild_id}/{thread_id}"
    
    return url

def update_record(token: str, table_id: str, record_id: str, new_url: str) -> bool:
    """更新单条记录的链接字段"""
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{table_id}/records/{record_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "fields": {
            "原文链接": {"link": new_url, "text": "跳转"}
        }
    }
    
    try:
        response = requests.put(url, headers=headers, json=payload, timeout=15)
        data = response.json()
        return data.get("code") == 0
    except Exception as e:
        print(f"[ERR] 更新记录 {record_id} 失败: {e}")
        return False

def process_table(table_id: str, table_name: str):
    """处理单个表格"""
    print(f"\n{'='*50}")
    print(f"[INFO] 开始处理表格: {table_name}")
    print(f"{'='*50}")
    
    token = get_feishu_token()
    if not token:
        print("[ERR] 获取飞书 Token 失败")
        return
    
    # 获取所有记录
    print("[INFO] 正在获取所有记录...")
    records = fetch_all_records(token, table_id)
    print(f"[INFO] 共获取 {len(records)} 条记录")
    
    # 筛选 Discord 来源的记录
    discord_records = []
    for rec in records:
        fields = rec.get("fields", {})
        source = fields.get("反馈来源", "")
        
        # 检查是否是 Discord 来源
        if source and "Discord" in str(source):
            link_field = fields.get("原文链接", {})
            url = link_field.get("link", "") if isinstance(link_field, dict) else ""
            if url:
                discord_records.append({
                    "record_id": rec.get("record_id"),
                    "url": url,
                    "fields": fields
                })
    
    print(f"[INFO] 找到 {len(discord_records)} 条 Discord 记录")
    
    if not discord_records:
        print("[INFO] 没有需要处理的 Discord 记录")
        return
    
    # 检查并修复链接
    fixed_count = 0
    for rec in discord_records:
        old_url = rec["url"]
        new_url = fix_discord_url(old_url)
        
        if old_url != new_url:
            print(f"\n[INFO] 修复链接:")
            print(f"  旧: {old_url}")
            print(f"  新: {new_url}")
            
            if update_record(token, table_id, rec["record_id"], new_url):
                print(f"  [OK] 更新成功")
                fixed_count += 1
            else:
                print(f"  [ERR] 更新失败")
    
    print(f"\n[INFO] 表格 {table_name} 处理完成，共修复 {fixed_count} 条记录")

def main():
    print("="*60)
    print("Discord 链接修复工具")
    print("="*60)
    
    # 处理主表
    process_table(FEISHU_TABLE_ID, "主表")
    
    # 如果有 Discord 分表，也处理
    if DISCORD_FEISHU_TABLE_ID:
        process_table(DISCORD_FEISHU_TABLE_ID, "Discord 分表")
    
    print("\n" + "="*60)
    print("[OK] 所有表格处理完成")
    print("="*60)

if __name__ == "__main__":
    main()
