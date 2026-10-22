"""locust 压测：POST /api/v1/conversation/chat（LLM 链路）

⚠ 这一轮的 QPS 主要受百炼配额和模型速度限制，**不代表后端能力**。
   要报"服务端 QPS / P95"，跑 locustfile_data.py（纯数据接口）。
   这里只报「并发 N 时，用户感知的 P95 与错误率」。

设计取舍（改之前先读这几条，都是踩过的地方）：

1) 成败必须按信封 code 判，不看 HTTP 状态码。
   core/response.py::fail 与 core/exceptions.py 的全局 handler 一律返回
   HTTP 200 + {"code": "..."}。默认的 "status < 400 即成功" 会把所有业务失败
   都算成功，错误率恒为 0，这份报告就是废的。

2) **光判 code 不够，还要判回答内容**（2026-10-22 加的，这条最容易被忽略）。
   conversation_chat_service.py:252 是 `answer = answer or self._fallback_answer(...)`：
   模型返回空内容时不是抛错，而是**换一段人话兜底**，此时
   HTTP 200 / code=success / degraded=False。而空回答比正常回答更快（0 token 输出），
   所以一旦模型抽风，报告会显示「错误率 0%、P95 反而下降」。必须按兜底文案特征串判失败。

3) 默认每次请求新建会话（LT_REUSE=0）。
   复用会话时 _prepare_chat 会把历史消息拼进 prompt，token 线性增长，
   P95 会随时间漂移；消息超过 20 条还会触发 context_compression 再调一次 LLM。
   新建会话让 history 恒为空，每一轮口径一致、可复现。

4) 默认 mode=memory。
   memory 跳过分区检索（context=""）和 done 之后那次 LLM 校验，只剩一次 LLM
   调用，方差最小。agent 才是前端主链路，但会 bind_tools、可能多轮，方差大
   且成本翻倍——要看真实体验请单独跑一轮 LT_MODE=agent。
   实测 prompt 体量：memory 91 字符、agent 1615 字符 + 10 个工具 schema（1056 字符）。

5) 访客身份不用手工处理。
   VisitorMiddleware 懒签发 jc_visitor，HttpUser 自带 requests.Session，
   首次请求拿到 cookie 后 create/chat 都会带上。
   注意：访客角色默认模型是 qwen-flash（见 core/model_policy.py），
   不是管理员的 qwen3.8-max。也就是说不带 admin token 压的就是线上访客真实路径。

6) `-u` 就是并发数，不是 QPS。
   wait_time 只有 0.1~0.3s，而一次请求要几秒，所以每个用户在等待时间之外
   几乎全程占着一条连接：并发 ≈ 用户数，QPS ≈ 用户数 / 单请求耗时。

常用环境变量：
  LT_MODE      memory | agent          默认 memory
  LT_QUERY     提问内容                 默认一句短问，控制 output token
  LT_REUSE     1 复用会话               默认 0
  LT_MODEL     指定模型（访客仅 qwen-flash / qwen-plus）
  LT_TIMEOUT   客户端读超时秒数          默认 200
  LT_STAGES    阶梯 "1:300,2:300"       off 关闭阶梯（改用 -u/-r/-t）

跑法：
  D:/dshcache/loadtest-venv/Scripts/locust.exe -f tools/loadtest/locustfile.py \
      --headless --host https://displaydemo.top --csv reports/llm --html reports/llm.html

**样本量口径**（决定 P95 能不能报）：
  单请求约 6~8s（memory），所以每档样本数 ≈ 档位秒数 / 7 × 并发数。
  默认 1:300,2:300,4:240,8:240 → 约 43 / 86 / 137 / 274 个样本。
  **1 并发档（43 个样本）的 P95 实际接近最大值，不要写进报告**；
  4 并发及以上的档位样本数够，P95 才可引用。
"""

from __future__ import annotations

import os

from locust import HttpUser, LoadTestShape, between, task

MODE = os.getenv("LT_MODE", "memory")
QUERY = os.getenv("LT_QUERY", "用一句话回答：Python 的 GIL 是什么？")
REUSE_SESSION = os.getenv("LT_REUSE", "0") == "1"
MODEL = os.getenv("LT_MODEL", "").strip()
# 管理员 token（浏览器 localStorage 的 jc_admin_token，或 /api/v1/auth/login 返回）。
# 为什么需要：访客角色白名单只有 qwen-flash / qwen-plus（core/model_policy.py 的
# USER_SELECTABLE_MODELS，刻意写死为产品决策），压 qwen3.8-flash 这类模型会被
# 服务端 400 拒掉。带 token 后 role=admin，可选清单放开。
# 注意：admin 的会话 owner_key='admin'，清理时要加 --any-owner。
TOKEN = os.getenv("LT_TOKEN", "").strip()
# 与 backend/app/core/model_policy.py 的 USER_SELECTABLE_MODELS 保持同步。
# locust 的 venv 里没有后端依赖，不能 import，只能手抄一份。
VISITOR_MODELS = ("qwen3.8-flash", "qwen3.8-omni-flash", "qwen-plus")
CREATE_TITLE = "lt-session"
# 少于这个字数就认为模型没真正作答（正常的"一句话回答"也在 20 字以上）
MIN_ANSWER_CHARS = int(os.getenv("LT_MIN_ANSWER", "5"))

# _fallback_answer 的两段兜底文案。命中即说明模型没作答，但接口照样回 success。
FALLBACK_MARKERS = ("这次模型没有返回内容", "这次没能给出完整回答")

# 后端单次请求最坏路径 = llm_request_timeout(60) × (1 + llm_max_retries(2))，
# 客户端必须留在这之上，否则会把「上游重试导致的慢」误记成「客户端超时」。
# 拆成 (connect, read)：连不上和读超时在报告里是两类问题，不该混。
CONNECT_TIMEOUT = float(os.getenv("LT_CONNECT_TIMEOUT", "5"))
TIMEOUT = float(os.getenv("LT_TIMEOUT", "200"))


def _body(resp):
    """取 JSON 信封；非 JSON（502 HTML、网关错误页）返回 None。"""
    try:
        return resp.json()
    except Exception:
        return None


def _failure_reason(resp, body) -> str:
    """失败原因带 HTTP 状态码：502 网关错误和业务失败在报告里必须能分开。"""
    status = getattr(resp, "status_code", "?")
    if body is None:
        return f"E_HTTP|{status}|非 JSON 响应（网关或 5xx）"
    code = body.get("code")
    msg = str(body.get("message") or "")[:60]
    return f"E_BUSINESS|{status}|{code}|{msg}"


def _judge(resp, body, *, expect_answer: bool) -> str | None:
    """返回失败原因；None 表示这次请求真的成功。"""
    if body is None or body.get("code") != "success":
        return _failure_reason(resp, body)

    if expect_answer:
        answer = str((body.get("data") or {}).get("answer") or "").strip()
        if len(answer) < MIN_ANSWER_CHARS:
            return f"E_EMPTY|answer 只有 {len(answer)} 字"
        for marker in FALLBACK_MARKERS:
            if marker in answer:
                return f"E_FALLBACK|命中兜底文案：{marker}"

    if body.get("degraded"):
        # 结果产出了但可信度受损（模型降级、工具链异常）。单独计一类，
        # 报告里按 E_ 前缀拆开看，别并进总错误率当成"系统挂了"。
        warnings = body.get("warnings") or []
        return f"E_DEGRADED|{str(warnings[0])[:60] if warnings else 'degraded'}"
    return None


class ChatUser(HttpUser):
    # 响应本身十几秒，这点等待对并发无实际影响，只是避免同一用户零间隔连发
    wait_time = between(0.1, 0.3)

    def on_start(self) -> None:
        # 本机 shell 里带着 http_proxy/https_proxy，requests 默认 trust_env=True
        # 会把压测流量全塞进代理，表现为大面积连接失败或凭空多出几百毫秒。
        # 压测要的是直连口径，这里统一关掉。
        self.client.trust_env = False
        self.cid = ""
        if TOKEN:
            # 带管理员身份：模型白名单放开（qwen3.8-flash 等只对 admin 开放）
            self.client.headers.update({"X-Admin-Token": TOKEN})
            print(f"[setup] 使用管理员身份压测，模型={MODEL or 'admin 默认(settings.llm_model)'}")
        elif MODEL and MODEL not in VISITOR_MODELS:
            print(f"[setup] ⚠ LT_MODEL={MODEL} 不在访客白名单内，服务端会回 400。"
                  f"请同时提供 LT_TOKEN。")
        # REUSE=1 才在这里建会话；REUSE=0 时每次 task 都会重建，
        # 在这里建只会多一个永远用不上的空会话（还多一次 create 请求）。
        if REUSE_SESSION:
            self._create_conversation()

    def _create_conversation(self) -> bool:
        with self.client.post(
            "/api/v1/conversation/create",
            json={"title": CREATE_TITLE, "mode": MODE},
            name="[setup] /conversation/create",
            timeout=(CONNECT_TIMEOUT, 30),
            catch_response=True,
        ) as resp:
            body = _body(resp)
            reason = _judge(resp, body, expect_answer=False)
            if reason is not None:
                resp.failure(reason)
                self.cid = ""
                return False
            self.cid = body["data"]["conversation_id"]
            return True

    @task
    def chat(self) -> None:
        if not REUSE_SESSION or not self.cid:
            if not self._create_conversation():
                return  # 建会话失败已经记 failure，这一轮不硬发请求

        payload = {"conversation_id": self.cid, "query": QUERY, "mode": MODE}
        if MODEL:
            payload["model"] = MODEL

        with self.client.post(
            "/api/v1/conversation/chat",
            json=payload,
            name=f"chat[{MODE}]",
            timeout=(CONNECT_TIMEOUT, TIMEOUT),
            catch_response=True,
        ) as resp:
            body = _body(resp)
            reason = _judge(resp, body, expect_answer=True)
            if reason is not None:
                resp.failure(reason)


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
    # 每档时长按「样本量」推出来的，不是拍脑袋：
    #   memory 单请求约 6~8s，样本数 ≈ 秒数 / 7 × 并发。
    #   1 并发 300s → 约 43 个，只够看冷启动，P95 不作数；
    #   2 并发 300s → 约 86 个，勉强；
    #   4 并发 240s → 约 137 个；8 并发 240s → 约 274 个，这两档的 P95 可引用。
    # agent 模式单请求 20~40s，要拿到同样样本量需把秒数整体 ×3~4。
    os.getenv("LT_STAGES", "1:300,2:300,4:240,8:240")
)

if _STAGES:

    class StepShape(LoadTestShape):
        """并发阶梯：每档跑固定时长，看 P95 与错误率随并发怎么变。

        spawn_rate 固定 1/秒（不是瞬间拉满），避免新建用户齐发造成一个假的延迟尖峰。
        """

        stages = _STAGES

        def tick(self):
            elapsed = self.get_run_time()
            start = 0
            for users, duration in self.stages:
                if elapsed < start + duration:
                    return users, 1
                start += duration
            return None
