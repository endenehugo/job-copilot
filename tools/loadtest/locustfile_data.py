"""locust 压测：纯数据接口（不含任何 LLM 调用）

存在的理由：locustfile.py 压的是 /conversation/chat，那条链路的耗时 99% 花在等百炼，
测出来的是供应商的排队情况，**不能作为后端能力的证据**。
要报「服务端 QPS / P95 / 错误率」，就跑这个文件——它只经过
中间件 → 依赖注入 → service → repository → MySQL，全部是你自己写的代码。

覆盖的接口（都是访客身份可用的只读端点，不烧模型额度）：
  GET /api/v1/conversation/list      列表（带 pinned_at 排序 + owner_key 过滤）
  GET /api/v1/conversation/detail    详情（会话 + 消息 + 元信息）
  GET /api/v1/skills                 技能清单（读 SKILL.md frontmatter）
  GET /api/v1/system/health          健康检查（最轻，用作基线）

口径提醒：
1) 后端 workers=1，路由是 sync 的，FastAPI 交给 anyio 线程池执行，默认 40 个线程。
   所以理论并发上限 ≈ 40，QPS 上限 ≈ 40 / 单请求耗时。
   并发数低于 40 时测的是「并发下的 P95」，高于 40 时测的是「排队有多长」——
   两者都要报，且必须写清是哪一段。
2) 默认 wait_time=0（压满）。想看「真实用户节奏」就设 LT_WAIT=0.5~2。
3) 会话是 on_start 建的**空会话**，detail 会跳过消息反序列化的开销，
   测出来是乐观下限。要更真实的数字，先手工灌 10 条消息再压。
4) 压的是线上单机。从 2:60 起步，别一上来就 40 并发——这台机器同时在对外提供服务。

常用环境变量：
  LT_SEED      on_start 建几个会话       默认 5
  LT_WAIT      每次请求后等待秒数        默认 0
  LT_STAGES    阶梯 "2:60,5:60"         off 关闭（改用 -u/-r/-t）

跑法：
  D:/dshcache/loadtest-venv/Scripts/locust.exe -f tools/loadtest/locustfile_data.py \
      --headless --host https://displaydemo.top --csv reports/data --html reports/data.html
"""

from __future__ import annotations

import os

from locust import HttpUser, LoadTestShape, constant, task

SEED_COUNT = int(os.getenv("LT_SEED", "5"))
WAIT = float(os.getenv("LT_WAIT", "0"))
CONNECT_TIMEOUT = float(os.getenv("LT_CONNECT_TIMEOUT", "5"))
TIMEOUT = float(os.getenv("LT_TIMEOUT", "15"))
# 可选：带管理员身份压测（能压到只有 admin 可见的路径）。
# 不带就是访客，主人群路径，也是线上真实流量的样子。
TOKEN = os.getenv("LT_TOKEN", "").strip()

# 清库脚本按这个标题匹配，别改。
SEED_TITLE = "lt-session"


def _body(resp):
    try:
        return resp.json()
    except Exception:
        return None


def _judge(resp, body) -> str | None:
    """只判信封。数据接口没有 degraded/answer 之说。"""
    status = getattr(resp, "status_code", "?")
    if body is None:
        return f"E_HTTP|{status}|非 JSON 响应（网关或 5xx）"
    code = body.get("code")
    if code != "success":
        msg = str(body.get("message") or "")[:60]
        return f"E_BUSINESS|{status}|{code}|{msg}"
    return None


class DataUser(HttpUser):
    wait_time = constant(WAIT)

    def on_start(self) -> None:
        self.client.trust_env = False
        if TOKEN:
            self.client.headers.update({"X-Admin-Token": TOKEN})
        self.cids: list[str] = []
        for _ in range(max(1, SEED_COUNT)):
            with self.client.post(
                "/api/v1/conversation/create",
                json={"title": SEED_TITLE, "mode": "memory"},
                name="[setup] /conversation/create",
                timeout=(CONNECT_TIMEOUT, 30),
                catch_response=True,
            ) as resp:
                body = _body(resp)
                reason = _judge(resp, body)
                if reason is not None:
                    resp.failure(reason)
                    continue
                self.cids.append(body["data"]["conversation_id"])

    def _get(self, url: str, name: str, params: dict | None = None) -> None:
        with self.client.get(
            url,
            name=name,
            params=params,
            timeout=(CONNECT_TIMEOUT, TIMEOUT),
            catch_response=True,
        ) as resp:
            reason = _judge(resp, _body(resp))
            if reason is not None:
                resp.failure(reason)

    @task(5)
    def list_conversations(self) -> None:
        self._get("/api/v1/conversation/list", "GET /conversation/list", {"limit": 50})

    @task(4)
    def conversation_detail(self) -> None:
        if not self.cids:
            return
        cid = self.cids[0]
        self._get(
            "/api/v1/conversation/detail",
            "GET /conversation/detail",
            {"conversation_id": cid},
        )

    @task(1)
    def skills(self) -> None:
        self._get("/api/v1/skills", "GET /skills")

    @task(1)
    def health(self) -> None:
        self._get("/api/v1/system/health", "GET /system/health")


def _parse_stages(raw: str) -> list[tuple[int, int]]:
    stages = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        users, _, duration = part.partition(":")
        stages.append((int(users), int(duration or 60)))
    return stages


_STAGES = [] if os.getenv("LT_STAGES", "").strip().lower() == "off" else _parse_stages(
    # 数据接口单请求 10~50ms，60s 一档就有上千个样本，P95 足够稳。
    # 上限受线程池（40）限制，所以阶梯要跨过 40 才能看到排队拐点；
    # 4 档默认到 20，先看拐点在哪，再决定要不要上 40。
    os.getenv("LT_STAGES", "2:60,5:60,10:60,20:60")
)

if _STAGES:

    class StepShape(LoadTestShape):
        """并发阶梯。spawn_rate 1/秒，避免齐发造成假尖峰。"""

        stages = _STAGES

        def tick(self):
            elapsed = self.get_run_time()
            start = 0
            for users, duration in self.stages:
                if elapsed < start + duration:
                    return users, 1
                start += duration
            return None
