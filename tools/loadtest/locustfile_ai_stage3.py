"""AI 压测阶段 3：单独测试 RAG 或 Agent（一次只测一种，默认 1 个虚拟用户）。

必须显式选择模式，两种模式各自独立运行、各自出报告：

    # RAG：默认上传一份合成 txt（1 次 embedding），再提问；一次提问约含检索、重排、回答、校验多次模型调用
    $env:LT_ENABLE_AI = "1"; $env:LT_MODE = "rag"
    locust -f tools/loadtest/locustfile_ai_stage3.py --headless --csv reports\\<日期>\\ai-s3-rag --html reports\\<日期>\\ai-s3-rag.html

    # Agent：问题只会触发无副作用的 multiply_tool；若返回了文件产物则记为 E_SIDE_EFFECT
    $env:LT_ENABLE_AI = "1"; $env:LT_ENABLE_AGENT = "1"; $env:LT_MODE = "agent"
    locust -f tools/loadtest/locustfile_ai_stage3.py --headless --csv reports\\<日期>\\ai-s3-agent --html reports\\<日期>\\ai-s3-agent.html

RAG 默认 LT_RAG_SOURCE=upload；设为 builtin 则不上传，只检索内置知识库（无文件写入、无 embedding 上传成本）。
Agent 仍可能因模型自行决策调用其他工具，所以要同时核对服务端日志和 generated_docs 是否新增文件。

用户数默认 1；确认成本、配额和工具副作用可控后，设置 LT_STAGE3_CONFIRM_2_USERS=YES 并
加 --users 2 才允许 2 个用户。任何一次失败或 HTTP 429 立即停止。
"""

from __future__ import annotations

from locust.exception import StopUser

from ai_common import RAG_DOC_NAME, RAG_DOC_TEXT, AiChatUser, Stage, install

STAGE = Stage(
    name="ai-stage3-rag-agent",
    modes=("rag", "agent"),
    min_users=1,
    max_users=2,
    default_users=1,
    default_requests=1,
    max_requests=3,
    default_think=1.0,
    default_run_time="120s",
    stop_after_failures=1,
    health_after=True,
    confirm_above_users=1,
    confirm_env="LT_STAGE3_CONFIRM_2_USERS",
)
SETTINGS = install(STAGE)


def _document_indexed(data: object) -> str | None:
    document = data.get("document") if isinstance(data, dict) else None
    if isinstance(document, dict) and document.get("status") == "indexed":
        return None
    return "E_CONTRACT|document.status"


class RagAgentUser(AiChatUser):
    stage = STAGE
    settings = SETTINGS

    def prepare_conversation(self) -> None:
        if self.settings.mode != "rag" or self.settings.rag_source != "upload":
            return
        reason, _ = self.call_aux(
            "POST",
            "/api/v1/document/upload",
            "[setup] POST /document/upload",
            validate=_document_indexed,
            # 上传含解析、切分与 embedding，给比普通写请求更长的读超时
            timeout=max(self.settings.aux_timeout, 60),
            data={"conversation_id": self.conversation_id},
            files={"file": (RAG_DOC_NAME, RAG_DOC_TEXT.encode("utf-8"), "text/plain")},
        )
        if reason is not None:
            raise StopUser()
