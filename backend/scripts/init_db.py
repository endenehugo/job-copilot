"""初始化数据库：建库（表由 SQLAlchemy create_all 在首次连接时自动创建）。

用法（在 backend/ 目录下）：
    python scripts/init_db.py

说明：
- 使用 .env 中的 MYSQL_USER/MYSQL_PASSWORD 连接 MySQL 服务端（不指定库）。
- 权限不足（GRANT 仅覆盖单个库）时，请用管理员账号手动执行其中的 CREATE DATABASE。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql

from app.core.config import settings


def main() -> int:
    connection = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        charset=settings.mysql_charset,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` "
                f"DEFAULT CHARACTER SET {settings.mysql_charset} COLLATE "
                f"{settings.mysql_charset}_unicode_ci"
            )
        connection.commit()
        print(f"数据库已就绪：{settings.mysql_database}")
        return 0
    except pymysql.err.AccessDeniedError as exc:
        print(f"建库失败（权限不足）：{exc}")
        print("请用管理员账号手动执行：")
        print(
            f"  CREATE DATABASE IF NOT EXISTS `{settings.mysql_database}` "
            f"DEFAULT CHARACTER SET {settings.mysql_charset};"
        )
        print("或为本用户授权：")
        print(
            f"  GRANT ALL PRIVILEGES ON `{settings.mysql_database}`.* "
            f"TO '{settings.mysql_user}'@'%';"
        )
        return 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
