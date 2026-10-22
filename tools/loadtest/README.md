# 压测工具（tools/loadtest）

三个脚本分工明确，**别用错**：

| 文件 | 压什么 | 报什么 | 花 token |
|---|---|---|---|
| `sanity.py` | 1 个请求，探路 | 能不能通、回答是否正常、单请求耗时、建议阶梯 | 少量 |
| `locustfile.py` | `/conversation/chat`（LLM 链路） | 并发 N 时用户感知的 P95 与错误率 | 有 |
| `locustfile_data.py` | 纯数据接口（list/detail/skills/health） | **服务端 QPS / P95 / 错误率** | 0 |
| `cleanup_loadtest.py` | — | 清理压测产生的会话与产物 | 0 |

**已归档的历史报告**：`reports/2026-10-22/`（含逐秒原始采样、口径说明与面试讲法）。
写简历的 QPS 只能来自 `locustfile_data.py`。`locustfile.py` 的耗时 99% 在等百炼，
反映的是供应商排队情况，不是后端能力。

## 跑法（按顺序）

```bash
VENV=D:/dshcache/loadtest-venv/Scripts

# 1) 探路：3 次取中位数，确认不是兜底文案、没有产物文件，并打印建议阶梯
$VENV/python.exe tools/loadtest/sanity.py https://displaydemo.top

# 2) 主轮：纯数据接口，4 分钟
$VENV/locust.exe -f tools/loadtest/locustfile_data.py \
    --headless --host https://displaydemo.top --csv reports/data --html reports/data.html

# 3) 副轮：LLM 链路，18 分钟（想缩短就改 LT_STAGES）
$VENV/locust.exe -f tools/loadtest/locustfile.py \
    --headless --host https://displaydemo.top --csv reports/llm --html reports/llm.html

# 4) 清理（默认 dry-run，先看行数）
$VENV/python.exe tools/loadtest/cleanup_loadtest.py
$VENV/python.exe tools/loadtest/cleanup_loadtest.py --yes --confirm-db llmrag
```

`--csv` 是为了按 `E_` 前缀拆错误类型：`E_FALLBACK`（模型没作答但接口回 success）、
`E_EMPTY`、`E_BUSINESS`、`E_DEGRADED`、`E_HTTP`。总错误率混在一起看没有意义。

## 模型：访客白名单（2026-10-22 已换模型）

`backend/app/core/model_policy.py` 的 `USER_SELECTABLE_MODELS` 是写死的产品决策
（不在 Settings 里）：

```
qwen3.8-flash（默认） / qwen3.8-omni-flash / qwen-plus
```

`qwen-flash` 免费额度已耗尽，已从白名单和降级链移除，换成上面两个模型
（各自有独立免费额度）。所以要压 qwen3.8-flash **不需要管理员 token 了**，
空手跑就是访客真实路径。

只有压 `qwen3.8-max` / `deepseek-*` / `kimi-k3` 这类管理专属模型才需要：

```bash
export LT_TOKEN="$(浏览器控制台 localStorage.getItem('jc_admin_token'))"
```

⚠ 这套改动要**重新发布到线上**才生效。发布前的线上仍是旧白名单（qwen-flash 已经没额度），
未经发布就压 LLM 轮，每个请求都会先撞一次额度错误再降级，P95 和错误率都不作数。

## 成本与耗时（2026-10-22 实测）

**qwen3.8-flash 单请求实测**（`F:\dshcache\measure_tokens.py`，qa_prompt 原文）：
输入 111 tokens / 输出 75 tokens / 1.64s。这一代模型在本项目这条短 prompt 上
**几乎不产生思考 token**（`reasoning_content` 为 0 字），别按"开思考贵 3 倍"去估。

牌价 0.8 / 2.7 元每百万 tokens（`backend/app/core/config.py`，百炼 `GET /api/v1/models`）。

| 轮次 | 请求数 | tokens | 费用 | 耗时 |
|---|---|---|---|---|
| 数据接口 2:40,5:40,10:40,20:40（实测） | 8,832 | 0 | 0 元 | 2.7 min |
| LLM（qwen3.8-flash）1:120,2:120,4:120,8:180 | ≈ 900 | ≈ 170k | **≈ 0.27 元** | 9 min |

单请求按 2.0~2.5s（含建会话与两次 DB 落库）估，实际请求数随服务器排队上下浮动。

**额度才是天花板**：qwen3.8-flash 剩 843.19K（2026-10-22 控制台）。上面这轮约吃 **20%**。
留余量的原因：额度耗尽时工厂会静默降级到链上下一个模型，P95 会突变，
而**降级不置 degraded 标记**，报告看不出来。

## 已跑出的基线（2026-10-22 22:37，线上，访客身份）

| 接口 | 请求数 | 中位 | P95 | P99 | 失败 |
|---|---|---|---|---|---|
| GET /conversation/list | 4,047 | 150ms | 350ms | 410ms | 0 |
| GET /conversation/detail | 3,086 | 140ms | 320ms | 380ms | 0 |
| GET /skills | 795 | 120ms | 290ms | 350ms | 0 |
| GET /system/health | 804 | 81ms | 140ms | 160ms | 0 |
| 汇总（20 并发上限） | 8,832 | 140ms | 330ms | 390ms | 0（0.00%） |

**这组数字含客户端到北京服务器的网络 RTT**，不是纯服务端耗时。
`/system/health` 几乎不碰数据库，可以当基线：85ms。扣掉它，
`/conversation/list` 的服务端增量约 93ms、`/conversation/detail` 约 79ms。
报数字时要把这层说清楚，否则会被当成服务端慢。

## 压测前必查

1. 服务器是 2 核 1.6G 单机、`workers=1`、sync 路由走 anyio 线程池（默认 40 线程）。
   **理论并发上限 ≈ 40，QPS 上限 ≈ 40 / 单请求耗时**，压到 40 以上测的是排队长度。
2. 模型额度是否还够（见上）。
3. 压的是线上生产库，`lt-session` 会话会真写进去，跑完记得跑清理脚本。
4. 数据轮的 `detail` 打的是空会话，比真实会话轻，是**乐观下限**。
