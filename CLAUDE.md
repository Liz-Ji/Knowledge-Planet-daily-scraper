# 星球内容助手

每天自动抓取指定知识星球群组的「星主发布」和「精华」内容，写入飞书多维表格；可选用大模型给每条内容生成一句话摘要+主题标签；每周日自动生成精华周报推送到飞书群；提供命令行语义问答（RAG）；Cookie 失效时通过飞书机器人 Webhook 报警。

## 抓取范围

- 姜胡说 (group_id: 552521142424)
- 珍大户的经济圈 (group_id: 458522225218)
- 只抓 `scope=by_owner`（星主发布）和 `scope=digests`（精华），不抓全部动态——避免表格被普通成员灌水内容淹没。

## 架构决策

- **语言**: Python（生态成熟，requests 足够应对这种"爬 API + 调用飞书接口"的任务，无需引入框架）。
- **知识星球访问方式**（2026-07-06 实测跑通）：
  - 端点用 **v2**：`https://api.zsxq.com/v2/groups/{group_id}/topics?scope={by_owner|digests}&count=20`。最初试的 `v1.10` 会被拒（返回"版本太旧"）。
  - 必带请求头：`Authorization`（= Cookie 里的 `zsxq_access_token`）、`User-Agent`、**`x-version: 2.64.0`**。缺 `x-version` 会被判定为"版本太旧"直接拒绝。不需要签名头。
  - **反爬拦截 code=1059**："不支持非官方工具访问"，实测是**概率性拦截**（约 1/5 请求命中），并非硬封禁。客户端遇到 1059 会退避重试（最多 4 次），基本都能拿到数据。
  - 风险：`x-version` 值会过期（试过 `2.71.0` 反而被更严的规则拒），拦截概率或规则可能随时收紧。若某天大量请求持续 1059、或提示"版本太旧"，先去 wx.zsxq.com 网页版抓包看当前 `x-version` 值和端点，更新 `zsxq_client.py` 顶部的 `X_VERSION` 常量。
  - 正文里含 ZSXQ 行内实体标签（`<e type="text_bold"/hashtag/web/mention" title="URL编码文字"/>`），`clean_text()` 会把 `title` URL 解码还原成纯文本，去掉标签。
- **去重方案**: 本地维护 `state/seen_topic_ids.json` 记录已同步的帖子ID。首次运行时会先拉取飞书表格里已有的「帖子ID」种子去重集合，之后完全依赖本地状态文件（避免每次都全量拉表格，省 API 调用）。
  - 如果 `state/` 被误删，下次运行会自动从飞书表格重新同步已有ID，不会产生重复数据，但会有一次全表扫描开销。
- **失败处理**: 知识星球返回 401/403 或响应体提示"登录"失效时，判定为 Cookie 过期，通过 `FEISHU_ALERT_WEBHOOK`（飞书群机器人）推送提醒文本消息，同时记录日志后退出，不重试（避免频繁触发风控）。
- **定时方式**: Windows 任务计划程序，**双触发器**：①「每次登录」(AtLogOn) 贴合"每天第一次开机就抓"；②「每天 9:00」固定时间兜底。任务通过 `scripts/run_daily.ps1` 调用 `.venv` 里的 Python 执行 `src/main.py`。用 `scripts/setup_task.ps1` 注册/更新任务。
  - **为什么要加固定时间兜底（踩坑）**：只用 AtLogOn 时，如果机器一直开着、当天不重新登录，触发根本不发生 → 当天不抓、且因为"没跑≠失败"所以机器人也不报警，用户就会遇到"既没更新也没提醒"。加每天 9:00 触发（配合 `StartWhenAvailable` 错过会补跑）后，机器长开不重登也能到点自动跑。
  - **每天只成功抓一次**：`main.py` 成功后写 `state/last_success_date.json`，当天再被触发（多次登录）会直接跳过。加 `--force` 可强制重跑。
  - **失败/不完整自动重试 + 提醒**：任一星球×范围抓取失败、或写飞书失败时，`main.py` 以退出码 1 结束且「不」标记当天完成；任务计划设置了失败后每 30 分钟重试、最多 6 次（`RestartCount/RestartInterval`），同时通过飞书 Webhook 发提醒。这样即使用户当天不再重新登录，也会在 30 分钟内自动补抓。
  - **缺勤补齐**：抓取采用「从最新往回翻页，直到整页都是已抓过的帖子就停」的策略（`fetch_topics(known_ids=...)`）。正常每天只翻 1 页；多天没开机时会自动多翻几页把落下的补齐，`max_pages=8`（≈160 条/范围）为安全上限。
  - 注意：AtLogOn 触发依赖用户登录；若长期不开机，补齐范围受 `max_pages` 上限约束（超过 ~160 条的更老内容不会补）。

## 飞书多维表格字段

表格 `app_token=WdRpbvdI5apvj8snHhWcUQxYnNe`，`table_id=tblJhtsdN2aVD4A9`（已存在的表，字段是本项目通过 API 创建的）：

| 字段名 | 类型 | 说明 |
|---|---|---|
| 帖子ID | 文本（主键） | 用于去重，值为 ZSXQ 的 topic_id |
| 星球名称 | 文本 | 姜胡说 / 珍大户的经济圈 |
| 类型 | 单选（星主/精华） | 对应 scope=by_owner / scope=digests |
| 作者 | 文本 | |
| 标题 | 文本 | 仅文章类帖子有 |
| 正文 | 文本 | |
| 发布时间 | 日期时间 | |
| 点赞数 | 数字 | |
| 评论数 | 数字 | |
| 原文链接 | 超链接 | 拼接的 wx.zsxq.com 详情页链接 |
| 抓取时间 | 日期时间 | 本次任务运行时间，便于排查 |
| 摘要 | 文本 | 大模型生成的一句话摘要（未配 LLM 时留空） |
| 主题标签 | 多选 | 大模型从固定词表选的 1~3 个标签（词表见 summarizer.py TAGS） |

- **AI 摘要+主题标签（可切换大模型）**：`src/summarizer.py` 是一层「可插拔适配器」，对外只暴露 `get_enricher()`，主流程（抓取/去重/写飞书）完全不知道背后用哪个模型。
  - **为什么这样设计**：用户明确要求「以后可能从 Claude 换成 Codex/DeepSeek」。所以把「用哪个模型」做成配置项而非写死——换模型只改 `.env` 的 `LLM_*`，不改代码。
  - **两个适配器**：`_OpenAICompatEnricher` 覆盖所有 OpenAI 兼容接口（DeepSeek / OpenAI/Codex / 通义 / Kimi / 智谱…，改 `LLM_BASE_URL`+`LLM_MODEL`+`LLM_API_KEY` 即可切换）；`_ClaudeEnricher` 用 Anthropic 官方 SDK。两者用同一套提示词和 JSON 输出结构，输出稳定、不锁定任何一家。SDK 都是延迟导入，只装你用的那个（见 requirements.txt）。
  - **默认建议 DeepSeek**：这台机器是国内 Windows、定时任务本地后台跑。DeepSeek 国内直连不用代理，最适合无人值守；Claude/OpenAI 大陆访问通常要代理，后台代理一断任务就失败。质量上做一句话摘要+打标签，DeepSeek 够用。
  - **未配置时不影响主流程**：`LLM_API_KEY` 留空时 `get_enricher()` 返回 None，抓取入库照常，只是不加工。加工是在 `topic_to_record` 里对新帖**入库前**内联完成的（一次写入，无需二次更新）；单条加工失败只留空该字段、不影响整条入库。
  - **历史数据补加工**：AI 加工是后加的能力，之前抓的记录没有摘要/标签。配好 key 后运行一次 `src/backfill_enrich.py` 补齐（读「摘要」为空的行→加工→`batch_update` 写回，每 100 条一批）。之后新抓的自动加工，无需再跑。
  - **标签词表固定**：`summarizer.py` 的 `TAGS` 必须与飞书「主题标签」多选字段的选项保持一致；模型只能从词表里选，解析时会过滤掉词表外的标签、空则回落到「其他」。改词表要两边一起改。

- **通用 chat()**：`summarizer.py` 除了 `get_enricher()`，还导出 `chat(system, user)` 通用补全，周报和问答都用它——所以换模型（改 `.env` 的 `LLM_*`）时，摘要、周报、问答三处一起切换。

- **每周精华周报**（`weekly_report.py`）：读飞书里过去 7 天入库的记录，按点赞取前 60 条，用 `chat()` 汇总成分主题、带 Top5+链接的中文周报，通过飞书群机器人 Webhook（复用 `notifier.send_alert`）推送。由任务计划「星球内容助手-每周周报」每周日 20:00 触发（`scripts/run_weekly.ps1`）。飞书文本消息不渲染 markdown，代码里会去掉 `**`/`#` 等符号。`--dry` 只打印不推送。

- **命令行语义问答 / 轻量 RAG**（`ask.py`）：`python src/ask.py "姜胡说怎么看黄金"`。DeepSeek 无 embedding，所以用轻量方案：问题里点到星球名就按星球过滤 → jieba 抽关键词 → 按关键词在标题/正文/摘要的命中次数打分（点赞加权）取前 18 条 → 连同原文链接交给 `chat()` 综合回答并给出引用。选命令行是因为飞书内问答需要公网服务器接收消息，本地 Windows 做起来太重。

- **两个一次性补数脚本**：`fix_links.py`（把所有记录的原文链接重建为正确 mweb 格式，幂等）；`backfill_history.py`（深翻历史补全指定年份全部内容，默认 2025；靠 `fetch_topics(stop_before=...)` 一直翻到年份边界）。

- **知识图谱（按专题学习）**：解决"记了一堆但连不起来、没法按专题学"的痛点。
  - **专题体系**：`topics.py` 定义 24 个专题（含 5 大类），由全部内容用大模型提炼后人工收敛而来。每条帖子在加工时归「1 个」专题（`summarizer` 的 enrich 顺带产出，写回飞书「专题」单选字段）；历史用 `backfill_topics.py` 批量补（一次给模型 15 条）。
  - **图谱生成**：`build_graph.py` 读飞书按专题聚合，生成独立 HTML（项目根 `知识图谱.html`）：中心「知识库」→ 5 大类 → 24 专题的思维导图，点专题看「脉络综述 + 帖子清单（跳原文）」。脉络综述调大模型、带缓存（`state/graph_summaries.json`），只在帖子数变化或 `--refresh` 时重算。
  - **自动更新**：`main.py` 每日抓取成功后调 `build_graph.build(refresh=False)`（刷结构 + 帖子数变化的专题综述，很快）；`weekly_report.py` 每周 `build_graph.build(refresh=True)`（全量刷综述）。所以飞书新内容进来，图谱当天自动更新。
  - **待做**：作者「文内引用」硬连接（帖子间 `<e type="web" href=…/topic/{id}>` 的引用关系）尚未接入图谱，需要重抓历史原始数据提取；这是把图谱从"专题聚合"升级成"真·关系图谱"的下一步。

- **今日待看**（`build_reading.py` → 项目根 `今日待看.html`）：解决"总库最新在最后、翻着看不方便"的痛点，是一个**待看队列**。
  - **队列构成**：待看 = 之前没勾"看了"的 + 最新抓到的。最新在最上（按发布时间倒序）。
  - **勾选**：每条末尾一个「看了」勾选框——勾上就移出队列（下次不再出现），不勾就留到下次继续待看。
  - **感想**：每条一个 textarea，可一键「填入知识卡片模板」（标题/核心概念/为什么值得关注/能用在哪不能用在哪/最小行动/和已有知识的连接），为未来做成知识卡片铺路。
  - **状态存 localStorage**（键 `todo_status`/`todo_notes`/`todo_inited`）：已看和感想存在本机浏览器，每天重建同名文件也不会丢。首次打开只把最近 14 天进队列、更早历史自动归档（`todo_inited` 标记），避免 1800 条刷屏；之后每天新内容自动进队列。
  - 与图谱的已读是**各自独立**的两套状态（图谱用 `zsxq_read`，待看用 `todo_status`），互不影响。
  - 由 `main.py` 每日抓取成功后和图谱一起自动重建。

### 两个飞书数据坑（重要）
- **数字字段读回来是字符串**：飞书多维表格「点赞数/评论数」等 Number 字段，通过 API 读记录时返回的是字符串（如 `"14"`），不是数字。凡是拿它做排序/运算，必须先 `int(float(v))`（见 `weekly_report.py`/`ask.py` 的 `to_int`）。否则会静默排错序或报 `can't multiply sequence` 之类的错。
- **GET 请求别带 Content-Type**：见 `feishu_client._auth_header`，否则 400（已记录在架构决策里）。

### 原文链接格式
知识星球「复制链接」是 `https://t.zsxq.com/xxxx` 短链，302 跳转到
`https://wx.zsxq.com/mweb/views/topicdetail/topicdetail.html?topic_id={topic_id}`。
这个 mweb 格式登录后作为成员可正常打开；早期用的 `dweb2/index/topic_detail/{id}` 会提示「没有权限」。统一由 `zsxq_client.topic_url()` 生成。

## 密钥与配置

所有密钥放在 `.env`（已被 `.gitignore` 排除，不会提交到 Git）。`.env.example` 是给别人 clone 项目后参考的模板，不含真实值。

`secrets.txt.txt` 是早期临时记录密钥的文件，同样被 `.gitignore` 排除；密钥已迁移到 `.env`，该文件可以手动删除。

`ZSXQ_COOKIE` 需要人工从浏览器获取（登录 wx.zsxq.com 后，F12 -> 网络 -> 任意 api.zsxq.com 请求 -> 复制 Cookie 请求头，或只复制其中的 `zsxq_access_token` 值），无法自动化获取，失效后需要重复此步骤。

**踩坑记录（重要）**：机器上曾残留系统级环境变量 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，指向的是另一个飞书应用，导致鉴权 99991672（权限不足）。`python-dotenv` 默认**不覆盖**已存在的系统环境变量，所以 `config.py` 里用了 `load_dotenv(override=True)` 强制让项目 `.env` 生效。排查这类"curl 能通、脚本报权限错"的问题，先确认脚本实际读到的 app_id 是不是 `.env` 里那个。

## 目录结构

```
src/
  config.py         # 读取 .env
  zsxq_client.py    # 知识星球抓取
  feishu_client.py  # 飞书多维表格读写（含 batch_update / list_all_records）
  notifier.py       # Webhook 报警
  summarizer.py     # 可切换大模型层：get_enricher() 摘要+标签、chat() 通用补全
  backfill_enrich.py# 给历史记录补做 AI 摘要+标签（配好 key 后跑一次）
  backfill_history.py# 深翻历史补全指定年份全部内容（默认2025，跑一次）
  fix_links.py      # 批量重建原文链接为正确 mweb 格式（幂等）
  weekly_report.py  # 每周精华周报生成+推送（--dry 只打印）
  ask.py            # 命令行语义问答：python src/ask.py "问题"
  topics.py         # 24个专题(含大类)分类体系（知识图谱骨架）
  backfill_topics.py# 批量给历史记录归专题（配好 key 跑一次）
  build_graph.py    # 生成/刷新知识图谱 HTML（--refresh 全量刷综述）
  build_reading.py  # 生成/刷新「今日待看」HTML（待看队列+勾选+感想）
  main.py           # 主流程入口
scripts/
  run_daily.ps1     # 每日抓取入口（任务计划调用）
  run_weekly.ps1    # 每周周报入口（任务计划调用）
  setup_task.ps1    # 注册/更新两个任务计划（每日AtLogOn + 每周日20:00）
state/
  seen_topic_ids.json     # 去重状态（不入库）
  last_success_date.json  # 当天是否已成功抓取的标记（不入库）
logs/
  YYYY-MM-DD.log       # 每日运行日志（不入库）
```
