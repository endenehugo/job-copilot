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

    llm_model: str = "qwen-plus"
    multimodal_model: str = "qwen-vl-plus"

    # DashScope 默认超时 300 秒、ChatTongyi 默认 max_retries=10，
    # 两者相乘让单次调用最坏可达 50 分钟。所有 LLM 调用统一走
    # core/llm_factory.build_chat_llm，由这里集中控制。
    llm_request_timeout: int = 60
    llm_stream_timeout: int = 180
    llm_max_retries: int = 2

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
