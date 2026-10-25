"""AI 压测阶段 2：memory 模式低并发（2 个虚拟用户，每人最多 5 次 chat）。

前提：阶段 1 已稳定通过。本阶段观察：
- chat 行的 P95 与错误率（读 `POST /conversation/chat [memory]` 行，不要看含 setup/cleanup/probe 的 Aggregated 行）；
- 模型超时：失败分类里的 E_TIMEOUT / E_SLOW / E_FALLBACK，以及脚本汇总中的“超过预算”次数；
- 服务器资源：`[probe] GET /system/health` 每 5 秒一次，模型请求占满后端线程时它的延迟会先变差；
  CPU/内存/MySQL 需在服务器上按 README“服务器资源采集”另行记录。

用户数固定为 2；连续 2 次失败或收到 HTTP 429 立即停止。每人请求数用
LT_AI_MAX_REQUESTS_PER_USER 调整（1–10），默认 5，样本仍少，分位数仅供参考。

    $env:LT_ENABLE_AI = "1"
    locust -f tools/loadtest/locustfile_ai_stage2.py --headless --csv reports\\<日期>\\ai-s2 --csv-full-history --html reports\\<日期>\\ai-s2.html
"""

from __future__ import annotations

from ai_common import AiChatUser, Stage, install

STAGE = Stage(
    name="ai-stage2-memory-low-concurrency",
    modes=("memory",),
    min_users=2,
    max_users=2,
    default_users=2,
    default_requests=5,
    max_requests=10,
    default_think=3.0,
    default_run_time="120s",
    stop_after_failures=2,
    health_after=True,
    health_probe_interval=5.0,
)
SETTINGS = install(STAGE)


class MemoryLowConcurrencyUser(AiChatUser):
    stage = STAGE
    settings = SETTINGS
