"""FastAPI 依赖注入：数据库会话与业务服务单例。

服务均为无状态或带惰性缓存的轻对象，用 lru_cache 保持进程内单例，
替代旧版 injector Module 的绑定关系。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Generator

from sqlalchemy.orm import Session

from app.repositories.mysql_base import DatabaseManager
from app.services.builtin_knowledge_service import BuiltinKnowledgeService
from app.services.context_compression_service import ContextCompressionService
from app.services.context_verification_service import ContextVerificationService
from app.services.conversation_chat_service import ConversationChatService
from app.services.conversation_store_service import ConversationStoreService
from app.services.document_index_service import DocumentIndexService
from app.services.document_parser_service import DocumentParserService
from app.services.export_service import ExportService
from app.services.image_analysis_service import ImageAnalysisService
from app.services.interview_simulation_service import InterviewSimulationService
from app.services.job_description_service import JobDescriptionService
from app.services.project_rewrite_service import ProjectRewriteService
from app.services.resume_scoring_service import ResumeScoringService


def get_db() -> Generator[Session, None, None]:
    session = DatabaseManager.get_session()
    try:
        yield session
    finally:
        DatabaseManager.remove_session()


@lru_cache
def get_document_parser_service() -> DocumentParserService:
    return DocumentParserService()


@lru_cache
def get_document_index_service() -> DocumentIndexService:
    # 注入内置知识库，让"会话文档 → 公共索引 → 内置知识库"三级检索真正闭环
    return DocumentIndexService(builtin_knowledge_service=get_builtin_knowledge_service())


@lru_cache
def get_conversation_store_service() -> ConversationStoreService:
    return ConversationStoreService()


@lru_cache
def get_conversation_chat_service() -> ConversationChatService:
    return ConversationChatService(
        conversation_store_service=get_conversation_store_service(),
        document_index_service=get_document_index_service(),
    )


@lru_cache
def get_job_description_service() -> JobDescriptionService:
    return JobDescriptionService()


@lru_cache
def get_resume_scoring_service() -> ResumeScoringService:
    return ResumeScoringService()


@lru_cache
def get_project_rewrite_service() -> ProjectRewriteService:
    return ProjectRewriteService()


@lru_cache
def get_interview_simulation_service() -> InterviewSimulationService:
    return InterviewSimulationService()


@lru_cache
def get_image_analysis_service() -> ImageAnalysisService:
    return ImageAnalysisService()


@lru_cache
def get_builtin_knowledge_service() -> BuiltinKnowledgeService:
    return BuiltinKnowledgeService()


@lru_cache
def get_export_service() -> ExportService:
    return ExportService()


@lru_cache
def get_context_compression_service() -> ContextCompressionService:
    return ContextCompressionService()


@lru_cache
def get_context_verification_service() -> ContextVerificationService:
    return ContextVerificationService()
