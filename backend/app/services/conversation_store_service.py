from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.repositories import DatabaseManager, ConversationRepository, MessageRepository, DocumentRepository


@dataclass
class ConversationStoreService:
    _IMAGE_MARKDOWN_PATTERN = re.compile(r"!\[[^\]]*\]\((/conversation/image/[^)\s]+)\)")

    def __post_init__(self):
        self.database_manager = DatabaseManager()
        self.conversation_repository = ConversationRepository()
        self.message_repository = MessageRepository()
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
            return [self._conversation_to_dict(item) for item in conversations]
        finally:
            session.close()

    def get_conversation_detail(self, conversation_id: str) -> dict:
        session = self.database_manager.get_session()
        try:
            conversation = self.conversation_repository.get_by_conversation_id(session, conversation_id)
            if conversation is None:
                raise ValueError("会话不存在")
            messages = self.message_repository.list_by_conversation_id(session, conversation_id)
            documents = self.document_repository.list_by_conversation_id(session, conversation_id)
            return {
                **self._conversation_to_dict(conversation),
                "messages": [self._message_to_dict(item) for item in messages],
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
        session = self.database_manager.get_session()
        try:
            messages = self.message_repository.list_by_conversation_id(session, conversation_id)
            return [self._message_to_dict(item) for item in messages]
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
        session = self.database_manager.get_session()
        now = datetime.now()
        try:
            with session.begin():
                conversation = self.conversation_repository.get_by_conversation_id(session, conversation_id)
                if conversation is None:
                    raise ValueError("会话不存在")

                self.message_repository.create(
                    session,
                    message_id=f"msg_{uuid.uuid4().hex[:16]}",
                    conversation_id=conversation_id,
                    role="user",
                    content=user_content,
                    created_at=now,
                )
                self.message_repository.create(
                    session,
                    message_id=f"msg_{uuid.uuid4().hex[:16]}",
                    conversation_id=conversation_id,
                    role="assistant",
                    content=assistant_content,
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
        finally:
            session.close()

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
    def _message_to_dict(item) -> dict:
        return {
            "message_id": item.message_id,
            "conversation_id": item.conversation_id,
            "role": item.role,
            "content": item.content,
            "created_at": item.created_at.isoformat(),
        }

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

    @classmethod
    def _strip_image_markdown(cls, content: str) -> str:
        return cls._IMAGE_MARKDOWN_PATTERN.sub("", content or "")