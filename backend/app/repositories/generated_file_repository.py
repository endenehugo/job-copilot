"""交付文件的归属登记。

为什么需要这张表：`/api/v1/conversation/file/<filename>` 是唯一不经过
`conversation_id` 的资源端点。文件名本身不含会话信息，所以下载时无从判归属——
只靠"时间戳 + 6 位随机、不可枚举"挡着，属于弱保护。

两种可选做法：
- **签名 URL**：生成时把 hmac 拼进 query。缺点是链接会过期，用户过几天回来
  点历史消息里的链接就是 403。对这个产品（简历文件可能被反复下载）不合适。
- **登记表**（本方案）：文件名 → 会话，下载时回查归属。URL 格式不变，
  历史链接继续有效，顺带解决了"删会话时不知道该删哪些文件"的问题。

旧文件（本表上线前生成的）没有登记记录，这时只有管理员能下载——与
`owner_key = ''` 的 legacy 语义一致。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, delete, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class GeneratedFile(Base):
    __tablename__ = "generated_files"

    # 文件名做主键：同一个文件名只会属于一个会话，重复登记用 merge 覆盖
    filename: Mapped[str] = mapped_column(String(255), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GeneratedFileRepository:
    @staticmethod
    def record(session: Session, filename: str, conversation_id: str, now: datetime | None = None) -> None:
        """登记（幂等）。

        用 merge 而不是 add：分块写文件的 tool 会用同一个文件名多次落盘，
        add 在第二次就会撞主键。
        """
        entity = session.get(GeneratedFile, filename)
        if entity is None:
            session.add(
                GeneratedFile(
                    filename=filename,
                    conversation_id=conversation_id,
                    created_at=now or datetime.now(),
                )
            )
        else:
            entity.conversation_id = conversation_id
        session.flush()

    @staticmethod
    def get(session: Session, filename: str) -> GeneratedFile | None:
        return session.get(GeneratedFile, filename)

    @staticmethod
    def list_by_conversation_id(session: Session, conversation_id: str) -> list[str]:
        stmt = select(GeneratedFile.filename).where(
            GeneratedFile.conversation_id == conversation_id
        )
        return list(session.execute(stmt).scalars())

    @staticmethod
    def delete_by_conversation_id(session: Session, conversation_id: str) -> None:
        session.execute(delete(GeneratedFile).where(GeneratedFile.conversation_id == conversation_id))
