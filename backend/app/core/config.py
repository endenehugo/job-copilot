"""应用配置：pydantic-settings 统一管理。

优先级：进程环境变量 > backend/.env > 代码默认值。
加载 .env 后会把 DashScope / Metaso 密钥回灌到进程环境变量，
因为 langchain-community 与工具实现直接从 os.environ 读取它们。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[3]

# —— 可更换的模型清单 ——
# 换主模型：把 LLM_MODEL 设成清单里的任意一个即可（也可用 LLM_SELECTABLE_MODELS 整体覆盖）。
# 统一用根目录 .env 里的同一个 DASHSCOPE_API_KEY 调用（deepseek / moonshot 是百炼托管的
# 第三方模型，不需要额外 Key）；均已核对支持 function-calling。
# 价格单位「元/百万 tokens（输入/输出）」，取自百炼 GET /api/v1/models，仅作选型参考。
#
# 端点差异（2026-10-12 实测）：百炼把这一代新模型（3.x/3.8 与第三方模型）挂在原生
# multimodal-generation 端点，而 langchain_community 的 ChatTongyi 用一份内置白名单
# 决定端点（只认含 "vl" 的模型），把它们发到 text-generation 端点就会报
# `InvalidParameter: url error`。因此下面 COMPATIBLE_MODE_MODELS 里的模型改走百炼
# OpenAI 兼容模式（对老模型同样可用），由 llm_factory 按模型自动选择客户端。
SELECTABLE_LLM_MODELS: list[str] = [
    # 走 OpenAI 兼容模式的这一代新模型
    "qwen3.8-max",  # 12 / 36     旗舰：深度思考 + 视觉理解
    "qwen3.8-flash",  # 0.8 / 2.7   最便宜，适合压缩/校验/重排等高频任务
    "qwen3.8-omni-flash",  # 价格未在 /api/v1/models 返回；全模态，2026-10-22 实测兼容模式可用
    "qwen3.8-2.4t-a95b",  # 12 / 36     超大参数（2.4T 总参 / A95B 激活），纯文本
    "qwen3.7-flash",  # 2026-10-22 加入：对普通用户开放
    "deepseek-v4.1-flash",  # 1 / 4       第三方（DeepSeek），1M 上下文，性价比高
    "kimi-k3",  # 20 / 100    第三方（Moonshot），深度思考 + 视觉理解
    # 走 ChatTongyi 原生端点的老模型
    "qwen-plus",  # 4.8 / 48    老一代主模型
    # qwen-flash（1.2 / 12）已于 2026-10-22 免费额度耗尽，从清单移除；
    # 换成 qwen3.8-flash + qwen3.8-omni-flash 两个模型（各自有独立免费额度）。
]

# 需要走百炼 OpenAI 兼容模式的模型：原生 text-generation 端点不提供它们。
# 与 SELECTABLE_LLM_MODELS 的交集即「新模型」；老模型留在 ChatTongyi 原生链路，
# 避免无谓改动现网已验证的问答/流式路径。
COMPATIBLE_MODE_MODELS: list[str] = [
    "qwen3.8-max",
    "qwen3.8-flash",
    # 2026-10-22 实测：这个 ID 走原生端点会报 400 url error（ChatTongyi 白名单只认含 vl 的名字），
    # 走兼容模式端点正常返回。它也不在 GET /api/v1/models 的返回里，所以不能只凭清单判断可用性。
    "qwen3.8-omni-flash",
    "qwen3.8-2.4t-a95b",
    "qwen3.7-flash",
    "deepseek-v4.1-flash",
    "kimi-k3",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 同时支持仓库根与 backend/ 下的 .env，避免依赖启动时的工作目录
        env_file=(_REPO_ROOT / ".env", _BACKEND_ROOT / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    env: str = Field(default="dev", validation_alias="APP_ENV")
    port: int = 8000
    log_level: str = "INFO"

    dashscope_api_key: str = ""
    metaso_api_key: str = ""

    llm_model: str = "qwen3.8-max"
    multimodal_model: str = "qwen-vl-plus"

    # 可更换的模型清单：换主模型/小模型时从这里挑，改 LLM_MODEL 即可，无需动代码。
    # 用 LLM_SELECTABLE_MODELS 可整体覆盖（JSON 数组），便于按免费额度余量临时收敛候选集。
    llm_selectable_models: list[str] = list(SELECTABLE_LLM_MODELS)

    # 其中需要走 OpenAI 兼容模式的模型（原生 text-generation 端点不提供这一代新模型）。
    # 链上只要有一个模型命中，整条链都用兼容模式客户端——兼容模式对老模型同样可用。
    llm_compatible_mode_models: list[str] = list(COMPATIBLE_MODE_MODELS)
    llm_compatible_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 第三方模型的采样参数限制（2026-10-12 实测：Moonshot 的 kimi-k3 只接受
    # temperature=1，且完全不支持 top_p，否则 400 InvalidParameter）。
    # 工厂据此改写 temperature、丢弃 top_p，避免调用方固定传 0.7/0.8 时直接失败。
    llm_fixed_temperature_models: dict[str, float] = {"kimi-k3": 1.0}
    llm_models_without_top_p: list[str] = ["kimi-k3"]

    # 简单/结构化任务（上下文压缩、事实校验、检索重排）使用的小模型。
    # 百炼免费额度按模型独立发放（每模型约 100 万 token / 90 天），
    # 把低价值高频调用分流到独立模型上，等价于免费额度扩容。
    # 2026-10-26 从 qwen3.8-flash 换成 qwen3.7-flash：按百炼官方价目（≤32K 档）
    # 输入 0.2 / 输出 0.8 元每百万 token，比 3.8-flash 的 0.8 / 2.7 便宜 3~4 倍；
    # 同一段 14 条历史做摘要实测 P50 1589ms → 1075ms（8 轮交替，1.48x），
    # JSON 排序与校验判断的输出格式未变。线上压缩是该模型最大的调用方。
    llm_small_model: str = "qwen3.7-flash"

    # 模型降级链：主模型报额度耗尽/欠费/模型不可用等错误时按序切换。
    # 服务端不会自动切换（见百炼官方 FAQ），该能力在客户端补齐；
    # 切换在同一进程内粘性生效（额度耗尽不会自愈，回退无意义）。
    # 顺序为便宜 → 贵，新老两代混排（每个模型各有独立免费额度，链越长可白嫖的容量越大）：
    # 主模型若走原生端点，链上这一代新模型会被工厂自动剔除（原生端点调不通它们）。
    llm_fallback_models: list[str] = [
        "qwen3.8-flash",  # 0.8 / 2.7
        "qwen3.8-omni-flash",  # 全模态，价未公示；2026-10-22 实测可用
        "deepseek-v4.1-flash",  # 1 / 4
        "qwen-plus",  # 4.8 / 48
        "qwen3.8-2.4t-a95b",  # 12 / 36
        "kimi-k3",  # 20 / 100
    ]

    # DashScope 默认超时 300 秒、ChatTongyi 默认 max_retries=10，
    # 两者相乘让单次调用最坏可达 50 分钟。所有 LLM 调用统一走
    # core/llm_factory.build_chat_llm，由这里集中控制。
    llm_request_timeout: int = 60
    llm_stream_timeout: int = 180
    llm_max_retries: int = 2

    # —— 管理员认证 ——
    # AUTH_SECRET：JWT 签名密钥；缺失时用进程内随机值并告警（重启后登录态失效）。
    # ADMIN_USERNAME / ADMIN_INIT_PASSWORD：启动时 admin_users 表为空则自动建初始
    # 管理员；表非空则忽略。两者都未配置时不会创建任何管理员（登录将不可用）。
    auth_secret: str = ""
    auth_token_expire_hours: int = 24 * 7
    admin_username: str = ""
    admin_init_password: str = ""

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "job_copilot"
    mysql_charset: str = "utf8mb4"
    mysql_pool_size: int = 5
    mysql_pool_recycle: int = 3600

    upload_allowed_extensions: list[str] = ["txt", "pdf", "docx", "doc"]
    image_allowed_extensions: list[str] = ["png", "jpg", "jpeg", "webp"]
    upload_max_content_length: int = 20 * 1024 * 1024

    conversation_index_chunk_size: int = 700
    conversation_index_chunk_overlap: int = 120

    # 检索重排策略：listwise | per_doc | rrf | off
    # 实测（scripts/bench_rerank_v3.py，简历+电商混合文档、5 个 query）：
    #   逐篇 per_doc 21.4s / 批量打分 9.7s / listwise 3.7s（5.8x，Top-4 重合 0.71）
    # rrf 虽零耗时但 Top-4 重合仅 0.14~0.33——BM25 按字切分导致召回质量差，
    # 而 RRF 不看 query-doc 语义，故只作 LLM 失败时的降级路径。
    rerank_strategy: str = "listwise"
    llm_rerank_max_docs: int = 10
    llm_rerank_char_limit: int = 300
    rerank_rrf_k: int = 60

    # 内置知识库（通用求职知识）的注入门槛。
    # 它和用户上传的文档性质不同：前者是"方法参考"，后者才是"候选人的真实经历"，
    # 混在一起会让模型把通用知识当成用户经历写进简历（2026-10-22 实测：生成的简历里
    # 出现了用户从未提供过的「企业级 AI 智能问答平台」）。
    # 实测「介绍本 Job Copilot 项目」这类完全无关的 query 也能命中 0.33~0.34 分，
    # 所以 0.3 的旧阈值形同虚设；0.55 能把这类弱相关条目挡掉。
    builtin_kb_enabled: bool = True
    builtin_kb_score_threshold: float = 0.55
    builtin_kb_top_k: int = 2
    # 命中该词的任务视为"写简历/改简历"，此时不再注入任何内置知识库条目
    builtin_kb_resume_block_keywords: str = "简历,履历,resume"

    # 历史压缩的两个水位（条数按 DB 消息计，一轮问答 = 2 条）。
    # 2026-10-26 线上实测：旧逻辑只有一个阈值兼作目标（12），每次只压掉溢出的 2 条，
    # 于是第 8 次请求之后每轮都触发，压缩那次模型调用均值 4475ms、输出 142~383 token，
    # 比回答调用（2243ms / 56~162 token）又慢又长。这里把"触发"和"压到多少"分开：
    # 攒到 trigger 才压，一次压到 target，压缩频率降到每 3 轮一次。
    history_compress_trigger_messages: int = 12
    history_compress_target_messages: int = 8
    # 后台队列追不上的硬上限：超过它就不再异步，退回同步压缩。
    # 用户连发时每轮只加 2 条，40 条 ≈ 20 轮，留足了后台排队的时间。
    history_compress_max_messages: int = 40
    # 摘要正文的 token 兜底。中文摘要 120 字约 150~180 token，留一倍余量防截断。
    history_summary_max_tokens: int = 240
    # 事实校验的输出上限。校验结果是一段短 JSON（实测关思考后 113 token），
    # 400 留了 3.5 倍余量；这个值同时是"思考被误开"的止损阀。
    context_verify_max_tokens: int = 400

    # 请求级分段计时（见 app/core/perf_trace.py）。
    # 日志：每个 chat 请求输出一行 PERF {json}，开销可忽略，默认开启。
    # 响应头：Server-Timing 会暴露内部阶段耗时，默认关闭；压测时设 PERF_TRACE_HEADER=1，
    # 且请求还要带 X-Perf-Trace: 1 才会返回。
    perf_trace_log: bool = True
    perf_trace_header: bool = False

    resources_root: str = str(_BACKEND_ROOT / "resources")

    def model_post_init(self, __context) -> None:
        if self.dashscope_api_key:
            os.environ.setdefault("DASHSCOPE_API_KEY", self.dashscope_api_key)
        if self.metaso_api_key:
            os.environ.setdefault("METASO_API_KEY", self.metaso_api_key)
        # Windows 开发环境下 torch 相关运行库与 faiss-cpu 的 OpenMP 可能冲突；
        # 仅开发/测试环境放行，生产环境以最小依赖隔离（见 deploy 文档）
        if os.name == "nt" and self.env in {"dev", "test"}:
            os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    @property
    def is_dev(self) -> bool:
        return self.env in {"dev", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
