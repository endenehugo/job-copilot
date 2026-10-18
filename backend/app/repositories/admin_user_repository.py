"""管理员用户表与仓储。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.repositories.mysql_base import Base


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class AdminUserRepository:
    def get_by_username(self, session: Session, username: str) -> AdminUser | None:
        stmt = select(AdminUser).where(AdminUser.username == username)
        return session.execute(stmt).scalar_one_or_none()

    def count(self, session: Session) -> int:
        return int(session.execute(select(func.count()).select_from(AdminUser)).scalar_one())

    def create(self, session: Session, username: str, password_hash: str) -> AdminUser:
        entity = AdminUser(username=username, password_hash=password_hash)
        session.add(entity)
        session.flush()
        return entity
