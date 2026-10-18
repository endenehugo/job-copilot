from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.repositories import (
    DatabaseManager,
    ConversationDocument,
    ConversationMessage,
    ConversationMessageMeta,
    ConversationRepository,
    InterviewMessage,
    InterviewSession,
    InterviewSessionRepository,
    JobAnalysis,
    MessageMetaRepository,
    MessageRepository,
    DocumentRepository,
    ResumeGeneration,
    ResumeVersion,
)


@dataclass
class ConversationStoreService:
    _IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[[^\]]*\]\((/conversation/image/[^)\s]+)\)")

    def __post_init__(self):
        self.database_manager = DatabaseManager()
        self.conversation_repository = ConversationRepository()
        self.message_repository = MessageRepository()
        self.message_meta_repository = MessageMetaRepository()
        self.document_repository = DocumentRepository()

    def create_conversation(self, title: str = "新对话", mode: str = "agent") -> dict:
        session = self.database_manager.get_session()
        now = datetime.now()
        conversation_id = f"conv_{now.strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        try:
            with session.begin():
                entity = self.conversation_repository.create(
                    session,
                    conversation_id=conversation_id,
                    title=(title or "新对话").strip()[:255] or "新对话",
                    mode=(mode or "agent").strip() or "agent",
                    message_count=0,
                    last_message_preview="",
                    created_at=now,
                    updated_at=now,
                )
            return self._conversation_to_dict(entity)
        finally:
            session.close()

    def list_conversations(self, limit: int = 50) -> list[dict]:
        session = self.database_manager.get_session()
        try:
            conversations = self.conversation_repository.list_recent(session, limit=limit)
            items = [self._conversation_to_dict(item) for item in conversations]
            self._attach_tree_badges(session, items)
            return items
        finally:
            session.close()

    @staticmethod
    def _attach_tree_badges(session, items: list[dict]) -> None:
        """为会话列表附加四类子节点徽标（面试/JD 分析/项目优化/简历）。

        每类一条 GROUP BY 聚合查询，避免前端树形列表按会话逐个请求造成 N+1。
        """
        from sqlalchemy import case, func, select

        from app.repositories import ProjectRewrite, ProjectRewriteRepository

        ids = [item["conversation_id"] for item in items]
        if not ids:
            return

        def _count_map(stmt_result):
            return {cid: int(total or 0) for cid, total in stmt_result}

        interview_rows = _count_map(session.execute(
            select(
                InterviewSession.conversation_id,
                func.count(InterviewSession.id),
            )
            .where(InterviewSession.conversation_id.in_(ids))
            .group_by(InterviewSession.conversation_id)
        ).all())
        ongoing_rows = _count_map(session.execute(
            select(
                InterviewSession.conversation_id,
                func.sum(case((InterviewSession.status == "in_progress", 1), else_=0)),
            )
            .where(InterviewSession.conversation_id.in_(ids))
            .group_by(InterviewSession.conversation_id)
        ).all())
        analysis_rows = _count_map(session.execute(
            select(JobAnalysis.conversation_id, func.count(JobAnalysis.id))
            .where(JobAnalysis.conversation_id.in_(ids))
            .group_by(JobAnalysis.conversation_id)
        ).all())
        rewrite_rows = _count_map(session.execute(
            select(ProjectRewrite.conversation_id, func.count(ProjectRewrite.id))
            .where(ProjectRewrite.conversation_id.in_(ids))
            .group_by(ProjectRewrite.conversation_id)
        ).all())
        resume_rows = _count_map(session.execute(
            select(ResumeGeneration.conversation_id, func.count(ResumeGeneration.id))
            .where(ResumeGeneration.conversation_id.in_(ids))
            .group_by(ResumeGeneration.conversation_id)
        ).all())

        for item in items:
            cid = item["conversation_id"]
            item["interview_count"] = interview_rows.get(cid, 0)
            item["interview_in_progress"] = ongoing_rows.get(cid, 0)
            item["analysis_count"] = analysis_rows.get(cid, 0)
            item["rewrite_count"] = rewrite_rows.get(cid, 0)
            item["resume_count"] = resume_rows.get(cid, 0)

    def get_conversation_detail(self, conversation_id: str) -> dict:
        session = self.database_manager.get_session()
        try:
            conversation = self.conversation_repository.get_by_conversation_id(session, conversation_id)
            if conversation is None:
                raise ValueError("会话不存在")
            messages = self.message_repository.list_by_conversation_id(session, conversation_id)
            documents = self.document_repository.list_by_conversation_id(session, conversation_id)
            meta_map = self.message_meta_repository.map_payloads_by_conversation_id(
                session, conversation_id
            )
            return {
                **self._conversation_to_dict(conversation),
                "messages": [
                    self._message_to_dict(item, self._parse_meta(meta_map.get(item.message_id)))
                    for item in messages
                ],
                "documents": [self._document_to_dict(item) for item in documents],
            }
        finally:
            session.close()

    def ensure_conversation_exists(self, conversation_id: str) -> None:
        session = self.database_manager.get_session()
        try:
            if self.conversation_repository.get_by_conversation_id(session, conversation_id) is None:
                raise ValueError("会话不存在")
        finally:
            session.close()

    def get_conversation_messages(self, conversation_id: str) -> list[dict]:
        """会话消息（含卡片载荷）。

        注意：调用方普遍把它当"给模型看的历史"用，`meta` 只影响展示、
        不进正文，所以带不带都不改变模型输入。
        """
        session = self.database_manager.get_session()
        try:
            messages = self.message_repository.list_by_conversation_id(session, conversation_id)
            meta_map = self.message_meta_repository.map_payloads_by_conversation_id(
                session, conversation_id
            )
            return [
                self._message_to_dict(item, self._parse_meta(meta_map.get(item.message_id)))
                for item in messages
            ]
        finally:
            session.close()

    def get_messages_after(self, conversation_id: str, after_id: int) -> list[dict]:
        """只加载 id > after_id 的消息，用于增量摘要。"""
        session = self.database_manager.get_session()
        try:
            messages = self.message_repository.list_after_id(session, conversation_id, after_id)
            meta_map = self.message_meta_repository.map_payloads_by_conversation_id(
                session, conversation_id
            )
            return [
                self._message_to_dict(item, self._parse_meta(meta_map.get(item.message_id)))
                for item in messages
            ]
        finally:
            session.close()

    def get_conversation_summary(self, conversation_id: str) -> tuple[str, int]:
        """读取持久化的摘要和水位线。"""
        session = self.database_manager.get_session()
        try:
            conv = self.conversation_repository.get_by_conversation_id(session, conversation_id)
            if conv is None:
                return ("", 0)
            return (conv.context_summary or "", conv.summarized_until_id or 0)
        finally:
            session.close()

    def save_conversation_summary(self, conversation_id: str, summary: str, until_id: int) -> None:
        """持久化增量摘要，乐观锁防止回退。"""
        session = self.database_manager.get_session()
        try:
            with session.begin():
                self.conversation_repository.update_context_summary(
                    session, conversation_id, summary, until_id
                )
        finally:
            session.close()

    def get_conversation_documents(self, conversation_id: str) -> list[dict]:
        session = self.database_manager.get_session()
        try:
            documents = self.document_repository.list_by_conversation_id(session, conversation_id)
            return [self._document_to_dict(item) for item in documents]
        finally:
            session.close()

    def append_message_pair(self, conversation_id: str, user_content: str, assistant_content: str, mode: str) -> None:
        self.append_pair_with_meta(conversation_id, user_content, assistant_content, mode=mode)
        return None

    def append_pair_with_meta(
        self,
        conversation_id: str,
        user_content: str,
        assistant_content: str,
        mode: str | None = None,
        assistant_meta: dict | None = None,
    ) -> dict:
        """写入一问一答，可选给助手消息附带结构化载荷（用于主会话里嵌卡片）。

        载荷单独存 `conversation_message_meta`（见该表 docstring 的选择理由）。
        同一事务内写消息、载荷与会话摘要，不会出现"卡片在、消息不在"。
        `mode` 传 None 时不改会话原有 mode（简历生成不是对话模式，不该覆盖它）。
        """
        session = self.database_manager.get_session()
        now = datetime.now()
        try:
            with session.begin():
                conversation = self.conversation_repository.get_by_conversation_id(session, conversation_id)
                if conversation is None:
                    raise ValueError("会话不存在")
                return self._append_pair(
                    session,
                    conversation,
                    conversation_id,
                    user_content,
                    assistant_content,
                    mode,
                    now,
                    assistant_meta=assistant_meta,
                )
        finally:
            session.close()

    def _append_pair(
        self,
        session,
        conversation,
        conversation_id: str,
        user_content: str,
        assistant_content: str,
        mode: str | None,
        now: datetime,
        assistant_meta: dict | None = None,
    ) -> dict:
        user_entity = self.message_repository.create(
            session,
            message_id=f"msg_{uuid.uuid4().hex[:16]}",
            conversation_id=conversation_id,
            role="user",
            content=user_content,
            created_at=now,
        )
        assistant_entity = self.message_repository.create(
            session,
            message_id=f"msg_{uuid.uuid4().hex[:16]}",
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_content,
            created_at=now,
        )
        if assistant_meta:
            self.message_meta_repository.create(
                session,
                message_id=assistant_entity.message_id,
                conversation_id=conversation_id,
                kind=str(assistant_meta.get("kind") or ""),
                payload_json=json.dumps(assistant_meta, ensure_ascii=False),
                created_at=now,
            )

        title = conversation.title
        if conversation.message_count == 0 and title == "新对话":
            text_for_title = self._strip_image_markdown(user_content).replace("\n", " ").strip()
            title = text_for_title[:20] or "图片消息"

        preview = assistant_content.strip().replace("\n", " ")[:500]
        self.conversation_repository.update_summary(
            conversation,
            title=title,
            mode=mode,
            message_count=conversation.message_count + 2,
            last_message_preview=preview,
            updated_at=now,
        )
        return {
            "user_message_id": user_entity.message_id,
            "assistant_message_id": assistant_entity.message_id,
        }

    def bind_document(self, conversation_id: str, parsed_document: dict, status: str = "parsed") -> dict:
        session = self.database_manager.get_session()
        now = datetime.now()
        try:
            with session.begin():
                conversation = self.conversation_repository.get_by_conversation_id(session, conversation_id)
                if conversation is None:
                    raise ValueError("会话不存在")

                document = self.document_repository.create(
                    session,
                    document_id=parsed_document["document_id"],
                    conversation_id=conversation_id,
                    original_name=parsed_document["original_name"],
                    stored_name=parsed_document["stored_name"],
                    stored_path=parsed_document["stored_path"],
                    parsed_text_path=parsed_document["parsed_text_path"],
                    file_type=parsed_document["file_type"],
                    status=status,
                    char_count=parsed_document["char_count"],
                    created_at=now,
                    updated_at=now,
                )
                self.conversation_repository.update_summary(conversation, updated_at=now)
            return self._document_to_dict(document)
        finally:
            session.close()

    def update_document_status(self, document_id: str, status: str) -> dict:
        session = self.database_manager.get_session()
        now = datetime.now()
        try:
            with session.begin():
                document = self.document_repository.get_by_document_id(session, document_id)
                if document is None:
                    raise ValueError("文档不存在")
                document.status = status
                document.updated_at = now
            return self._document_to_dict(document)
        finally:
            session.close()

    def remove_document(self, document_id: str) -> dict:
        session = self.database_manager.get_session()
        try:
            with session.begin():
                document = self.document_repository.get_by_document_id(session, document_id)
                if document is None:
                    raise ValueError("文档不存在")
                payload = self._document_to_dict(document)
                conversation = self.conversation_repository.get_by_conversation_id(session, document.conversation_id)
                self.document_repository.delete(session, document)
                if conversation is not None:
                    self.conversation_repository.update_summary(conversation, updated_at=datetime.now())
            return payload
        finally:
            session.close()

    @staticmethod
    def _conversation_to_dict(item) -> dict:
        return {
            "conversation_id": item.conversation_id,
            "title": item.title,
            "mode": item.mode,
            "message_count": item.message_count,
            "last_message_preview": item.last_message_preview,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    @staticmethod
    def _message_to_dict(item, meta: dict | None = None) -> dict:
        data = {
            "id": item.id,
            "message_id": item.message_id,
            "conversation_id": item.conversation_id,
            "role": item.role,
            "content": item.content,
            "created_at": item.created_at.isoformat(),
        }
        # 绝大多数消息没有载荷，不额外下发 null，省一点带宽也少一层前端判断
        if meta:
            data["meta"] = meta
        return data

    @staticmethod
    def _parse_meta(payload_json: str | None) -> dict | None:
        """解析消息载荷。脏数据只丢卡片，不能连带整个会话详情 500。"""
        if not payload_json:
            return None
        try:
            value = json.loads(payload_json)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _document_to_dict(item) -> dict:
        return {
            "document_id": item.document_id,
            "conversation_id": item.conversation_id,
            "original_name": item.original_name,
            "stored_name": item.stored_name,
            "stored_path": item.stored_path,
            "parsed_text_path": item.parsed_text_path,
            "file_type": item.file_type,
            "status": item.status,
            "char_count": item.char_count,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }

    def delete_conversation(self, conversation_id: str) -> dict:
        """删除会话及全部关联数据（消息/文档/评分/简历版本/面试会话与问答）。

        单事务级联删除；文件与向量索引由路由层在事务成功后清理。
        """
        from sqlalchemy import delete as sql_delete

        session = self.database_manager.get_session()
        try:
            with session.begin():
                conversation = self.conversation_repository.get_by_conversation_id(session, conversation_id)
                if conversation is None:
                    raise ValueError("会话不存在")

                session_ids = [
                    s.session_id
                    for s in InterviewSessionRepository.list_by_conversation_id(
                        session, conversation_id, limit=1000
                    )
                ]
                if session_ids:
                    session.execute(
                        sql_delete(InterviewMessage).where(InterviewMessage.session_id.in_(session_ids))
                    )
                    session.execute(
                        sql_delete(InterviewSession).where(InterviewSession.conversation_id == conversation_id)
                    )
                session.execute(sql_delete(JobAnalysis).where(JobAnalysis.conversation_id == conversation_id))
                session.execute(sql_delete(ResumeVersion).where(ResumeVersion.conversation_id == conversation_id))
                session.execute(
                    sql_delete(ResumeGeneration).where(ResumeGeneration.conversation_id == conversation_id)
                )
                session.execute(
                    sql_delete(ConversationMessageMeta).where(
                        ConversationMessageMeta.conversation_id == conversation_id
                    )
                )
                session.execute(
                    sql_delete(ConversationMessage).where(ConversationMessage.conversation_id == conversation_id)
                )
                session.execute(
                    sql_delete(ConversationDocument).where(ConversationDocument.conversation_id == conversation_id)
                )
                session.delete(conversation)
        finally:
            session.close()
        return {"conversation_id": conversation_id}

    @classmethod
    def _strip_image_markdown(cls, content: str) -> str:
        return cls._IMAGE_MARKDOWN_PATTERN.sub("", content or "")