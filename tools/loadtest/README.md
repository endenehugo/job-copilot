# Job Copilot 接口性能测试

本目录复用项目既有 Locust 工具，提供**单阶段、可限速、默认不触发模型**的接口测试。测试阶段须逐次单独启动；脚本不会自动从 5 用户升到 10、20 或 40 用户。先在本机或独立压测机运行，避免与 2 核/2GB 被测服务争抢 CPU 和内存。

> 只对你拥有或明确获准测试的目标运行。工具仅允许 `displaydemo.top`、`47.92.74.156` 和本机回环地址。本文档不执行远程连接或线上测试；示例默认指向站点，运行者须自行确认当前授权、维护窗口、配额和数据清理。

## 代码核对与接口风险

接口均来自 `backend/app/main.py` 与 `backend/app/api/`：API 前缀是 `/api/v1`。访客无需登录；`VisitorMiddleware` 首次请求签发隔离 Cookie，之后由 `owner_key` 控制会话可见性。管理员请求可通过 `LT_ADMIN_TOKEN` 传 `X-Admin-Token`。响应通常是 `{code, message, data}`，业务失败有时仍返回 HTTP 200，因此只看状态码会误判成功。基础脚本同时检查 HTTP 状态、`code == "success"` 和关键字段。

| 方法与路径 | 用途/数据 | 风险及测试策略 |
|---|---|---|
| `GET /api/v1/system/health` | 进程健康状态；不实时请求外部服务 | 低风险，只读 |
| `GET /api/v1/skills` | `data.skills` 静态技能清单 | 低风险，只读 |
| `POST /api/v1/conversation/create` | JSON `{title, mode}`，成功返回 `data.conversation_id` | 写 MySQL；每个虚拟用户有独立 Cookie 和 ID，仅标题 `lt-session` 共用，标为 `[setup]` |
| `GET /api/v1/conversation/list?limit=50` | `data.conversations`，按访客归属过滤 | 只读 |
| `GET /api/v1/conversation/detail?conversation_id=...` | 会话对象含 `conversation_id/messages/documents` | 只允许当前 Cookie/管理员可读；脚本使用自己的有效 ID |
| `POST /api/v1/conversation/delete` | JSON `{conversation_id}` | 级联删会话及其关联文件/索引；只对本虚拟用户创建的会话调用，标为 `[cleanup]` |

`backend/app/core/visitor_middleware.py` 负责访客 Cookie；`conversation_store_service.py` 对列表/详情执行归属过滤。`backend/app/repositories/mysql_base.py` 使用 SQLAlchemy `QueuePool`，配置的 `MYSQL_POOL_SIZE` 默认 5，未显式设置 `max_overflow`（SQLAlchemy 默认值）；项目没有暴露连接池占用、等待时间或数据库耗时指标。

AI/重型链路不混入基础场景：

- `POST /api/v1/conversation/chat` 与 `/chat/stream` 调用 `ConversationChatService` 和模型。`memory` 也会调用外部 LLM；RAG 会涉及检索/embedding；`agent` 可触发工具（如网页搜索、文档生成）。AI 文件默认因 `LT_ENABLE_AI` 未开启而拒绝启动；agent 还要求 `LT_ENABLE_AGENT=1`。
- `POST /api/v1/job/analyze` 至少调用 JD 分析与简历评分模型，并写入分析记录；需真实上传解析简历才能成功，不在默认脚本里编造简历。
- `/api/v1/interview/start`、`/answer` 调用模型、创建面试状态；start 还会后台预取题目。没有合成简历/JD 夹具前不自动触发。
- `/api/v1/resume/generate` 调模型并写入简历生成记录/文件；`/resume/generations/{id}/regenerate` 虽不调 LLM，仍会新增记录。`/resume/project/rewrite` 会调用模型并落库。
- `/api/v1/knowledge/query` 会对查询做向量检索（embedding 服务可能在缓存缺失时产生外部调用）；`/knowledge/entries`、`self-expand`、`rebuild` 会审核/生成内容或改写知识库/索引，应单独准备可回滚的测试数据。
- `/api/v1/document/upload` 会解析文档、生成向量并写入文件/数据库；图片上传也会写文件。以上均不包含在低风险场景中。

项目未提供把应用耗时、数据库耗时与 LLM 上游耗时拆开的 tracing/metrics；聊天和 AI 统计是**端到端延迟**，不能据此声称纯后端处理时间。

## 安装与配置

Python 版本按后端依赖为 Python 3.12。Locust 是压测端工具，不要装进生产后端依赖。示例：

```powershell
py -3.12 -m venv .venv-loadtest
.\.venv-loadtest\Scripts\python.exe -m pip install "locust==2.47.0"
```

复制 `.env.example` 到本机安全位置并按需设置环境变量。Locust 不会自动读取该文件；不要把真实 Token 写入仓库或命令历史。PowerShell 可逐项设置 `$env:LT_HOST`、`$env:LT_USERS` 等。`--host`、`--users`、`--spawn-rate`、`--run-time` 可由命令行覆盖同名环境变量。

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `LT_HOST` | `https://displaydemo.top` | 仅允许站点、指定服务器 IP 或本机回环 |
| `LT_SCENARIO` | `basic` | `basic` 加权轻量请求，或 `business` 顺序业务读取流 |
| `LT_USERS` / `LT_SPAWN_RATE` / `LT_RUN_TIME` | `1` / `1` / `60s` | 单次阶段的并发、每秒启动用户数、持续时间 |
| `LT_MAX_RPS` / `LT_AI_MAX_RPS` | basic `5` / AI `0.5` | 单 Locust 进程全局请求启动速率；远程 basic 硬上限 30、AI 硬上限 1 RPS；多 worker 时每 worker 单独计 |
| `LT_WAIT_MIN` / `LT_WAIT_MAX` | `0.1` / `0.5` | basic 用户任务等待秒数；business 在每轮四个请求之间等待 |
| `LT_WEIGHT_LIST/DETAIL/SKILLS/HEALTH` | `5/4/1/1` | basic 场景权重；设为 `0` 可关闭单类任务，但至少一个权重需大于 0 |
| `LT_CONNECT_TIMEOUT` / `LT_READ_TIMEOUT` / `LT_AI_READ_TIMEOUT` | `5` / `15` / `200` 秒 | AI 专用读取超时覆盖单模型重试等待；仍不保证覆盖完整 fallback 链 |
| `LT_ADMIN_TOKEN` | 空 | 可选，注入 `X-Admin-Token`；否则使用每个用户自己的访客 Cookie |
| `LT_BASIC_AUTH_USER` / `LT_BASIC_AUTH_PASSWORD` | 空 | 可选，用于部署层 HTTP Basic Auth；必须成对设置 |
| `LT_ABORT_FAILURE_RATE` | `0.10` | 滚动窗口失败率阈值；`0` 关闭该项 |
| `LT_ABORT_5XX_STREAK` | `3` | 连续 5xx 触发停止的次数 |
| `LT_ABORT_P95_MS` | basic `2000`，AI `30000` | 最近请求滚动 P95 超阈值停止；`0` 关闭 |
| `LT_ABORT_WINDOW` | `50` | 安全阈值的最近请求窗口大小 |
| `LT_ENABLE_AI` | `0` | 必须显式设为 `1` 才允许加载 AI 脚本 |
| `LT_AI_MAX_REQUESTS_PER_USER` | `1` | 每个 AI 虚拟用户最多 1–3 次模型请求 |
| `LT_MODE` | `memory` | AI 场景可选 `memory`、`rag`、`agent`；RAG/Agent 必须准备独立测试数据 |
| `LT_ENABLE_AGENT` | `0` | `LT_MODE=agent` 的第二道显式确认 |
| `LT_QUERY` / `LT_MODEL` | 合成短问题 / 空 | 不包含个人数据；指定模型前确认模型权限、供应商成本与额度 |

基础场景最多 30 用户、单次 300 秒；31–40 用户需显式 `--confirm-40-users`（或设置 `LT_CONFIRM_40_USERS=YES`），不得超过 40。基础场景 spawn rate 最高 5 用户/秒。AI 场景最多 2 用户、120 秒、spawn rate 最高 1 且每用户默认只调用一次。远程目标必须设置正的 RPS 上限（basic ≤30、AI ≤1）；仅本机回环测试可以用 `--max-rps 0` 不限速。测试必须有有限 `--run-time`，禁止无限运行。

## 分阶段运行

每条命令只执行一个阶段。确认上一阶段错误率、延迟和服务器资源稳定后，再手动启动下一条。以下默认均为 60 秒、spawn rate 1 用户/秒；可单独调整，不会自动升压。先在本地/独立压测机运行；生产目标只在授权和维护窗口内使用。

```powershell
# 准备一份独立的输出前缀；Locust 会生成 _stats.csv、_stats_history.csv、_failures.csv
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$stage = "basic-01"
$users = 1
New-Item -ItemType Directory -Force -Path "reports\$stamp" | Out-Null
$prefix = "reports\$stamp\$stage"

# 单次只运行一个阶段。复核结果后，手动把 $stage/$users 改为下表中的一行再运行。
.\.venv-loadtest\Scripts\locust.exe -f tools/loadtest/locustfile_data.py --headless --scenario basic `
  --users $users --spawn-rate 1 --run-time 60s --max-rps 5 `
  --csv $prefix --csv-full-history --html "$prefix.html"
```

建议阶段顺序为基线 1–2、5、10、15–20、25–30 用户；每次只将 `$stage` 与 `$users` 设置为单一目标值，再运行上面的命令。每档应在查看报告与服务器资源后由操作者决定是否继续。40 用户仅在确认前一阶段稳定后，将 `$users=40` 并在命令中增加 `--confirm-40-users`。默认全局限速 5 RPS；只有确认承载安全后才调整。business 流程可把 `--scenario basic` 改成 `--scenario business`，仍需独立阶段运行。

| 阶段标签 | 用户数 | 测试前提 |
|---|---:|---|
| `stage-01` | 1–2 | 基线/脚本校验 |
| `stage-05` | 5 | 轻负载 |
| `stage-10` | 10 | 中低负载 |
| `stage-15` / `stage-20` | 15 / 20 | 对照历史报告 |
| `stage-25` / `stage-30` | 25 / 30 | 仅在前阶段稳定时单独运行 |
| `stage-40` | 40 | 还需 `--confirm-40-users`，明确人工确认 |

可以用 `--max-rps 5` 或 `LT_MAX_RPS=5` 限制单进程全局请求启动速率；远程目标上限 30 RPS，不能用 0 关闭。运行前检查目标 host、用户数、持续时间和 spawn rate。发生错误率超限、连续 5xx 或滚动 P95 超限时会请求 Locust 停止；`Ctrl+C` 是人工紧急停止方式。测试异常退出时 `on_stop` 未必执行，按下文检查是否遗留 `lt-*` 会话，不要不经核对使用不限归属的数据库清理。

若异常退出留下 `lt-session`，可以在**经确认连接到目标数据库**的后端管理环境里运行现有 `cleanup_loadtest.py`：先 dry-run 核对数据库主机/库名、归属和行数；只在确认都是压测数据后，才使用 `--yes --confirm-db <库名>`。该脚本会直接删数据库记录与登记文件，默认只匹配 `lt-session` 和访客/admin owner；不要用 `--any-owner`。没有后端数据库访问权限时，不要为清理而把生产凭据复制到压测机。

### AI 请求（默认禁用）

AI 只单独运行 `locustfile.py`，不可与基础场景混用。默认每虚拟用户只调用一次 `/conversation/chat`，最多 2 用户，不含自动阶梯：

```powershell
$env:LT_ENABLE_AI = "1"
$env:LT_AI_MAX_RPS = "0.5"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force -Path "reports\$stamp" | Out-Null
.\.venv-loadtest\Scripts\locust.exe -f tools/loadtest/locustfile.py --headless --scenario ai --users 1 --spawn-rate 0.5 --run-time 60s --csv "reports\$stamp\ai-memory-01" --csv-full-history --html "reports\$stamp\ai-memory-01.html"
```

必须先确认模型余额、限流策略和用户输入是合成数据。`memory` 仍会产生外部模型调用费用；后端 `llm_request_timeout=60`、`llm_max_retries=2`，且可能启用模型 fallback，所以一次 Locust 请求可能产生多次上游调用；客户端超时也不保证服务端立即停止。`rag` 需明确测试文档/索引；`agent` 可能触发工具、外部搜索及文件副作用，需额外设置 `LT_ENABLE_AGENT=1`，不得拿它当纯 HTTP 基准。压测机无法拆出供应商耗时；没有应用侧 trace、DB query timing 和 pool metrics 时，报告应明确写“端到端”。

## 结果与跨阶段对比

Locust 的 `--html` 生成标准 HTML；`--csv` 生成请求汇总、逐秒历史和失败 CSV。分析工具读取 `_stats.csv` 的真实统计字段，不从平均值推算分位数；缺失数据会输出 `N/A`。

```powershell
$reportDir = "reports\20261025" # 替换为本轮实际日期目录
.\.venv-loadtest\Scripts\python.exe tools/loadtest/analyze_csv.py "$reportDir\stage-02_stats.csv" "$reportDir\stage-05_stats.csv" "$reportDir\stage-10_stats.csv" --output "$reportDir\comparison.md"
```

输入目录应各含一个 Locust `*_stats.csv`，目录名作为阶段标签。Markdown 包含总请求/成功/失败/失败率、平均值、中位数、P90/P95/P99、RPS、分接口统计及相邻阶段变化。Locust 的统计行按文件中实际存在的请求计数；初始化请求以 `[setup]` 单列。Locust 2.47 的 CSV 快照可能早于 `on_stop` 清理请求，因此 `[cleanup]` 可能只出现在进程结束时的控制台汇总中，需一并核对。比较阶段时保持相同流程/配置。单次阶段耗时太短或请求数不足时，仍可读取原值，但应在结论中说明样本不足，不把高分位当稳定容量承诺。

历史数据是代码仓库中 2026-10-22 报告所记：20 用户约 8,832 请求、失败 0、平均约 161.5ms、P95 约 330ms；40 用户约 13,345 请求、失败 0、平均约 324.5ms、P95 约 650ms。它们是历史测量，不代表当前版本、当前资源配置或最大承载能力。历史报告还记录服务器当时 2 核/约 1.6GB；请以本次服务器 `free -h` 实测为准。

## 服务器资源采集（Linux）

建议压测机与服务器分离。以下命令在**被测 Linux 服务器**执行；轻量、只读命令通常无需 root，进程详情/慢查询需相应权限：

```bash
# CPU/load、内存/Swap、进程 RSS：测试前和每阶段结束各记录一次
date -Is; uptime; free -h; vmstat 1 5
app_pid="$(systemctl show -p MainPID --value job-copilot)"
if [ "$app_pid" -gt 0 ]; then
  pidstat -u -r -d -p "$app_pid" 1 5   # sysstat；systemd MainPID
  ps -o pid,pcpu,pmem,rss,vsz,stat,cmd -p "$app_pid"
fi

# 网络接口计数与 TCP 概况；/proc 只读，ss 低开销
cat /proc/net/dev; ss -s; ss -tan state established | wc -l

# MySQL：使用已配置安全凭证的 mysql client；不要把密码写入命令行
mysql --defaults-extra-file=/secure/path/client.cnf -NBe "SHOW GLOBAL STATUS LIKE 'Threads_connected'; SHOW GLOBAL STATUS LIKE 'Slow_queries';"
mysql --defaults-extra-file=/secure/path/client.cnf -e "SHOW FULL PROCESSLIST"

# 服务日志：按阶段时间窗口观察 timeout/exception/5xx
sudo journalctl -u job-copilot --since "10 minutes ago" --no-pager
```

`pidstat` 依赖 `sysstat`；MySQL `PROCESS` 权限可见更多会话详情。避免在 2GB 服务器上额外部署 Prometheus/Grafana。`vmstat`/`pidstat` 每秒 5 次采样开销低；`SHOW FULL PROCESSLIST` 仅在异常时短暂运行。若需网络速率差值，可间隔 5–10 秒读取 `/proc/net/dev`；不要把累计字节误读成瞬时吞吐。

### 瓶颈排查顺序

- **CPU**：持续 `%usr + %sys` 接近可用核数、run queue/load 长时间高于 2，且 RPS 不再增长而 P95 上升，支持 CPU 饱和假设；分辨 Python/系统态需 `pidstat`/profile 证据。
- **内存/Swap**：`MemAvailable` 持续下降、进程 RSS 增长、`vmstat` 的 `si/so` 非零或 OOM 日志出现，优先检查内存/交换；不要仅凭 `free` 的 `used` 判断。
- **DB/连接池**：MySQL `Threads_connected`、慢查询数、processlist、应用日志同步观察。项目未导出 SQLAlchemy pool checkout/wait 指标，不能只凭连接数断言连接池耗尽；慢查询需 DBA 权限和慢日志配置。
- **网络**：服务 CPU/DB 正常但远程接口延迟高时，对照本机回环与公网、RTT、网卡累计字节及错误日志；HTTP 请求延迟包含客户端到服务器网络耗时。
- **外部模型**：只对比 AI 端到端请求与同时段健康/轻量接口；只有供应商 trace/应用内部阶段计时才能把模型等待从后端/DB 中剥离。禁止从普通接口 P95 推断 LLM 时延。

建议记录人工停止条件：连续 3 次 5xx、最近 50 请求失败率 >10%、滚动 P95 高于预设阈值、超时增加、CPU/内存预警或线上业务告警。Locust 只能按请求错误和延迟自动停止；远端 CPU、内存、MySQL 与网络需人工监看。

## 验证与限制

本目录的 CSV 汇总器和配置可在本机验证；完整压测需要 Locust、可访问的授权目标及有效测试数据/凭据。本次不连接服务器、不调用任何 AI 接口，也不声称线上验证通过。重型业务端点因缺少专用合成简历/职位/知识库夹具而保持未自动化；需先准备独立测试数据和清理/费用方案。

## 报告记录模板

见 [`REPORT_TEMPLATE.md`](REPORT_TEMPLATE.md)，可复制进项目 README 或每次测试报告。
