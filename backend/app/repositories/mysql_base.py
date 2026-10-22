from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)


Base = declarative_base()

# 项目没有迁移框架：create_all 只建新表，不会给已有表加列。
# 这里登记"后加的列"，启动时幂等补齐，避免忘了跑手工 SQL 就在查询时 1054 报错。
_COLUMN_PATCHES = {
    "conversations": {
        "pinned_at": "ALTER TABLE conversations ADD COLUMN pinned_at DATETIME NULL",
        # 索引必须与列写在同一条 ALTER 里：模型上的 index=True 只对 create_all
        # 新建的表生效，已有表不会因为改了模型就长出索引。
        "owner_key": (
            "ALTER TABLE conversations "
            "ADD COLUMN owner_key VARCHAR(64) NOT NULL DEFAULT '', "
            "ADD INDEX idx_conversations_owner (owner_key)"
        ),
    },
}


@dataclass
class DatabaseManager:
    _engine = None
    _session_factory = None
    _config_fingerprint = None
    _tables_ready = False

    @classmethod
    def _build_config_fingerprint(cls, config: dict) -> tuple:
        return (
            config.get("MYSQL_HOST"),
            config.get("MYSQL_PORT"),
            config.get("MYSQL_USER"),
            config.get("MYSQL_PASSWORD"),
            config.get("MYSQL_DATABASE"),
            config.get("MYSQL_CHARSET"),
            config.get("MYSQL_POOL_SIZE"),
            config.get("MYSQL_POOL_RECYCLE"),
        )

    @classmethod
    def _build_url(cls, config: dict) -> str:
        return (
            f"mysql+pymysql://{config['MYSQL_USER']}:{config['MYSQL_PASSWORD']}"
            f"@{config['MYSQL_HOST']}:{config['MYSQL_PORT']}/{config['MYSQL_DATABASE']}"
            f"?charset={config.get('MYSQL_CHARSET', 'utf8mb4')}"
        )

    @classmethod
    def _settings_as_config(cls) -> dict:
        return {
            "MYSQL_HOST": settings.mysql_host,
            "MYSQL_PORT": settings.mysql_port,
            "MYSQL_USER": settings.mysql_user,
            "MYSQL_PASSWORD": settings.mysql_password,
            "MYSQL_DATABASE": settings.mysql_database,
            "MYSQL_CHARSET": settings.mysql_charset,
            "MYSQL_POOL_SIZE": settings.mysql_pool_size,
            "MYSQL_POOL_RECYCLE": settings.mysql_pool_recycle,
        }

    @classmethod
    def _ensure_engine(cls):
        config = cls._settings_as_config()
        fingerprint = cls._build_config_fingerprint(config)
        if cls._engine is not None and cls._config_fingerprint == fingerprint:
            return

        engine = create_engine(
            cls._build_url(config),
            pool_pre_ping=True,
            pool_recycle=config.get("MYSQL_POOL_RECYCLE", 3600),
            pool_size=config.get("MYSQL_POOL_SIZE", 5),
            future=True,
        )
        cls._engine = engine
        cls._session_factory = scoped_session(
            sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
        )
        cls._config_fingerprint = fingerprint
        cls._tables_ready = False

    @classmethod
    def ensure_tables(cls):
        cls._ensure_engine()
        if cls._tables_ready:
            return
        Base.metadata.create_all(bind=cls._engine)
        cls._ensure_columns()
        cls._tables_ready = True

    @classmethod
    def _ensure_columns(cls):
        """幂等补列：缺哪列补哪列，已存在则跳过。

        失败只告警不抛——DBA 账号没有 ALTER 权限时仍要能启动，
        留一条明确的错误日志比直接崩更好排查。
        """
        inspector = inspect(cls._engine)
        for table, patches in _COLUMN_PATCHES.items():
            try:
                existing = {column["name"] for column in inspector.get_columns(table)}
            except Exception:  # 表还不存在/无权限查看，交给 create_all 的既有行为
                continue
            for column, ddl in patches.items():
                if column in existing:
                    continue
                try:
                    with cls._engine.begin() as connection:
                        connection.execute(text(ddl))
                    logger.warning("已自动补列 %s.%s", table, column)
                except Exception as exc:  # noqa: BLE001
                    logger.error("自动补列失败 %s.%s：%s（请手工执行：%s）", table, column, exc, ddl)
        inspector.clear_cache()

    @classmethod
    def get_session(cls):
        cls.ensure_tables()
        return cls._session_factory()

    @classmethod
    def remove_session(cls):
        """释放当前线程的 scoped_session（请求结束时由 FastAPI 依赖调用）。"""
        if cls._session_factory is not None:
            cls._session_factory.remove()