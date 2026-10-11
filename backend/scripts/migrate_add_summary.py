"""增量摘要字段迁移：为 conversations 表添加 context_summary 和 summarized_until_id。

用法（在 backend/ 目录下）：
    python scripts/migrate_add_summary.py

说明：
- 项目无 alembic，create_all 不会 ALTER 已有表，需手动执行本脚本。
- 脚本幂等：字段已存在时跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql

from app.core.config import settings

COLUMNS = [
    ("context_summary", "TEXT NOT NULL"),
    ("summarized_until_id", "INT NOT NULL DEFAULT 0"),
]


def main() -> int:
    connection = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset=settings.mysql_charset,
    )
    try:
        with connection.cursor() as cursor:
            for col_name, col_def in COLUMNS:
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'conversations' AND COLUMN_NAME = %s",
                    (settings.mysql_database, col_name),
                )
                if cursor.fetchone()[0] > 0:
                    print(f"  跳过：{col_name} 已存在")
                    continue
                cursor.execute(f"ALTER TABLE conversations ADD COLUMN {col_name} {col_def}")
                print(f"  添加：{col_name} {col_def}")
        connection.commit()
        print("迁移完成。")
        return 0
    except pymysql.err.MySQLError as exc:
        print(f"迁移失败：{exc}")
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
