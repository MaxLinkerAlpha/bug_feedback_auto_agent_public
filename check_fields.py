#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import requests
import os
from dotenv import load_dotenv
load_dotenv(override=True)

app_id = os.getenv("FEISHU_APP_ID")
app_secret = os.getenv("FEISHU_APP_SECRET")
token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
res = requests.post(token_url, json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
token = res.json().get("tenant_access_token")
print("Token:", token[:20] + "...")

table_id = os.getenv("FEISHU_TABLE_ID")
app_token = os.getenv("FEISHU_APP_TOKEN")
fields_url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/fields"
headers = {"Authorization": f"Bearer {token}"}
res = requests.get(fields_url, headers=headers, timeout=10)
data = res.json()
print("Code:", data.get("code"))
if data.get("code") == 0:
    fields = data.get("data", {}).get("items", [])
    print(f"字段数量: {len(fields)}")
    for f in fields:
        print(f"  - {f.get('field_name')} (type={f.get('type')})")
else:
    print("Error:", data.get("msg"))
