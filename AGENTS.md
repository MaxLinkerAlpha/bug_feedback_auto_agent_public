# 🤖 AGENTS.md - Game Feedback AI Agent

> **项目语言**: Python 3.x | **主要自然语言**: 中文 | **运行平台**: Windows
> 
> **文档目的**: 为 AI Coding Agent 提供项目全景视图，确保代码生成、调试和重构任务符合项目架构与规范。

---

## 📖 项目概述

本项目是一套**全自动化游戏社区舆情采集与智能分析流水线**（代号：Command Center）。通过底层无头浏览器、接口监听与消息中心接口覆盖七大玩家社区（Steam、Reddit、Discord、腾讯频道、百度贴吧、小黑盒、bilibili），利用大语言模型（LLM）对单条反馈进行智能诊断与 P0-P3 分级，并自带代理自动检测，实现"抓取-提炼-入库"的真正无人值守闭环。

### 核心架构特点

| 特性 | 说明 |
|------|------|
| **多战区全域覆盖** | Steam 社区、Reddit、Discord 论坛、腾讯频道、百度贴吧、小黑盒、bilibili 七路并进 |
| **微观 AI 分析** | 单条反馈十一类分类（v3.2）+ P0-P3 分级 + 致命故障硬校验 |
| **工业级鲁棒性** | 异常隔离防雪崩、本地物理缓存、智能代理探测与切换、渠道失败飞书告警 |
| **快慢双模型路由** | 统一主力 glm-4-flash（智谱），失败时降级 glm-4-flashx → glm-4-air |

---

## 📂 项目结构

```text
.
├── __pycache__/                    # Python 编译缓存
├── playwright_tencent_data/        # Playwright 浏览器持久化数据（腾讯频道登录态）
├── playwright_xiaoheihe_data/      # Playwright 浏览器持久化数据（小黑盒设备指纹）
│
├── .env                            # ⚠️ 核心配置文件：密钥、Token、代理、各平台参数
├── bilibili_cookies.json           # ⚠️ B站官方号 Cookie（浏览器导出插件格式，gitignore，手动更新）
│
├── base_utils.py                   # 🏗️ 核心基建模块：AI 调度、飞书 API、字段映射、降噪清洗
├── console_utils.py                # 🎨 日志分级模块：DEBUG/INFO/WARN/ERROR 双路输出
├── main.py                         # 🎯 中央司令部：并行调度七大战区，异常隔离入口
│
├── spider_steam.py                 # Steam 社区爬虫（公告区 + 讨论区双板块）
├── spider_reddit.py                # Reddit 爬虫（.json API 双层下潜，递归解析嵌套评论）
├── spider_discord.py               # Discord 爬虫（会话凭据接口调用，Forum Thread 解析）
├── spider_tencent.py               # 腾讯频道爬虫（Playwright 无头浏览器，Vue 动态渲染处理）
├── spider_tieba.py                 # 百度贴吧爬虫（Playwright 持久化登录态）
├── spider_xiaoheihe.py             # 小黑盒爬虫（Playwright 复用页面请求）
├── spider_bilibili.py              # B站爬虫（消息中心 msgfeed API + WBI 签名）
│
├── run_spider.bat                  # 🤖 定时任务脚本：后台静默运行，日志写入 daily_log.txt
│
│
├── discord_history.json            # 各平台本地历史记忆库（去重脱水用，自动生成）
├── reddit_history.json             # ↑
├── steam_history.json              # ↑
├── tencent_history.json            # ↑
├── tieba_history.json              # ↑
├── xiaoheihe_history.json          # ↑（值含 title_prefix 供触墙验证）
├── bilibili_history.json           # ↑
│
├── daily_log.txt                   # 定时任务运行日志（run_spider.bat 输出）
├── README.md                       # 面向人类用户的项目说明
└── AI_CODING_GUIDELINES_v1_5.md    # 本项目的 AI 代码生成准则（独立文档）
```

---

## 🛠️ 技术栈详解

### 核心依赖

```bash
pip install openai requests beautifulsoup4 playwright python-dotenv urllib3
playwright install chromium
```

| 依赖库 | 用途 |
|--------|------|
| `openai` | 调用 SiliconFlow/DeepSeek 等大模型 API |
| `requests` | HTTP 请求（Reddit、Discord、Steam 爬取） |
| `beautifulsoup4` | HTML 解析（Steam 评论提取） |
| `playwright` | 腾讯频道动态渲染页面爬取 |
| `python-dotenv` | `.env` 配置文件加载 |
| `urllib3` | 连接池与重试策略 |

### 外部工具

- **Windows 任务计划程序**: 用于无人值守定时触发 `run_spider.bat`

---

## ⚙️ 配置系统 (.env)

项目所有敏感配置与可变参数均集中管理于 `.env` 文件，通过 `python-dotenv` 加载。

### 配置分层

```env
# ================= AI 与 飞书基建 =================
AI_API_KEY="sk-..."
AI_BASE_URL="https://api.siliconflow.cn/v1"
AI_MODEL_CHEAP="Qwen/Qwen2.5-7B-Instruct"      # 廉价极速模型
AI_MODEL_HEAVY="deepseek-ai/DeepSeek-V2.5"     # 重型长上下文模型

FEISHU_APP_ID="cli_..."
FEISHU_APP_SECRET="..."
FEISHU_APP_TOKEN="..."                          # wiki 解析后的 obj_token（base token）
FEISHU_TABLE_ID="..."                          # 总表 ID（四表合并后统一写入目标）

# ================= 网络穿透配置 =================
PROXY_URL="http://127.0.0.1:7890"              # 你的本地代理端口

# ================= Steam 配置 =================
STEAM_APP_ID="your_steam_app_id"
STEAM_MAX_ANNOUNCEMENTS=3
STEAM_MAX_DISCUSSIONS=30

# ================= Reddit 配置 =================
REDDIT_SUBREDDIT="your_subreddit"
REDDIT_FETCH_LIMIT=15
#REDDIT_FEISHU_TABLE_ID="..."                  # 已废弃：四表合并后统一写入总表

# ================= Discord 配置 =================
DISCORD_USER_TOKEN="MTEw..."                   # 浏览器抓包获取
DISCORD_FETCH_LIMIT=15
#DISCORD_FEISHU_TABLE_ID="..."                 # 已废弃：四表合并后统一写入总表

# ================= 腾讯频道配置 =================
TENCENT_CHANNEL_URL="https://pd.qq.com/s/..."
TENCENT_MAX_POSTS=15
HEADLESS_MODE="True"                           # False=有头模式（首次扫码）

# ================= bilibili 配置 =================
# Cookie 由浏览器导出插件保存为 bilibili_cookies.json（已加入 .gitignore），更新 Cookie 直接改该文件
BILI_COOKIES_FILE="bilibili_cookies.json"
BILI_MSGFETCH_LIMIT=20                         # 日常巡逻单次入库上限（补抓模式不受限）

# ================= 小黑盒配置 =================
XIAOHEIHE_USER_ID="your_xiaoheihe_user_id"                   # 官方号 UID
XIAOHEIHE_MAX_POSTS=30                         # 每轮进详情页的动态数上限（列表第一页共 30 条）
XIAOHEIHE_WINDOW_DAYS=30                       # 动态处理窗口（天）：只处理近 N 天发布的动态的评论区
XIAOHEIHE_HEADLESS_MODE="True"

# ================= 百度贴吧配置 =================
# （配置项见 .env 实际文件）
```

---

## 🏗️ 代码架构与模块分工

### 1. base_utils.py - 核心基建

**严禁在业务爬虫中重复实现以下功能，必须统一从此模块导入：**

| 导出项 | 类型 | 说明 |
|--------|------|------|
| `setup_windows_console()` | 函数 | Windows 终端 UTF-8 编码修复（单例模式） |
| `analyze_micro_feedback()` | 函数 | 微观单条分析（智能路由快慢模型） |
| `analyze_macro_summary()` | 函数 | 宏观整体汇总（强制使用重型模型） |
| `write_to_feishu()` | 函数 | 飞书多维表格写入（支持动态分表路由） |
| `build_feishu_record()` | 函数 | 统一的飞书字段映射器 |
| `check_proxy_availability()` | 函数 | TCP 探测代理端口是否存活 |
| `rate_limit()` | 异步函数 | 平台间休眠限速 |
| `trim_history_map()` | 函数 | 历史记录 LRU 裁剪 |
| `IncrementalHistoryManager` | 类 | 增量历史记录管理器（防崩溃回滚） |
| `AI_MODEL_CHEAP` / `AI_MODEL_HEAVY` | 常量 | 模型名称常量 |
| `HISTORY_MAX_SIZE` | 常量 | 历史记录上限（默认 20000） |
| `get_logger()` | 函数 | 日志分级获取（INFO/WARN/ERROR/DEBUG） |

### 2. 爬虫模块规范

四个爬虫模块 (`spider_*.py`) 遵循统一的**管道接口规范**：

```python
async def main_pipeline() -> None:
    """统一入口函数，由 main.py 串行调用"""
    # 1. 加载历史记录
    # 2. 抓取新数据（遇到历史 ID 自动停止）
    # 3. 并发处理微观明细（AI 分析 + 飞书写入）
    # 4. 更新历史记录
```

**爬虫差异对比：**

| 爬虫 | 技术方案 | 认证方式 | 采集策略 |
|------|----------|----------|----------|
| Steam | requests + BeautifulSoup | 无需认证 | 智能代理探测、多端口回退 |
| Reddit | requests + .json API | 无需认证 | User-Agent 轮换、链接物理清洗 |
| Discord | requests + Discord API v9 | User Token | 限流保护（每频道休眠 3 秒） |
| 腾讯频道 | Playwright + Chromium | QQ 扫码 Cookie | 无头模式、JS 注入滚动、DOM 防抖 |
| 百度贴吧 | Playwright + Chromium | 持久化登录态（playwright_tieba_data） | 无头模式、持久化设备指纹 |
| 小黑盒 | Playwright + Chromium（拦截 API 响应） | 无需登录（未登录评论可见） | 评论粒度入库（动态正文不入库）、首页预热获取站点 cookie、统一浏览器上下文（UA / locale / 视口）、show_captcha 重试、详情页间 2~4s 随机等待、仅处理近 XIAOHEIHE_WINDOW_DAYS(30) 天动态 |
| bilibili | requests + msgfeed API（WBI 签名） | 官方号 Cookie（bilibili_cookies.json） | 每轮仅 1~2 请求、nav/msgfeed 间 3~6s 抖动、失败不重试、放任务序列最后执行 |

> **2026-09-05 bilibili 战区上线**：`spider_bilibili.py` 走消息中心「回复我的」msgfeed API（`/x/msgfeed/reply` + WBI 签名），每条回复的 `source_content` 为完整内容（前端看到的截断是 CSS 行为，API 层无截断）。cursor 翻页参数（实测）：`id` + `reply_time`(=cursor.time) + `like_time=0`（传 `id`+`time` 无效，返回重复数据）。支持 `python spider_bilibili.py --catchup [天数]` 补抓模式（翻页至窗口起点，已入库 rpid 自动去重）。Cookie 更新方式：浏览器导出插件重新导出覆盖 `bilibili_cookies.json`。

> **2026-09-05 小黑盒战区上线（同日 v3.0.1 改评论粒度）**：`spider_xiaoheihe.py` 走 Playwright 拦截页面自然请求方案（API 有 hkey+_time+nonce 签名无法复刻）：主页 `events` API 拦截动态列表（30条/页），逐条进详情页拦截 `link/tree` API 拿评论。★入库粒度（用户指定）：**每条玩家评论一条记录，忽略动态正文**（官方自建内容非反馈），动态标题仅作 AI context；楼中楼子评论内容加 `[回复 {主评论者}]` 前缀；comment_id = `c_{commentid}`（c_ 前缀防与旧动态 linkid 历史键碰撞）。评论级去重的优势：老动态来了新评论也能增量抓到。仅处理发布时间在 `XIAOHEIHE_WINDOW_DAYS`(默认30) 天内的动态；`python spider_xiaoheihe.py --catchup [天数]` 补抓模式滚动加载翻页（最多 6 页）。**风控要点**：全新 user_data_dir 首访必须先预热首页建立指纹 cookie，否则 API 返回 `show_captcha`。

> **2026-08-18 Steam 修复**：`fetch_steam_comments()` 新增**楼主（OP）正文提取**。此前只提取回复评论（`.commentthread_comment`），导致楼主正文从未入库、纯 OP 帖（0 回复）整篇丢失。现在 OP 从 `.forum_op > .topic/.content` 结构提取为一条独立反馈记录（comment_id = 主题 id，与回复共用同一 AI 分析 + 飞书写入流程）。详见 `ERROR_SUMMARY.md` 2026-08-18 条目。

> **2026-08-18 Steam 分页枚举**：`get_latest_forum_topics()` 新增 `time_threshold_ms` / `max_pages` 参数，支持 `?fp=N` 列表分页枚举整个时间窗口（用于补抓）。**停页条件**：某页所有帖子的最后活动时间均早于阈值时停止（发帖时间 ≤ 最后活动时间，更深的页面不可能再有窗口内帖子）。注意：列表分页参数是 `?fp=N`，`?ctp=N` 是帖子详情页内评论翻页参数，两者不可混用。

> **2026-08-18 Steam 全量追补**：`_catchup_steam_all.py` 支持三板块（官方公告评论 / 平衡与建议 / Bug与故障排查）× 60 天窗口的全量漏抓补抓，三重去重（comment_id + 裸帖 URL + 内容指纹），支持 `--dry-run` 只统计不写库。本次执行全部成功入库。

### 3. main.py - 中央司令部

- **任务编排**：`missions` 列表定义七个战区（腾讯 / Steam / Reddit / Discord / 百度贴吧 / 小黑盒 / bilibili），通过 `asyncio.gather` **并行执行**（列表顺序即启动顺序，bilibili 官方号 Cookie 风控敏感置尾）
- **异常隔离**：每个战区通过 `run_mission()` 防御性执行，单点崩溃不影响全局
- **失败告警**：渠道失败自动调用 `report_channel_failure()` 写入 🚨 系统告警记录（P0），并生成 `run_summary.txt` 运行摘要

---

## 🔄 运行模式与部署

### 模式 A：手动检阅（开发调试）

```batch
# 直接前台运行，控制台可视化输出
python main.py
```

**流程：**
1. 自动检测代理端口（17890 / 17891 / 4780 → clash 37890 → 直连）
2. 七大战区并行执行
3. 生成 run_summary.txt 运行摘要，失败渠道写入飞书告警

### 模式 B：无人值守（生产部署）

```batch
# 由 Windows 任务计划程序定时触发
run_spider.bat
```

**特点：**
- 所有输出重定向到 `daily_log.txt`
- 10 秒代理握手等待（网络更稳定）
- 适合 Session 0 后台静默运行

### 定时任务配置建议

```powershell
# PowerShell 创建每天 9:00、15:00、21:00 执行的计划任务
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c C:\Path\To\run_spider.bat"
$trigger = New-ScheduledTaskTrigger -Daily -At "09:00"
$trigger2 = New-ScheduledTaskTrigger -Daily -At "15:00"
$trigger3 = New-ScheduledTaskTrigger -Daily -At "21:00"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "GameFeedbackAI_Agent" -Action $action -Trigger $trigger,$trigger2,$trigger3 -Settings $settings
```

---

## 📊 数据流与飞书字段映射

### 飞书数据字典

> **2026-07-28 四表合并 + 字段重命名**：原 2 张精选表 + 2 张现行表已合并为 1 张总表（`<FEISHU_TABLE_ID>`），所有爬虫统一写入总表。
> **2026-07-28 字段精简**：删除"情感倾向"和"人工/AI 备注"字段写入；"反馈来源"重命名为"平台"；砍掉"版本总结"宏观简报功能。
> **2026-07-29 彻底清理**：宏观分析函数（`analyze_macro_summary` / `build_feishu_macro_record` / `generate_macro_report`）已从代码中完全删除；情感倾向相关 AI 提示词已清理；废弃分表变量（`REDDIT_FEISHU_TABLE_ID` / `DISCORD_FEISHU_TABLE_ID`）已从爬虫代码中删除；Discord 频道名统一添加 `[Discord]` 前缀；处理标签默认值改为"未处理"（只能由人工打标）。
> **2026-07-29 分类误判修复**：实测致命Bug 96% 不含致命关键词（AI 倾向于把普通 bug 标为致命Bug）。修复：① Prompt 增加六类分类判定标准（新增"战术讨论"分类，区分 Reddit 战术讨论帖）；② 新增 `FATAL_KEYWORDS` 硬校验（`_validate_result` 中致命Bug 不含致命关键词自动降级为一般Bug）；③ 分类字段选项增加"战术讨论"。
> **2026-09-02 分类体系 v2**：用户在表端重构"分类"字段为新体系（含推送流转规则），代码全面跟进统一：致命Bug→**紧急故障**、一般Bug→**功能异常**、玩法建议→**反馈&建议**、数值吐槽→**数值异常**、战术讨论→**玩家讨论（不处理）**，新增 **UI/美术**；保留纯情绪发泄。Prompt 改为七类判定标准；"紧急故障 硬校验"不通过降级为"功能异常"（移除过宽关键词 "startup"）；仅紧急故障可标 P0，功能异常最高 P2；默认分类改为"反馈&建议"。`build_feishu_record` **停写"AI总结"**（该字段已挂飞书内置 LLM 自动补全，由表端 AI 生成，避免互相覆盖）。
> **2026-09-03 分类体系 v3（最终版，用户指定十类）**：紧急故障 / 纯情绪发泄 / 功能异常 / 反馈&建议 / 其它 / 玩家讨论（不处理） / 描述/本地化问题 / UI/美术 / 一般Bug / 数值吐槽。与 v2 差异：**取消"数值异常"回归"数值吐槽"**；恢复"一般Bug"（判定为不影响核心玩法功能的轻微异常，与"功能异常"=核心玩法功能受损相区分）；新增"其它"（杂项兜底）和"描述/本地化问题"（文案翻译/描述与实际效果不符）。同日已完成全表"分类"为空记录的 AI 回填。
> **2026-09-03 分类体系 v3.1**：表端三个 bug 类选项绑定优先级改名：**紧急故障P0 / 功能异常P1 / 一般BugP2**（名称即优先级）。代码同步：Prompt 更新候选列表并要求 priority 与分类绑定级别一致；`_validate_result` 新增 `bound_priority` 强制纠正（分类与优先级不符自动纠正）+ `category_aliases` 别名归一化（小模型偶发漏掉 P 级后缀，"功能异常"→"功能异常P1"等，避免被误兜底为反馈&建议）；硬校验降级链变为 紧急故障P0→功能异常P1（优先级同步 P1）。
> **2026-09-03 分类体系 v3.2**：**数值吐槽→数值吐槽/平衡类**；新增**性能&体验优化类**（卡顿/掉帧/加载慢/发热/内存占用高，或要求优化性能与操作体验的反馈），共十一类。代码同步：Prompt 候选列表与判定标准更新；`category_aliases` 增加 "数值吐槽"→"数值吐槽/平衡类"、"性能优化"→"性能&体验优化类" 归一化，防止模型沿用旧名被误兜底。

| 字段名称 | 类型 | 说明 | 爬虫是否写入 |
|----------|------|------|------------|
| 平台 | 单选 | [Discord] 频道名 / Reddit (版块名) / Steam (板块名) / 腾讯频道 / [bilibili] 专栏动态 / [bilibili] 视频 / 小黑盒 / 🚨 系统告警 等（Discord 频道加 [Discord] 前缀；bilibili 按 business 类型映射为 "[bilibili] {类型}"，"文章"→"[bilibili] 专栏动态"；未收录类型由飞书自动建选项） | 是 |
| 用户名 | 文本 | 发帖者用户名 | 是 |
| 反馈时间 | 日期 | UTC 毫秒时间戳（格式 yyyy/MM/dd HH:mm） | 是 |
| 链接 | 文本 | 原始 URL（纯文本，非超链接类型） | 是 |
| 未处理的原内容 | 文本 | 800 字截断，保留核心 | 是 |
| AI总结 | 文本 | `1. 2. 3.` 结构化排版 | 否（2026-09-02 起由表端内置 AI 字段生成，爬虫停写） |
| 分类 | 单选 | 2026-09-03 v3.2 十一类体系：紧急故障P0 / 纯情绪发泄 / 功能异常P1 / 反馈&建议 / 其它 / 玩家讨论（不处理） / 描述/本地化问题 / UI/美术 / 一般BugP2 / 数值吐槽/平衡类 / 性能&体验优化类（三个 bug 类名称绑定优先级） | 是 |
| AI 优先级 | 单选 | P0 / P1 / P2 / P3 | 是 |
| 处理标签 | 单选 | 未处理（默认，只能由人工打标）/ 已解决 / 已提报并修复中 / 未复现 / 需跟进 / 未修 / 不处理 / 不确定 / 已知问题 / 待处理 / 机制设计 / 跟进中（共 13 个选项） | 是 |
| 在地址栏输入链接打开图片 | 文本 | 图片/视频等媒体附件的 URL | 是 |
| 联系方式 | 文本 | 玩家联系方式（如 Discord ID） | 否 |
| 附件 | 附件 | 文件附件（图片等） | 否 |
| 备注 | 文本 | 人工备注（爬虫不再自动写入） | 否 |
| 原反馈（翻译版） | 文本 | 外文反馈的中文翻译 | 否 |
| 创建时间 | 创建时间 | 系统自动记录的记录创建时间 | 自动 |
| 收集时间 | 公式 | 基于创建时间的本周/本月/今年判定 | 自动 |
| ID | 自动编号 | 自增 ID | 自动 |

**已删除/停用的字段写入**：
- ~~情感倾向~~：不再写入（`build_feishu_record` 已移除该字段）
- ~~人工/AI 备注~~：不再写入（`remark` 参数保留兼容但不再写入飞书）
- ~~版本总结（宏观简报）~~：功能已停用（`build_feishu_macro_record` 返回 None，4 个爬虫的 `analyze_macro_summary_and_write` / `generate_macro_report` 调用已注释）

### 写入路由规则

> **2026-07-28 更新**：分表路由已废弃，所有平台统一写入总表。

- **总表** (`FEISHU_TABLE_ID` = `<FEISHU_TABLE_ID>`): 所有平台的唯一写入目标
- ~~Reddit 分表~~ (`REDDIT_FEISHU_TABLE_ID`): 已废弃并从代码中删除，write_to_feishu 传 None 自动回退到总表
- ~~Discord 分表~~ (`DISCORD_FEISHU_TABLE_ID`): 已废弃并从代码中删除，write_to_feishu 传 None 自动回退到总表

### 字段重命名映射（2026-07-28）

| 旧字段名 | 新字段名 | 说明 |
|---------|---------|------|
| 反馈来源 | 平台 | 内容不变，仍为渠道来源 |
| 玩家昵称 | 用户名 | - |
| 发帖时间 | 反馈时间 | - |
| 原文链接 | 链接 | - |
| 原始反馈内容 | 未处理的原内容 | - |
| AI 总结与提炼 | AI总结 | - |
| 优先级 | AI 优先级 | - |
| 媒体与附件链接 | 在地址栏输入链接打开图片 | - |

### 数据回填（2026-07-28 一次性操作）

通过 `backfill_fields.py` v2 脚本，基于"未处理的原内容"前 80 字符（去空白）匹配 4 个源表，将历史数据的"平台/分类/处理标签"字段回填至总表：

**v2 核心改进**（v1 因匹配顺序错误导致平台字段 99% 为空）：
- 现行表1/2 优先匹配（"反馈来源"字段完整），精选表1/2 兜底（"来源"字段为空但有分类/标签）
- 多源合并：同一条记录在多个源表中时，优先保留非空字段值
- URL 推断平台：对"来源"为空的精选表记录，从总表"链接"字段推断平台（discord.com→Discord 等）

**回填结果**：
- 匹配成功率：99.5%
- URL 推断平台：少量记录
- 更新：全部成功，失败 0 条
- 平台字段为空率：99% → 42%（改善 57%）
- 分类字段为空率：92% → 43%（改善 49%）
- 处理标签为空率：97% → 42%（改善 55%）

**剩余 42% 为空记录**：主要是精选表中"来源"字段为空且"链接"无法推断平台的记录，需人工补充。

匹配键：`normalize_key(text)` = 去除所有空白后取前 80 字符
源表字段映射：精选表1/2 用"来源(空)/分类/状态+Bug用"，现行表1/2 用"反馈来源/分类/处理标签"

---

## 🛡️ 可靠性与安全设计

### 历史记录机制

- **存储格式**: JSON 文件（`{comment_id: timestamp}` 映射）
- **去重策略**: 以 `comment_id` 为唯一键，碰历史即停（节省 API Token）
- **自动裁剪**: 超过 `HISTORY_MAX_SIZE` (默认 20000) 时 LRU 淘汰
- **断点续传**: 意外中断后，已处理 ID 不会重复抓取

### 代理高可用

项目支持**智能代理回退机制**，自动检测并选择可用的代理方式：

#### 代理检测优先级

| 优先级 | 代理来源 | 端口 | 协议 | 说明 |
|--------|---------|------|------|------|
| 1 | 代理软件 / 用户代理 | 17890, 17891, 4780 | HTTP | 优先检测，延迟低 |
| 2 | clash.exe (自带) | 37890 | SOCKS5 | 用户代理未开启时启用 |
| 3 | 直连 | - | - | 最后手段 |

#### 从 代理软件 获取配置

如果使用 代理软件 代理软件，可以复用其完整配置：

1. **配置文件路径**：
   ```
   <你的代理配置路径>
   ```

2. **复制并适配**：
   ```bash
   # 复制到项目根目录
   copy "<你的代理配置路径>" config.yaml

   # 修改端口（代理软件 原端口 17891 会与自身冲突，改为 37890）
   # 将 config.yaml 中的 mixed-port: 17891 改为 mixed-port: 37890
   ```

3. **配置内容**：
   - 包含 **70+ SSR 节点**，覆盖新加坡、美国、日本、韩国、香港、台湾等
   - 内置 Steam 社区分流规则：`DOMAIN-SUFFIX,steamcommunity.com,Proxy`

#### 代码示例

```python
# base_utils.py 中的智能代理选择
def select_best_proxy() -> str:
    # 检测用户代理软件 - 尝试多个常用端口
    for port in [17890, 17891, 4780]:
        proxy_url = f"http://127.0.0.1:{port}"
        if check_proxy_availability(proxy_url):
            return proxy_url

    # 检测自带 clash.exe (37890)
    if check_proxy_availability("socks5://127.0.0.1:37890"):
        return "socks5://127.0.0.1:37890"

    return None  # 最后手段：直连
```

### 异常处理规范

- **禁止裸 `except:`**: 必须捕获具体异常类型
- **防御性编程**: 函数入口校验参数，非法即抛异常（Fail Fast）
- **任务级隔离**: 单个平台崩溃通过 `try/except` 捕获，返回 False 标记失败，不影响其他平台

### 密钥安全

- **零硬编码**: 所有密钥、Token 必须来自 `.env`
- **环境隔离**: `.env` 已加入 `.gitignore`（如使用 Git）
- **缺失阻断**: `_get_required_env()` 在启动期即阻断，防止空值裸奔

---

## 📝 开发规范与约束

### 文件编码红线

- **绝对 UTF-8**: 所有 Python 文件头必须包含 `# -*- coding: utf-8 -*-`
- **IO 强制编码**: `open(file, 'r', encoding='utf-8')`
- **Windows 控制台**: 通过 `setup_windows_console()` 统一修复，禁止要求用户修改注册表

### 命名规范

| 场景 | 规范 | 示例 |
|------|------|------|
| 模块/文件 | snake_case | `spider_steam.py`, `base_utils.py` |
| 函数/变量 | snake_case | `fetch_reddit_posts()`, `history_map` |
| 常量 | UPPER_SNAKE_CASE | `AI_MODEL_HEAVY`, `HISTORY_MAX_SIZE` |
| 类（如有） | PascalCase | `FeedbackAnalyzer` |

**禁止**: 文件名中使用小数点（如 `base_utils_v1.0.py` ❌，应为 `base_utils_v1_0.py` ✅）

### 并发控制

- **AI 并发上限**: 每个爬虫通过 `asyncio.Semaphore(3)` + `ThreadPoolExecutor(max_workers=3)` 实现 3 并发 AI 调用
- **七平台并行**: `main.py` 使用 `asyncio.gather()` 同时执行七个战区
- **总并发**: 7 平台 × 3 = 21 并发上限（智谱 glm-4-flash 实际远低于此）

---

## 🔧 常见调试场景

### 场景 1：首次运行腾讯频道需要登录

```bash
# 修改 .env
HEADLESS_MODE="False"
# 运行后扫码登录，Cookie 自动保存到 playwright_tencent_data/
# 确认登录成功后改回 HEADLESS_MODE="True"
```

### 场景 2：Reddit 返回 HTML 而非 JSON

- **症状**: `JSONDecodeError` 或解析警告
- **原因**: 代理节点被 Reddit 识别屏蔽
- **解决**: 更换 config.yaml 中的代理节点，或调整 `REDDIT_USER_AGENT`

### 场景 3：飞书写入失败

- **检查项**:
  1. `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 是否过期
  2. 子表列名是否与代码一致（注意空格，如 `"AI 总结与提炼"` 不能写成 `"AI总结与提炼"`）
  3. ~~分表 ID 是否正确（`REDDIT_FEISHU_TABLE_ID` 等）~~ 已废弃，所有写入统一走总表 `FEISHU_TABLE_ID`

### 场景 4：Clash 启动后无法连接

- **检查端口占用**: `netstat -ano | findstr 37890`
- **检查配置**: config.yaml 中必须删除 `tun:` 配置段（纯 HTTP 代理模式）
- **验证**: 浏览器手动设置代理 `socks5://127.0.0.1:37890` 测试连通性

---

## 🚀 扩展开发指南

### 添加新平台爬虫

1. **新建文件**: `spider_newplatform.py`
2. **实现管道**: `async def main_pipeline(): ...`
3. **历史记录**: 使用 `load_history()` / `save_history()` 规范
4. **字段映射**: 调用 `build_feishu_record()` 统一组装
5. **注册入口**: 在 `main.py` 中引入并加入 `missions` 列表
6. **配置扩展**: 在 `.env` 添加平台专属配置项

### 修改 AI Prompt

- **微观分析**: 修改 `base_utils.py` 中的 `analyze_micro_feedback()` 函数内的 `prompt` 变量（2026-09-03 已内置十一类分类判定标准：紧急故障P0/纯情绪发泄/功能异常P1/反馈&建议/其它/玩家讨论（不处理）/描述/本地化问题/UI/美术/一般BugP2/数值吐槽/平衡类/性能&体验优化类；priority 必须与分类绑定级别一致）
- **分类硬校验**: 修改 `FATAL_KEYWORDS` 常量（紧急故障P0 硬校验关键词，内容不含则自动降级为功能异常P1）
- **Prompt 红线**: 必须要求返回严格 JSON，且 `ai_summary` 使用 `1. 2. 3.` 换行排版

### 新增飞书字段

1. 修改 `build_feishu_record()` 添加新字段
2. 在飞书多维表格中手动添加对应列（名称必须完全匹配）
3. 更新本 AGENTS.md 中的数据字典

---

## 📚 相关文档索引

| 文档 | 用途 |
|------|------|
| `README.md` | 面向新用户的快速上手指南 |
| `AI_CODING_GUIDELINES_v1_5.md` | AI 代码生成的详细准则（零号法则、黄金法则等） |
| `ERROR_SUMMARY.md` | 项目错误总结与经验教训（每次BUG修复后必须更新） |
| `.env` | 运行时配置（敏感信息） |
| `config.yaml` | Clash 代理节点配置 |

---

## 🔴 AI 行为准则：错误总结强制执行

> **适用范围**: 每次修改代码、修复BUG、发现问题后**

### 强制规则

每次发生以下情况时，**必须**更新 `ERROR_SUMMARY.md`：

| 触发条件 | 操作 |
|---------|------|
| 修复了一个BUG | 追加错误记录到 `ERROR_SUMMARY.md` |
| 发现新问题 | 追加问题记录到 `ERROR_SUMMARY.md` |
| 代码重构 | 检查是否影响既有错误记录 |
| 上线后发现回归 | 新增回归记录，更新预防措施 |

### 错误记录模板

```markdown
### [日期]: [问题简述]

**问题现象**: ...

**根因分析**:
1. ...
2. ...

**解决方案**:
...

**预防措施**:
- ...
```

### 预防措施检查清单

- [ ] 是否检查了同名函数/变量冲突？
- [ ] 是否检查了导入来源唯一性？
- [ ] 是否在代码注释中说明了数据格式？
- [ ] 是否使用了 `FlushingStreamHandler` 确保日志刷新？
- [ ] 是否删除了旧代码而非只添加新代码？

---

## ⚠️ 已知限制与注意事项

1. **Windows 专用**: 批处理脚本 (`*.bat`) 和 `setup_windows_console()` 均为 Windows 设计，跨平台需重写启动脚本
2. **Discord Token 有效期**: User Token 可能因密码修改或 Discord 安全策略失效，需定期更新 `.env`
3. **Playwright 首次初始化**: 腾讯频道首次运行必须 `HEADLESS_MODE=False` 完成扫码（不过实际上不登录也能正常运行）
4. **AI API 费用**: 重型模型（DeepSeek-V2.5）费用较高，大量数据时请关注账单
5. **历史记录非加密**: `*_history.json` 为明文存储，敏感场景需额外加密
6. **B站 Cookie 有效期**: 官方号 Cookie 失效时爬虫返回状态 `bili_cookie_expired`（并触发飞书告警），需用浏览器导出插件重新导出覆盖 `bilibili_cookies.json`（该文件在 .gitignore 中，更新 Cookie 直接改 JSON 文件，不经过 .env）
7. **小黑盒风控**: 首次运行/长期未运行后 API 可能返回 `show_captcha`，代码已内置首页预热 + 浏览器上下文统一 + 重试；若仍失败等下一轮定时任务，勿高频重试
8. **国内站直连**: 腾讯/贴吧/小黑盒/B站四个国内平台不走海外代理（bilibili/小黑盒代码中完全不含代理逻辑）；`base_utils` 导入时打印的"检测到用户代理软件"WARN 是全局日志，不代表该爬虫在用代理

---

> **最后更新**: 2026-09-05 | **维护者**: AI Agent on Kimi Code CLI
