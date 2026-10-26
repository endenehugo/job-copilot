# 2026-10-26 服务器容量测试（同机压测）

## 一句话

单进程后端的吞吐天花板是 **69-70 RPS**，安全水位 **70 RPS（P95 52ms）**。
超过之后吞吐不涨、延迟从 52ms 跳到 820ms——瓶颈是 **backend 单核打满**，
不是 MySQL、不是内存、也不是网络。

## 方法

按新口径（2026-10-26 用户指定）：压测脚本跑在服务器本机，locust 直连 `127.0.0.1:8000`，
放开限速压容量。场景用 `locustfile_data.py --scenario basic`（list / detail / skills / health
四个无模型调用的接口），不烧模型额度，可以反复压。

每档 60 秒，同轮用 `server_monitor.py` 每秒采样，按进程组分开记 CPU。

```bash
LT_MAX_USERS=200 LT_MAX_ALLOWED_RPS=1000 LT_MAX_SPAWN_RATE=50 LT_MAX_DURATION_S=300 \
LT_CONFIRM_40_USERS=YES LT_ABORT_P95_MS=0 \
/opt/loadtest-venv/bin/locust -f /opt/job-copilot/tools/loadtest/locustfile_data.py \
  --headless --host http://127.0.0.1:8000 --scenario basic \
  --users <N> --spawn-rate 20 --run-time 60s --max-rps <目标RPS> --csv cap<N>
```

四个安全上限的放宽读环境变量，默认值不变（`support.py` 的 `_limit_env`）；
放宽时 test_start 会打一条 warning 列出被改的项。

## 结果

| 目标 RPS | 实际 RPS | P50 | P95 | max | backend CPU 均值 / 峰值 | 系统 CPU 均值 | load1 峰值 |
|---|---|---|---|---|---|---|---|
| 50 | 49.6 | **14 ms** | **15 ms** | 380 ms | 28.8% / 52% | 29.2% | 0.69 |
| 70 | 68.4 | **14 ms** | **52 ms** | 990 ms | 44.7% / 94% | 45.6% | 2.06 |
| 100 | 69.9 | 820 ms | 1200 ms | 2200 ms | 76.8% / **101%** | 67.4% | 3.62 |
| 150 | 68.2 | 1500 ms | 1900 ms | 3500 ms | 78.1% / 98% | 69.6% | 3.29 |

（CPU 以「100% = 满载一个核」计，2 核机器理论上限 200%。）

看三件事：

1. **吞吐封顶在 69-70 RPS**。目标 100 和 150 都只跑到 ~68-70，不是压测工具限速（`--max-rps` 分别设的 100/150）。
2. **延迟在 70 RPS 之后断崖**。P95 从 15ms（50 RPS）→ 52ms（70 RPS）→ 1200ms（100 RPS）→ 1900ms（150 RPS）。
3. **backend 在 100 RPS 时单核 100%**，而 mysqld 只有 25.5%、可用内存 428MB、swap 0、nginx 0%。

## 结论

**瓶颈是单 worker + GIL。** 后端是 `uvicorn` 单进程（`deploy/job-copilot.service` 没配 `--workers`），
Python 同一时刻只有一个线程能跑字节码，所以哪怕机器有 2 个核，backend 最多吃满 1 个。
100 RPS 那轮 backend 峰值 101%、系统总 CPU 只有 67.4%——**另一半 CPU 一直闲着**。

`[setup] POST /conversation/create` 在 150 RPS 那轮 P50 440ms、max 2500ms，看着像 DB 慢，
但同一时刻 `/skills`（纯静态、不碰 DB）也到了 780ms，说明是**事件循环/线程池在排队**，不是 SQL 慢。

## 三点必须声明的偏差

1. **同机压测**：locust 自己占 16.5%-21.5% CPU（约 0.2 个核），和 backend 抢同一台机器。
   真实容量应该略高于 69 RPS，但不会差一个量级。
2. **压测期间线上会变慢**：100/150 两档那两分钟，P50 到 820-1500ms。如果这段时间有真人访问，
   会有明显感知。这是压容量的固有代价，应该挑低峰做。
3. **测的是轻接口**。AI 场景（memory 2.2s / RAG 8.4s / Agent 22.6s）根本压不出 RPS 拐点——
   它每秒最多处理 0.5 个请求，瓶颈完全在上游模型。**69 RPS 这个数字不能用来描述 AI 场景的容量。**

## 下一步的三个选项

### A. 加 uvicorn worker（2 个），理论上翻倍

收益明确：backend 能用上第二个核。但会把几处**进程内状态**打散，得先处理：

| 影响点 | 后果 | 严重度 |
|---|---|---|
| `services/question_bank.py` 的 `bank_lock` | 多 worker 各有一把锁，读改写不再互斥 → 丢题、游标回退 | **高（数据一致性）** |
| `context_compression_scheduler` 的 `pending` 去重 | 同会话可能被压两次；写库有乐观锁兜底，最多浪费一次调用 | 低 |
| `build_chat_llm` 的 lru_cache | 单例变成每进程一份，内存 +171MB/worker | 低 |
| 模型降级粘性 | 额度耗尽后各 worker 各自切换，行为不一致 | 中 |

**我建议暂时不加。** `bank_lock` 那条是数据一致性问题，不是性能问题，用性能收益去换它不划算。
真要加，先把 question_bank 的锁改成 DB 行锁（`SELECT ... FOR UPDATE`）。

### B. 接受 70 RPS 作为容量结论

70 RPS 意味着**每秒 70 个并发用户各点一次**。演示站的真实访问量（面试官、HR 偶尔点开）
离这个数量级差 3 个数量级以上。先确认一件事：**这个站需要扛多少 RPS？**
如果要的是"简历上写个容量数字"，70 RPS 配上"单核已满、理论可翻倍"的解释，比硬加 worker 更站得住。

### C. 优化单请求耗时，让一个核扛更多

70 RPS 时 backend 只用了 44.7%，说明还有空间——那 100 RPS 时为什么掉？因为请求是突发的
（`--max-rps` 只是限速上限，实际到达不均匀）+ 同步端点占满线程池。
可以先看 `anyio` 线程池大小与 `SQLAlchemy` 连接池是否偏小，但这是细活，收益不如 A 明确。

## 产物

| 文件 | 内容 |
|---|---|
| `cap{50b,70,100,150b}_stats.csv` / `.html` | Locust 原始统计 |
| `server-cap{50b,70,100,150b}_server.csv` | 对应每秒资源采样 |
| `SUMMARY-20261026.md` | 之前 memory 场景的现状总结 |

## 复现注意

- 每轮必须换 `--csv` 前缀，`server_monitor.py` 前缀存在会拒绝覆盖。
- `--confirm-40-users`（或 `LT_CONFIRM_40_USERS=YES`）是独立于 `LT_MAX_USERS` 的第二道门，用户数超 40 时两个都要给。
- `LT_ABORT_P95_MS=0` 关闭 P95 熔断，否则高负载下会被安全停止误杀；`LT_ABORT_FAILURE_RATE` 建议保留 0.05 防真挂。
- 压完确认 `health` 回到个位数毫秒再收工（本次压完 2-7ms，load1 从 3.6 回落到 1.5）。
