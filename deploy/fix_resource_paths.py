#!/usr/bin/env python3
"""把 conversation_documents 表里的绝对路径从开发机前缀改写为服务器前缀。

背景：conversation_documents.stored_path / parsed_text_path 保存的是上传时的
绝对路径（Windows 上是 F:\\code\\job-copilot\\backend\\resources\\uploads\\...）。
迁库到 Linux 后这些路径在服务器上不存在，会造成：

1. 旧会话检索失效（document_index_service 读到路径不存在就跳过该文档）；
2. 更隐蔽的后果——在旧会话里再传一份新文档会触发 rebuild_conversation_index，
   被跳过的旧文档会从 FAISS 索引里消失，检索能力静默退化。

本脚本按「resources/ 之后的相对部分」重建路径，跨平台、幂等，可重复执行。

用法（用后端 venv 的 python 运行）：
    MYSQL_HOST=127.0.0.1 MYSQL_USER=jobcopilot MYSQL_PASSWORD=*** MYSQL_DATABASE=job_copilot \\
    /opt/job-copilot/backend/.venv/bin/python fix_resource_paths.py \\
        --resources-root /opt/job-copilot/backend/resources --report-missing
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    import pymysql
except ImportError:  # pragma: no cover
    sys.exit("缺少 PyMySQL：请用后端 venv 的 python 运行本脚本")

PATH_COLUMNS = ("stored_path", "parsed_text_path")
MARKER = "/resources/"
# 未显式传参时的默认库名（服务器上通常是 llmrag，与 .env 保持一致）
DEFAULT_DATABASE = "job_copilot"
DEFAULT_USER = "jobcopilot"


def load_env_defaults() -> None:
    """把仓库根 / backend 下的 .env 读进环境变量（不覆盖已存在的真实环境变量）。

    这样在开发机上直接跑 `python deploy/fix_resource_paths.py --resources-root ...`
    也能连上 .env 里配置的那个库，不必手工 export 一堆变量。
    """
    repo_root = Path(__file__).resolve().parents[1]
    for candidate in (repo_root / ".env", repo_root / "backend" / ".env"):
        if not candidate.is_file():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def relative_under_resources(path: str) -> str | None:
    """取出旧绝对路径中 resources 之后的相对部分；无法识别时返回 None。"""
    if not path:
        return None
    normalized = path.replace("\\", "/").strip()
    index = normalized.rfind(MARKER)
    if index != -1:
        return normalized[index + len(MARKER):]
    if normalized.endswith("/resources"):
        return ""
    return None


def rebuild_path(resources_root: str, relative: str) -> str:
    if not relative:
        return resources_root
    return os.path.join(resources_root, *relative.split("/"))


def connect(args: argparse.Namespace):
    return pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=args.database,
        charset=args.charset,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def main() -> int:
    load_env_defaults()
    parser = argparse.ArgumentParser(description="改写 conversation_documents 的绝对路径")
    parser.add_argument("--host", default=os.environ.get("MYSQL_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("MYSQL_PORT", "3306")))
    parser.add_argument("--user", default=os.environ.get("MYSQL_USER", DEFAULT_USER))
    parser.add_argument("--password", default=os.environ.get("MYSQL_PASSWORD", ""))
    parser.add_argument("--database", default=os.environ.get("MYSQL_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--charset", default=os.environ.get("MYSQL_CHARSET", "utf8mb4"))
    parser.add_argument("--resources-root", required=True, help="服务器上的 resources 绝对路径")
    parser.add_argument("--report-missing", action="store_true", help="逐个检查文件是否存在并报告缺失")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做的改动")
    args = parser.parse_args()

    resources_root = os.path.abspath(args.resources_root)
    root_exists = os.path.isdir(resources_root)
    if not root_exists:
        if args.dry_run:
            # 允许在开发机上预演服务器路径：只做换算，不落盘
            print(f"[警告] resources 目录在本地不存在：{resources_root}（dry-run 继续，仅做路径换算）")
        else:
            print(f"[失败] resources 目录不存在：{resources_root}", file=sys.stderr)
            return 2
    if not root_exists:
        args.report_missing = False

    connection = connect(args)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_id, original_name, stored_path, parsed_text_path "
                "FROM conversation_documents"
            )
            rows = cursor.fetchall()

            total = len(rows)
            updated = 0
            already_ok = 0
            unresolvable = []
            missing = []
            updates = []

            for row in rows:
                new_values = {}
                for column in PATH_COLUMNS:
                    old_value = row.get(column) or ""
                    relative = relative_under_resources(old_value)
                    if relative is None:
                        unresolvable.append((row["document_id"], column, old_value))
                        continue
                    new_value = rebuild_path(resources_root, relative)
                    if os.path.normpath(new_value) != os.path.normpath(old_value):
                        new_values[column] = new_value
                    if args.report_missing and not os.path.isfile(new_value):
                        missing.append((row["document_id"], row.get("original_name") or "", new_value))

                if new_values:
                    updates.append((row["document_id"], new_values))
                else:
                    already_ok += 1

            print(f"待处理文档记录：{total} 条")
            print(f"  需要改写：{len(updates)} 条")
            print(f"  已正确  ：{already_ok} 条")
            print(f"  无法识别：{len(unresolvable)} 条")
            if args.report_missing:
                print(f"  文件缺失：{len(missing)} 个路径")

            for document_id, column, old_value in unresolvable[:10]:
                print(f"  [无法识别] {document_id}.{column} = {old_value}")

            if args.dry_run:
                for document_id, values in updates[:20]:
                    print(f"  [试运行] {document_id} -> {values}")
                print("dry-run：未写入数据库")
                return 0

            for document_id, values in updates:
                assignments = ", ".join(f"`{column}`=%s" for column in values)
                cursor.execute(
                    f"UPDATE conversation_documents SET {assignments} WHERE document_id=%s",
                    list(values.values()) + [document_id],
                )
                updated += 1

        connection.commit()
        print(f"已改写 {updated} 条记录的绝对路径 -> {resources_root}")

        if args.report_missing and missing:
            print("\n以下文件在磁盘上不存在，对应会话的检索会退化（可让用户重新上传）：")
            for document_id, original_name, path in missing[:20]:
                print(f"  - {document_id} ({original_name}) -> {path}")
            if len(missing) > 20:
                print(f"  ... 另有 {len(missing) - 20} 个")

        if unresolvable:
            print("\n[警告] 存在无法识别的路径，请人工确认后再启动服务", file=sys.stderr)
            return 1
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
