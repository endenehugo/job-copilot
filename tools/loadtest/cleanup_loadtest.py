"""清理 locust 压测产生的会话数据。

默认 dry-run：只统计会删多少行，什么都不动。
加 --yes 且 --confirm-db 与实际库名一致才真的删。

⚠️ 连的库来自 .env / backend/.env 的 MYSQL_* —— 本机开发时它指向**线上库**。
   脚本会先打印 host/database，并要求二次确认，别跳过。

安全性设计（都是踩过或想过才加的）：
1) 时间比较放在 SQL 里做（`NOW() - INTERVAL n HOUR`）。
   用 Python 的 datetime.now() 去比库里的 updated_at 会踩时区：库里的值由服务器写，
   服务器多半是 UTC，而本机是 CST，整体偏 8 小时——"清 6 小时前的"会清成
   "清 14 小时前的"或干脆什么都不清。SQL 侧算，两端时区无关。
2) 默认只删访客会话（owner_key LIKE 'v_%'）。万一有人把正式会话命名为
   lt-session，这一条能挡住。-any-owner 才放开。
3) 一个事务删完。原来每张表 commit 一次，中途异常就是"删了一半"。
4) 顺带删 generated_docs 里的物理文件。裸 SQL 绕过了 generated_file_service，
   那一层才会删文件；只删表行会让文件变成永远查不到主人的孤儿。

用法：
    D:/dshcache/loadtest-venv/Scripts/python.exe tools/loadtest/cleanup_loadtest.py
    ... --yes --confirm-db job_copilot
    ... --title lt-session --older-than-hours 6
    ... --any-owner --yes --confirm-db job_copilot
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pymysql

_REPO = Path(__file__).resolve().parents[2]
_GENERATED_DIR = _REPO / "backend" / "resources" / "generated_docs"
BATCH = 200
_SAFE_TABLE = re.compile(r"^[A-Za-z0-9_]+$")

# 默认值与 backend/app/core/config.py 保持一致
_DEFAULTS = {
    "MYSQL_HOST": "127.0.0.1",
    "MYSQL_PORT": "3306",
    "MYSQL_USER": "root",
    "MYSQL_PASSWORD": "",
    "MYSQL_DATABASE": "job_copilot",
    "MYSQL_CHARSET": "utf8mb4",
}


def _load_env() -> dict[str, str]:
    """只解析 .env 里的 MYSQL_*。

    刻意不 import app.core.config：那会要求 pydantic 全家桶，
    而这个脚本只是想连库删几行压测数据。
    """
    values = dict(_DEFAULTS)
    for path in (_REPO / ".env", _REPO / "backend" / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            key = key.strip()
            if key in values:
                values[key] = raw.strip().strip("'\"")
    return values


ENV = _load_env()
DB_NAME = ENV["MYSQL_DATABASE"]


def _conn():
    return pymysql.connect(
        host=ENV["MYSQL_HOST"],
        port=int(ENV["MYSQL_PORT"]),
        user=ENV["MYSQL_USER"],
        password=ENV["MYSQL_PASSWORD"],
        database=DB_NAME,
        charset=ENV["MYSQL_CHARSET"],
        autocommit=False,
    )


def _tables_with_conversation_id(cur) -> list[str]:
    """动态发现带 conversation_id 的表，避免漏掉后来新增的关联表。"""
    cur.execute(
        """
        SELECT table_name FROM information_schema.columns
        WHERE table_schema = %s AND column_name = 'conversation_id'
          AND table_name <> 'conversations'
        """,
        (DB_NAME,),
    )
    return [row[0] for row in cur.fetchall() if _SAFE_TABLE.match(row[0])]


def _physical_files(cur, ids: list[str]) -> list[Path]:
    """按登记表查出这些会话的产物文件名。必须在删表**之前**取。"""
    names: list[str] = []
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        marks = ",".join(["%s"] * len(chunk))
        cur.execute(
            f"SELECT filename FROM `generated_files` WHERE conversation_id IN ({marks})",
            chunk,
        )
        names.extend(row[0] for row in cur.fetchall())
    found = []
    for name in names:
        path = (_GENERATED_DIR / name).resolve()
        # 只删 generated_docs 目录内的文件，防登记表被写坏后越界删除
        if _GENERATED_DIR.resolve() in path.parents:
            found.append(path)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="清理压测会话（默认 dry-run）")
    parser.add_argument("--title", default="lt-session", help="按标题精确匹配")
    parser.add_argument(
        "--older-than-hours", type=float, default=0, help="只清这么多小时之前的（库里算，时区无关）"
    )
    parser.add_argument(
        "--owner-key",
        default="v_%,admin",
        # 访客 ID 形如 v_xxx（core/ownership.py 的 VISITOR_PREFIX），admin 是带
        # LT_TOKEN 压测时的归属。demo / 空串（legacy）都不会命中。
        # 逗号分隔多个 LIKE 模式。
        help="只清归属匹配的会话，默认只清访客与 admin 的产物；%% 表示不限",
    )
    parser.add_argument("--any-owner", action="store_true", help="不限归属（危险，需自行确认）")
    parser.add_argument("--yes", action="store_true", help="真的执行删除")
    parser.add_argument("--confirm-db", default="", help="必须等于实际库名，防止连错库")
    args = parser.parse_args()

    print(f"目标库：{ENV['MYSQL_HOST']}:{ENV['MYSQL_PORT']}/{DB_NAME}")
    scope = "不限归属" if args.any_owner else f"owner_key LIKE {args.owner_key!r}"
    print(f"匹配条件：title = {args.title!r}，{scope}"
          + (f"，且早于 {args.older_than_hours} 小时前" if args.older_than_hours else ""))

    if args.yes and args.confirm_db != DB_NAME:
        print(f"\n拒绝执行：--confirm-db 必须是 {DB_NAME!r}（当前 {args.confirm_db!r}）。")
        print("先跑一遍 dry-run 看清行数，再 --yes --confirm-db " + DB_NAME)
        return 2

    conn = _conn()
    try:
        with conn.cursor() as cur:
            sql = "SELECT conversation_id FROM conversations WHERE title = %s"
            params: list = [args.title]
            if not args.any_owner:
                patterns = [p.strip() for p in args.owner_key.split(",") if p.strip()]
                if patterns:
                    sql += " AND (" + " OR ".join(["owner_key LIKE %s"] * len(patterns)) + ")"
                    params.extend(patterns)
            if args.older_than_hours:
                sql += " AND updated_at < NOW() - INTERVAL %s HOUR"
                params.append(args.older_than_hours)
            cur.execute(sql, params)
            ids = [row[0] for row in cur.fetchall()]

            if not ids:
                print("没有匹配到压测会话，退出。")
                return 0

            tables = _tables_with_conversation_id(cur)
            files = _physical_files(cur, ids) if "generated_files" in tables else []
            print(f"\n匹配到 {len(ids)} 个会话，前 5 个：{ids[:5]}")
            print(f"涉及表：conversations, {', '.join(tables)}")
            print(f"关联物理文件：{len(files)} 个"
                  + (f"（目录 {_GENERATED_DIR}）" if files else ""))

            counts: dict[str, int] = {}
            for table in ["conversations", *tables]:
                total = 0
                for i in range(0, len(ids), BATCH):
                    chunk = ids[i:i + BATCH]
                    marks = ",".join(["%s"] * len(chunk))
                    cur.execute(
                        f"SELECT COUNT(*) FROM `{table}` WHERE conversation_id IN ({marks})",
                        chunk,
                    )
                    total += cur.fetchone()[0]
                counts[table] = total

            print("\n待删行数：")
            for table, n in counts.items():
                print(f"  {table:<28} {n}")

            if not args.yes:
                print("\n这是 dry-run，没有删除任何数据。")
                print(f"确认无误后重跑：--yes --confirm-db {DB_NAME}")
                return 0

            print("\n开始删除（单事务）……")
            deleted = 0
            for table in tables + ["conversations"]:  # 先子表后主表
                for i in range(0, len(ids), BATCH):
                    chunk = ids[i:i + BATCH]
                    marks = ",".join(["%s"] * len(chunk))
                    cur.execute(
                        f"DELETE FROM `{table}` WHERE conversation_id IN ({marks})", chunk
                    )
                    deleted += cur.rowcount
                print(f"  {table:<28} 已清理")
            conn.commit()

            removed = 0
            for path in files:
                try:
                    path.unlink()
                    removed += 1
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    print(f"  ! 删除文件失败 {path.name}: {exc}")
            print(f"\n完成：删除 {deleted} 行，{removed} 个物理文件。")
            return 0
    except Exception:
        conn.rollback()
        print("\n出错，已回滚本次删除（表数据未变更）。")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
