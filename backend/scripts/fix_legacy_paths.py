"""存量文档路径修复：把 Docker 部署时代入库的 /opt/... 绝对路径改写为本机路径。

背景：
- 旧部署（Docker，容器内根目录 /opt/job-copilot）把文档的绝对路径写进了
  conversation_documents 表的 stored_path / parsed_text_path 字段。
- 换到 Windows 或任何其他部署位置后这些路径不存在，导致旧会话的
  JD 分析 / 模拟面试 / 简历评分全部报「无法读取简历解析文本」（HTTP 500）。
- 代码本身没问题（ResourceUtils 按当前系统生成路径，新上传的记录正常），
  只需要修存量数据。

用法（在 backend/ 目录下）：
    python scripts/fix_legacy_paths.py            # dry-run，只打印将要改什么
    python scripts/fix_legacy_paths.py --apply    # 真正写库

安全设计：
- 只替换确定的旧前缀（LEGACY_PREFIX），不做模糊猜测；
- 替换后校验目标文件真实存在，不存在则只报告、不写库（避免造出新的坏路径）；
- 幂等：已是当前路径的记录自动跳过，可重复执行。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymysql
from dotenv import dotenv_values

from app.core.config import settings
from app.utils.resouce_utils import ResourceUtils

# Docker 部署时 backend/resources 在容器内的位置
LEGACY_PREFIXES = (
    "/opt/job-copilot/backend/resources/",
    "/opt/job-copilot/backend\\resources\\",
)

# 需要检查的表与字段：表名 -> (主键字段, 路径字段列表)
TARGETS = {
    "conversation_documents": ("document_id", ["stored_path", "parsed_text_path"]),
}


def main() -> int:
    apply = "--apply" in sys.argv
    current_root = str(Path(ResourceUtils.get_resource_path("")).resolve())
    print(f"当前资源根目录: {current_root}")
    print(f"模式: {'APPLY（写库）' if apply else 'DRY-RUN（只看不动，加 --apply 生效）'}\n")

    connection = pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        user=settings.mysql_user,
        password=settings.mysql_password,
        database=settings.mysql_database,
        charset=settings.mysql_charset,
    )
    fixed = skipped = missing = 0
    try:
        with connection.cursor() as cursor:
            for table, (pk, cols) in TARGETS.items():
                cursor.execute(f"SELECT {pk}, {', '.join(cols)} FROM {table}")  # noqa: S608
                for row in cursor.fetchall():
                    row_id, *paths = row
                    updates = {}
                    for col, old in zip(cols, paths):
                        if not old:
                            continue
                        new = old
                        for prefix in LEGACY_PREFIXES:
                            if old.startswith(prefix):
                                new = current_root.rstrip("/\\") + "/" + old[len(prefix):]
                                break
                        if new == old:
                            skipped += 1
                            continue
                        if not Path(new).exists():
                            missing += 1
                            print(f"  [缺文件] {table}.{pk}={row_id} {col}: {old} -> {new}（目标不存在，不写）")
                            continue
                        updates[col] = new
                    if not updates:
                        continue
                    set_clause = ", ".join(f"{c} = %s" for c in updates)
                    print(f"  [修复] {table}.{pk}={row_id}: " + "; ".join(
                        f"{c} -> {v}" for c, v in updates.items()
                    ))
                    if apply:
                        cursor.execute(
                            f"UPDATE {table} SET {set_clause} WHERE {pk} = %s",  # noqa: S608
                            (*updates.values(), row_id),
                        )
                    fixed += 1
            if apply:
                connection.commit()
    finally:
        connection.close()

    print(f"\n完成：{'已修复' if apply else '待修复'} {fixed} 条，已正常 {skipped} 条，目标文件缺失 {missing} 条")
    if not apply and fixed:
        print("确认无误后执行: python scripts/fix_legacy_paths.py --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
