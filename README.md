# 🤖 Game Feedback AI Agent: 全域游戏舆情自动化哨兵

<h1 align="center">⚠️ 由于分享需要，部分做了脱敏处理，<br>未经测试，可能存在部分BUG ⚠️</h1>

<h3 align="center">DESENSITIZED FOR SHARING · NOT FULLY TESTED · MAY CONTAIN BUGS</h3>

---

> **当前版本**: v3.0.0 | **最后更新**: 2026-09-05

## 📖 项目背景

在游戏运营过程中，玩家反馈散落在各大社区（Steam、Reddit、Discord、腾讯频道、百度贴吧、小黑盒、bilibili），呈碎片化、多语种且伴随大量情绪噪声。传统的"人工搬运+人工汇总"模式不仅效率低下，且难以做到 24 小时实时响应。

本项目是一套**全自动化、免维护的 AI 舆情采集与智能分析流水线**（代号：Command Center）。通过底层无头浏览器、接口监听与消息中心接口，覆盖**七大玩家社区**，利用大语言模型（LLM）对单条反馈进行智能诊断与 P0-P3 分级，并将结果统一写入飞书多维表格总表，实现"抓取-提炼-入库"的真正无人值守闭环。

---

## ✨ 核心特性

* **七大战区全域覆盖 (Multi-Platform)**：
  * **腾讯频道**：基于 Playwright 的底层 JS 注入，处理重度动态渲染，支持从帖子 ID 解码精确时间戳。
  * **Steam 社区**：自适应爬取**三大板块**（官方公告评论 / 平衡与建议 / Bug与故障排查），支持按模式选择抓取范围，API 失败时自动降级为 Playwright 渲染抓取。**30 天帖子时间过滤**：列表页时间解析 + 详情页 OP 时间二次验证，彻底拦截旧帖。
  * **Reddit**：使用 `requests` + `BeautifulSoup` 直接抓取 **old.reddit.com** 服务端渲染 HTML，按最新排序，提取帖子正文和完整评论，支持翻页直到历史边界自动停止。无代理冲突、无需浏览器。
  * **Discord 论坛**：基于会话凭据的接口调用，监控 **5 个论坛频道**（综合建议、本地化反馈、游戏 Bug、专家反馈、测试服反馈），支持按频道配置时间范围过滤。
  * **百度贴吧**：Playwright 持久化登录态（`playwright_tieba_data/`）抓取官方吧动态与评论。
  * **小黑盒**：Playwright 拦截页面自然 API 请求（复用页面自身请求，无需逆向接口签名），**每条玩家评论一条记录**（动态为官方自建内容不入库，仅标题作 AI 上下文；楼中楼带 `[回复 xxx]` 最小上下文）。评论级去重——老动态来了新评论也能增量抓到。会话建立：首页预热获取站点 cookie + 统一浏览器上下文（UA / locale / 视口）。
  * **bilibili**：消息中心「回复我的」msgfeed API + **WBI 签名**（官方号 Cookie），回复内容 API 层完整无截断（前端看到的截断只是 CSS 行为）。
* **单轨微观 AI 分析 (Micro Analysis)**：
  * **十一类智能分类（v3.2）**：紧急故障P0 / 纯情绪发泄 / 功能异常P1 / 反馈&建议 / 其它 / 玩家讨论（不处理） / 描述/本地化问题 / UI/美术 / 一般BugP2 / 数值吐槽/平衡类 / 性能&体验优化类。三个 bug 类名称直接绑定优先级（P0/P1/P2）。
  * **致命故障硬校验**：AI 判为紧急故障P0 但内容不含致命关键词（崩溃/闪退/坏档等 30+ 词表）时，自动降级为功能异常P1——宁轻勿重。
  * **上下文注入**：将主帖标题/内容作为上下文注入 Prompt，帮助 AI 理解回复的语境背景。
  * **AI总结由表端生成**：爬虫只写"分类/AI 优先级/处理标签"等结构化字段；"AI总结"与"原反馈（翻译版）"由飞书表格内置 LLM 字段自动补全，避免互相覆盖。
  * **模型智能路由与降级**：长文本/复杂内容走重型模型；主模型失败时自动切换备用模型（glm-4-flash → glm-4-flashx → glm-4-air）。
* **工业级自动化与防暴毙设计 (Industrial Robustness)**：
  * **中央司令部 (main.py)**：**七平台并行调度**（`asyncio.gather`），总耗时 ≈ 最慢平台时间；单平台崩溃异常隔离不影响全局；渠道失败自动写入 🚨 系统告警记录（P0）。
  * **补抓模式 (Catch-up)**：bilibili / 小黑盒支持 `python spider_xxx.py --catchup [天数]` 按时间窗口翻页补抓，已入库 ID 自动去重，断档后一键补漏。
  * **本地物理缓存**：独立的 `xxx_history.json` 自动脱水去重（LRU 裁剪，上限 20000），无视断电与意外中断，已处理 ID 不重复抓取。
* **智能字段映射**：飞书写入前自动检查表格字段并缓存（5 分钟），兼容字段变更，模糊匹配空格差异。

---

## 📂 项目结构

```text
.
├── __pycache__/                    # Python 编译缓存
├── playwright_tencent_data/        # Playwright 浏览器持久化数据 (腾讯频道登录态)
├── playwright_tieba_data/          # Playwright 浏览器持久化数据 (百度贴吧登录态)
├── playwright_xiaoheihe_data/      # Playwright 浏览器持久化数据 (小黑盒设备指纹)
│
├── .gitignore                      # Git 忽略规则（.env / Cookies / 浏览器数据 / 日志 / 历史缓存）
├── .env                            # 全局环境与密钥配置文件 (务必保密，已 gitignore)
├── .env.example                    # .env 模板文件（可安全提交，供新用户参考）
├── bilibili_cookies.json           # ⚠️ B站官方号 Cookie（浏览器导出插件格式，已 gitignore，手动更新）
│
├── base_utils.py                   # 核心基建：AI 调度(含模型降级)、分类硬校验、飞书映射(含动态字段缓存)、控制台修复
├── console_utils.py                # 日志分级模块：DEBUG（仅文件）/ INFO / WARN / ERROR 双路输出 + FlushingStreamHandler
├── main.py                         # 中央司令部：七平台并行调度所有爬虫管线 + 失败飞书告警
│
├── spider_tencent.py               # 腾讯频道爬虫 (Playwright 无头浏览器，JS 注入滚动，帖子ID时间戳解码)
├── spider_steam.py                 # Steam 三大板块爬虫 (公告/平衡建议/Bug排查) + Playwright 兜底
├── spider_reddit.py                # Reddit 爬虫 (requests+BS4 抓取 old.reddit.com，提取正文+评论)
├── spider_discord.py               # Discord 爬虫（会话凭据接口调用，5 频道 Forum Thread 解析）
├── spider_tieba.py                 # 百度贴吧爬虫 (Playwright 持久化登录态)
├── spider_xiaoheihe.py             # 小黑盒爬虫（Playwright 复用页面请求，支持 --catchup）
├── spider_bilibili.py              # B站爬虫 (消息中心 msgfeed API + WBI 签名；支持 --catchup)
│
├── run_spider.bat                  # 统一入口脚本：检测代理 → 运行流水线 → 展示摘要
│
├── check_fields.py                 # 飞书字段诊断工具 (手动调用)
├── retry_ai_summary.py             # AI总结重试工具
├── fix_discord_links.py            # Discord 链接修复工具
│
├── tencent_history.json            # 各平台本地历史记忆库 (自动生成，去重脱水用；
├── steam_history.json              #  格式: {comment_id: {timestamp, title_prefix}}，
├── reddit_history.json             #  LRU 裁剪上限 HISTORY_MAX_SIZE=20000)
├── discord_history.json            # ↑
├── tieba_history.json              # ↑
├── xiaoheihe_history.json          # ↑
├── bilibili_history.json           # ↑
│
├── daily_log.txt                   # 运行日志 (main.py logging.FileHandler 输出)
├── bug_feedback_agent.log          # 主日志文件 (console_utils RotatingFileHandler, 5MB 轮转)
├── run_summary.txt                 # 每轮运行摘要 (run_spider.bat 结束后展示)
│
├── AGENTS.md                       # 项目全景架构文档 (AI Agent 开发参考)
└── README.md                       # 本说明文档
```

---

## 🛠️ 快速开始

### 1. 环境准备

安装核心依赖库，并初始化 Playwright 浏览器环境：

```bash
pip install openai requests beautifulsoup4 playwright python-dotenv urllib3
playwright install chromium
```

### 2. 配置文件 (.env)

在项目根目录创建或编辑 `.env` 文件，填入以下参数（也可复制 `.env.example` 作为模板）：

```env
# ================= AI 大模型配置（智谱 GLM 开放平台） =================
AI_API_KEY="..."                             # 智谱开放平台 API Key
AI_BASE_URL="https://open.bigmodel.cn/api/paas/v4/"
AI_MODEL_CHEAP="glm-4-flash"                 # 主力模型（免费、稳定）
AI_MODEL_HEAVY="glm-4-flash"                 # 长文本路由模型
# 模型降级链：glm-4-flash → glm-4-flashx → glm-4-air（主模型失败时依次尝试）

# ================= 飞书应用凭证 =================
FEISHU_APP_ID="cli_..."
FEISHU_APP_SECRET="..."
FEISHU_APP_TOKEN="..."                       # wiki 解析后的 obj_token（base token）
FEISHU_TABLE_ID="tbl..."                     # 总表 ID（七平台统一写入目标）

# ================= 代理配置 =================
# 脚本自动检测用户代理软件（端口 17890/17891/4780），无需手动配置
# 如需强制指定其他代理，取消注释: PROXY_URL="http://127.0.0.1:xxxx"
# 优先级: 用户代理(17890等) → PROXY_URL 自定义 → 直连
# ⚠️ 国内平台（腾讯/贴吧/小黑盒/B站）代码内全程直连，不走代理

# ================= 腾讯频道配置 =================
TENCENT_CHANNEL_URL="https://pd.qq.com/g/your_channel_id?tab=1"
TENCENT_MAX_POSTS=20                        # 每次巡逻最大抓取篇数
HEADLESS_MODE="True"                        # 首次设为 False 扫码后改为 True

# ================= Steam 配置 =================
STEAM_APP_ID="your_steam_app_id"
STEAM_MAX_ANNOUNCEMENTS=2                   # 公告区抓取篇数
STEAM_MAX_DISCUSSIONS=20                    # 讨论区抓取篇数 (每个子板块)
STEAM_FETCH_MODE="all"                      # 抓取模式: all/announcements/discussions
STEAM_IGNORE_HISTORY="False"                # 是否忽略历史记录强制重抓

# ================= Reddit 配置 =================
REDDIT_SUBREDDIT="your_subreddit"
REDDIT_FETCH_LIMIT=15
REDDIT_USER_AGENT="Mozilla/5.0..."

# ================= Discord 配置 =================
DISCORD_USER_TOKEN="your_discord_user_token"  # 网页端抓包获取的 User Token
DISCORD_FETCH_LIMIT=15
# 当前采集频道: 综合建议 / 本地化反馈 / Bug 反馈 / 专项玩家反馈 / 测试服反馈（频道 ID 自行配置）

# ================= bilibili 配置 =================
# Cookie 由浏览器导出插件保存为 bilibili_cookies.json（已 gitignore），更新 Cookie 直接改该文件
BILI_COOKIES_FILE="bilibili_cookies.json"
BILI_MSGFETCH_LIMIT=20                      # 日常巡逻单次入库上限（补抓模式不受限）

# ================= 小黑盒配置 =================
XIAOHEIHE_USER_ID="your_xiaoheihe_user_id"                # 官方号 UID
XIAOHEIHE_MAX_POSTS=15
XIAOHEIHE_HEADLESS_MODE="True"

# ================= 百度贴吧配置 =================
# （配置项见 .env.example 与 .env 实际文件）
```

### 3. 运行流水线

双击 `run_spider.bat` 即可运行。脚本会**自动检测代理环境**并执行全量巡逻：

1. 检测本机代理软件（端口 17890 / 17891 / 4780）
2. 七平台**并行执行**爬取任务
3. 任务结束后在终端展示运行摘要（`run_summary.txt`）

**代理配置说明**：
- 脚本会自动按优先级检测可用代理端口，无需手动配置
- 请确保您的代理软件（如 Clash、v2rayN）已开启
- 如需强制指定代理端口，修改 `.env` 中的 `PROXY_URL`

### 4. 补抓模式（Catch-up）

断档或首次接入时，可对 bilibili / 小黑盒按时间窗口补抓（已入库 ID 自动去重）：

```bash
python spider_bilibili.py --catchup 30      # 补抓近 30 天消息中心回复（翻页至窗口起点）
python spider_xiaoheihe.py --catchup 30     # 补抓近 30 天官方号动态+评论（滚动翻页）
```

---

## 代理要求

本项目访问 Steam / Reddit / Discord 需要代理；国内四平台（腾讯 / 贴吧 / 小黑盒 / B站）全程直连。

请自行准备代理软件（Clash / v2rayN 等），按下表优先级自动探测：

| 优先级 | 代理来源 | 端口 | 协议 |
|--------|---------|------|------|
| 1 | 用户代理 | 17890 / 17891 / 4780 | HTTP |
| 2 | 自定义 `PROXY_URL` | 任意 | HTTP |
| 3 | 直连 | - | - |

> 如需强制指定，在 `.env` 中设置 `PROXY_URL`，例如 `PROXY_URL="http://127.0.0.1:7890"`。

### 故障排查

| 问题 | 解决方案 |
|------|----------|
| 代理端口被占用 | 检查端口是否被其他程序占用，或改用 `PROXY_URL` 指定 |
| 小黑盒返回 show_captcha | 代码已内置首页预热 + 浏览器上下文统一 + 重试；仍失败则等下一轮定时任务，勿高频重试 |
| B站提示 Cookie 过期 | 用浏览器导出插件重新导出，覆盖 `bilibili_cookies.json` |

---

## 📊 飞书数据字典参考

> 2026-07-28 起，原 4 张分表已合并为 1 张总表，七平台统一写入；字段名已全面重命名。

| 字段名称 (Column Name) | 飞书字段类型 | 说明 |
| --- | --- | --- |
| **平台** | 单选标签 | `[Discord] 频道名` / `Reddit (版块名)` / `Steam (板块名)` / `腾讯频道` / `[bilibili] 专栏动态` / `[bilibili] 视频` / `小黑盒` / `🚨 系统告警` 等。 |
| **用户名** | 文本 | 发帖者用户名。 |
| **反馈时间** | 日期 | 玩家实际发出该评论的时间戳（毫秒），格式 yyyy/MM/dd HH:mm。 |
| **链接** | 文本 | 原始 URL（纯文本，非超链接类型；B站评论链接带 `#reply{rpid}` 锚点可直达楼层）。 |
| **未处理的原内容** | 文本 | 原始文本全貌（800字截断），作为 AI 分析的 Source of Truth。 |
| **AI总结** | 文本 | 由**表端内置 LLM 字段自动生成**（爬虫停写），`1. 2. 3.` 结构化排版。 |
| **分类 (核心)** | 单选标签 | 2026-09-03 v3.2 十一类体系：**紧急故障P0**（崩溃/闪退/打不开/坏档等无法游玩）、**纯情绪发泄**（无具体问题的抱怨）、**功能异常P1**（核心玩法功能受损但可游玩）、**反馈&建议**（改进诉求）、**其它**（杂项兜底）、**玩家讨论（不处理）**（打法/阵容讨论）、**描述/本地化问题**（文案翻译错误/描述与实际不符）、**UI/美术**（贴图/UI错位/乱码）、**一般BugP2**（不影响核心玩法的轻微异常）、**数值吐槽/平衡类**（强度/平衡性抱怨）、**性能&体验优化类**（卡顿/掉帧/加载慢/发热或性能与操作体验优化诉求）。三个 bug 类名称绑定优先级；硬校验：AI 判为紧急故障P0 但内容不含致命关键词时自动降级为功能异常P1。 |
| **AI 优先级 (核心)** | 单选标签 | `P0` (紧急修复), `P1` (高优), `P2` (中优), `P3` (低优/讨论)。 |
| **处理标签** | 单选标签 | 未处理（默认，仅人工打标）/ 已解决 / 已提报并修复中 / 未复现 / 需跟进 / 未修 / 不处理 / 不确定 / 已知问题 / 待处理 / 机制设计 / 跟进中（共 13 选项）。 |
| **在地址栏输入链接打开图片** | 文本 | 图片/视频等媒体附件的 URL。 |
| **原反馈（翻译版）** | 文本 | 外文反馈的中文翻译（表端内置 LLM 字段生成）。 |
| **联系方式 / 附件 / 备注** | 文本/附件/文本 | 人工维护字段，爬虫不写入。 |
| **创建时间 / 收集时间 / ID** | 自动 | 系统自动记录。 |

---

## 🔧 调试工具

### check_fields.py - 飞书字段诊断

当飞书写入异常时，用于检查实际字段名：

```bash
python check_fields.py
```

### IncrementalHistoryManager - 历史记录防崩溃机制

`base_utils.py` 中提供了 `IncrementalHistoryManager` 类，实现增量保存 + 自动备份回滚：

**核心功能**：
- **增量保存**：每成功处理一条帖子就立即保存到磁盘，崩溃时最多只丢失最后一条
- **自动备份**：每次加载历史记录前自动创建 `.bak` 备份
- **回滚恢复**：支持恢复到上次运行前的状态
- **LRU 裁剪**：超过 `HISTORY_MAX_SIZE`（默认 20000）时自动淘汰最旧记录

**使用方式**：
```python
from base_utils import IncrementalHistoryManager

manager = IncrementalHistoryManager("tencent_history.json")
history = manager.load()  # 自动创建备份

# ... 处理帖子，成功后立即保存 ...
manager.mark_success(comment_id, timestamp, title_prefix)

# ... 所有处理完成 ...
manager.commit()  # 确认，删除备份

# 或者崩溃后想回滚：
manager.rollback()  # 恢复到 load() 前的状态
```

---

## ⚙️ 并发与性能参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 平台并行数 | **7** (全量并行) | 七大战区同时执行（asyncio.gather），非串行 |
| 单平台 AI 并发 | **Semaphore(3)** | 每个爬虫同时最多 3 条 AI 分析 |
| AI 线程池 | **max_workers=3** | 全局线程池大小 |
| 单次 AI 超时 | 微观 60s | 含重试时间的硬上限 |
| 模型重试次数 | 3 次/模型 | 单个模型最多尝试 3 次 |
| 模型降级链 | glm-4-flash → glm-4-flashx → glm-4-air | 逐级降级保证不阻塞 |

---

## ⚠️ 注意事项

1. **腾讯频道首次运行**：将 `HEADLESS_MODE` 设为 `False`，扫码登录成功后改回 `True`（不过实际上不登录也能正常运行）
2. **B站 Cookie 有效期**：官方号 Cookie 失效时爬虫返回 `bili_cookie_expired` 并触发飞书告警，用浏览器导出插件重新导出覆盖 `bilibili_cookies.json` 即可（该文件已 gitignore，更新 Cookie 直接改 JSON，不经过 .env）
3. **小黑盒风控**：首次/长期未运行后 API 可能返回 `show_captcha`，代码已内置首页预热 + 浏览器上下文统一 + 重试；仍失败等下一轮定时任务，勿高频重试
4. **国内站直连**：腾讯/贴吧/小黑盒/B站四平台不走海外代理；`base_utils` 的代理 WARN 日志是全局打印，与具体爬虫无关
5. **字段变更**：飞书表格字段变更后，系统会自动适配（缓存 5 分钟，模糊匹配空格差异）
6. **历史记录**：删除 `*_history.json` 可强制重抓所有内容，删除 `*_history.json.bak` 可清除备份
7. **Steam 调试**：设置 `STEAM_IGNORE_HISTORY="True"` 可忽略历史记录；设置 `STEAM_FETCH_MODE="discussions"` 可只抓讨论区
8. **崩溃恢复**：如果程序在保存历史记录前崩溃，可使用 `IncrementalHistoryManager.rollback()` 恢复到最后一次加载时的状态
9. **日志文件**：
   - `daily_log.txt`：main.py 的 logging FileHandler 输出
   - `bug_feedback_agent.log`：console_utils 的 RotatingFileHandler 输出（5MB 轮转，保留 3 份）
10. **Discord Token 有效期**：User Token 可能因密码修改或 Discord 安全策略失效，需定期更新 `.env`
11. **B站官方号风控敏感**：bilibili 每轮任务仅 1~2 个 API 请求、失败不重试（等下一轮定时任务），勿手动高频补抓

---

## 📝 变更日志

### v3.0.1 (2026-09-05) — 小黑盒改评论粒度入库
- **变更** 小黑盒入库粒度：~~每条动态一条记录（正文+评论合并）~~ → **每条玩家评论一条记录**，忽略动态正文（官方自建内容非玩家反馈），动态标题仅作 AI 分析上下文
- **新增** 楼中楼子评论保留 `[回复 {主评论者}]` 最小上下文；comment_id = `c_{commentid}`
- **优势** 评论级去重——老动态来了新评论也能增量抓到（旧合并粒度下老动态新评论会永久丢失）
- **新增** 动态处理时间窗口 `XIAOHEIHE_WINDOW_DAYS`（默认 30 天，老动态的新评论概率极低不进详情页）
- **补录** 近 30 天若干动态的玩家评论全量入库验收通过（无正文残留、分类分布合理、无越权 P0）

### v3.0.0 (2026-09-05) — 七战区全域覆盖 + 分类体系定稿

- **新增** bilibili 战区：消息中心「回复我的」msgfeed API + WBI 签名，官方号 Cookie（`bilibili_cookies.json`，gitignore 管理）；回复内容 API 层完整无截断；支持 `--catchup` 翻页补抓
- **新增** 小黑盒战区：Playwright 拦截页面自然 API 请求（复用页面自身请求，无需逆向接口签名），动态+楼内评论合并入库；首页预热 + 浏览器上下文统一；支持 `--catchup` 滚动翻页补抓
- **新增** 百度贴吧战区（Playwright 持久化登录态）
- **修复** main.py 战区注册缺陷：小黑盒/bilibili 已 import 但未注册进 `missions` 列表，导致定时任务不执行两平台；现已注册为七平台并行
- **更新** 分类体系定稿 v3.2（十一类，三个 bug 类绑定优先级；`FATAL_KEYWORDS` 硬校验降级）
- **更新** 字段体系：2026-07-28 四表合并为总表 `<FEISHU_TABLE_ID>` + 字段重命名（平台/用户名/反馈时间/链接/未处理的原内容/AI总结/AI 优先级/处理标签）；停写"情感倾向""人工/AI 备注"；宏观简报功能删除
- **更新** AI 基建切换智谱 GLM 开放平台（glm-4-flash 主力，免费稳定）；AI总结/原反馈（翻译版）改由表端内置 LLM 字段生成，爬虫停写
- **安全** 所有 `*_cookies*.json`、Playwright 浏览器数据目录纳入 `.gitignore`；解除历史误跟踪的 `reddit_cookies.json` 与 `playwright_tieba_data/`
- **补抓** bilibili 近一月回复补抓 +9 条入库；小黑盒近一月动态确认已全覆盖
- **文档** AGENTS.md 全面对齐七战区现状

### v2.4.0 (2026-07-08) — Reddit 方案升级 + 追补
- **重构** Reddit 爬虫：从 Playwright 浏览器方案切换为 `requests` + `BeautifulSoup` 直接抓取 old.reddit.com HTML
  - old.reddit.com 为服务端渲染，无需 JS 执行，无需浏览器
  - 解决 Playwright 与 base_utils 全局代理环境变量冲突（Chromium 走 代理软件 代理被 Reddit 封）
  - `trust_env=False` 确保 requests Session 不受全局 HTTP_PROXY 影响
  - 新增 `_safe_get()` 429 限流指数退避重试（2s → 4s → 8s）
- **新增** `--catch-up` 追补模式：按日期截止抓取遗漏帖子，跳过历史边界检查
- **追补** 124 篇 ~5/28-7/8 期间遗漏的 Reddit 帖子全部写入飞书
- **对比结论** RSS vs Browser vs requests+BS4：
  | 方案 | 评论 | 代理冲突 | 翻页 | 结论 |
  |------|------|---------|------|------|
  | .json API | 有 | 403 封禁 | - | 已废弃 |
  | RSS | 无 | 无 | 不支持 | 数据不完整 |
  | Playwright | 有 | 有 | 支持 | 代理冲突 |
  | **requests+BS4** | **有** | **无** | **支持** | ✅ 最优 |
- **清理** 删除 6 个诊断/测试脚本

### v2.3.0 (2026-07-07) — 渠道失败告警 + 腾讯频道修复
- **新增** `report_channel_failure()` 飞书告警机制：任一渠道失败时写入 P0 系统告警记录
- **修复** 腾讯频道从"热门"标签切换到"全部"标签（真正的时间降序）
- **追补** 腾讯频道 13 篇遗漏帖子（因"热门"标签非时间排序导致）
- **修复** Steam 多重时间过滤完善（列表 data-tooltip-forum + OP 容器 + Playwright 兜底）
- **修复** Discord 函数内重复 `import datetime`

### v2.2.2 (2026-06-05) — Steam 旧帖拦截深度修复
- **修复** **严重 BUG**：Steam 列表页 `data-timestamp` 是"最后活动"（lastpost）不是发帖时间，导致 3 年前旧帖被错误识别为 6 小时前新帖
- **修复** 详情页 OP 楼主选择器错误（OP 在专用 `.forum_op` 容器中）
- **修复** 列表页时间无法解析时"乐观保留"导致旧帖漏拦：改为放进"待验证"列表，在详情页严格验证
- **新增** `_parse_steam_time` 时间解析：支持英文/中文、相对/绝对、12h/24h 共 17 种时间格式
- **新增** `_extract_op_post_time_ms` 辅助函数：4 级回退机制
- **强化** 保守策略：详情页提取不到 OP 时间戳时**直接跳过**该帖（宁可漏抓不可错抓）
- **测试** 添加 30+ 单元测试用例

### v2.2.1 (2026-06-01)
- 修复 6 项 BUG：重复 write_to_feishu、Playwright 时间过滤、死代码、重复 import、字段映射、函数内 import

### v2.2 (2026-06-01) — 稳定版本
- **新增** Steam 30 天帖子级时间过滤（列表页时间解析 + 详情页 OP 二次验证）
- **修复** 重复 `write_to_feishu` 函数定义导致成功日志丢失
- **修复** Playwright 兜底路径缺少时间过滤
- **安全** 新增 `.gitignore` 排除敏感文件（.env / Cookies / 日志 / 缓存）
- **安全** 新增 `.env.example` 模板文件供新用户参考

### v2.1 (2026-05-26)
- 四平台并行调度（asyncio.gather）
- 模型智能路由与降级机制
- IncrementalHistoryManager 防崩溃增量保存
- Discord 5 频道全面监控
