"""AI 压测阶段 1：memory 模式冒烟（1 个虚拟用户 × 1 次 chat）。

目的：只验证三件事，不衡量容量——
1. 模型调用：回答非空、不是兜底文案、未降级；
2. 响应结构：信封 code/message、data 的 answer/conversation/sources/files 字段、消息已落库；
3. 超时处理：客户端读超时被记为 E_TIMEOUT、耗时超过预算被记为 E_SLOW，
   且结束后 /system/health 仍正常（说明服务没有被卡死）。

用户数固定为 1、每用户固定 1 次请求；任何一次失败立即停止。确认本阶段稳定后再运行阶段 2。

    $env:LT_ENABLE_AI = "1"
    locust -f tools/loadtest/locustfile_ai_stage1.py --headless --csv reports\\<日期>\\ai-s1 --html reports\\<日期>\\ai-s1.html
"""

from __future__ import annotations

from ai_common import AiChatUser, Stage, install

STAGE = Stage(
    name="ai-stage1-memory-smoke",
    modes=("memory",),
    min_users=1,
    max_users=1,
    default_users=1,
    default_requests=1,
    max_requests=1,
    default_think=1.0,
    default_run_time="120s",
    stop_after_failures=1,
    health_after=True,
)
SETTINGS = install(STAGE)


class MemorySmokeUser(AiChatUser):
    stage = STAGE
    settings = SETTINGS
