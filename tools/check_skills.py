"""内置 skills 静态体检：frontmatter、本地引用、脚本语法、跨 skill 引用。

用法：python tools/check_skills.py [skills_dir]
输出每个 skill 的用例结果，退出码非 0 表示存在硬失败。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_DIR = Path(os.path.expanduser("~/.workbuddy/skills"))
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# 只认"独立出现的 /command"：前面是行首/空白/括号/反引号，后面不是路径或扩展名
CMD_RE = re.compile(r"(?<![\w/`<>])/([a-z][a-z0-9-]{2,})(?![\w./*-])")


def parse_frontmatter(text: str) -> dict:
    """极简 YAML：支持 `key: value`、引号、`key: >-` / `|` 多行块。"""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    lines = text[3:end].strip().splitlines()
    data: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if ":" not in line or line.startswith((" ", "\t")):
            i += 1
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if value in {">-", ">", "|", "|-"}:
            buf = []
            i += 1
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                buf.append(lines[i].strip())
                i += 1
            data[key] = " ".join(x for x in buf if x)
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        data[key] = value
        i += 1
    return data


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DIR
    skills = sorted(p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").exists())
    names = {p.name for p in skills}
    failures = 0

    for sk in skills:
        text = (sk / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(text)
        cases = []

        cases.append(("frontmatter.name 与目录一致", fm.get("name") == sk.name, f"name={fm.get('name')}"))
        desc = fm.get("description") or fm.get("description_zh") or ""
        cases.append(
            (
                "description 存在且 >20 字",
                len(desc) > 20,
                f"{len(desc)} 字符" + ("" if fm.get("description") else "（仅有 description_zh）"),
            )
        )

        # 本地引用（排除 http / 锚点 / 目录占位）；先剔除链接再找命令，避免把路径当 skill
        body = LINK_RE.sub("", text)
        missing = []
        for target in LINK_RE.findall(text):
            t = target.split("#")[0].strip()
            if not t or t.startswith(("http://", "https://", "mailto:")):
                continue
            if not (sk / t).exists():
                missing.append(t)
        cases.append(("Markdown 本地引用可达", not missing, "缺失: " + ", ".join(missing) if missing else "全部存在"))

        # 跨 skill 引用（/xxx 形式）必须存在对应 skill
        refs = sorted(set(CMD_RE.findall(body)))
        unknown = [r for r in refs if r not in names]
        cases.append(
            (
                "跨 skill 引用有效",
                not unknown,
                f"{len(refs)} 个引用" + (f"，未找到: {', '.join(unknown)}" if unknown else "，全部命中"),
            )
        )

        # 脚本：py 语法检查 / sh 与 js 存在性
        scripts = sorted((sk / "scripts").glob("**/*")) if (sk / "scripts").exists() else []
        py = [p for p in scripts if p.suffix == ".py"]
        bad_py = []
        for p in py:
            r = subprocess.run(
                [sys.executable, "-c", "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())", str(p)],
                capture_output=True,
                text=True,
            )
            if r.returncode != 0:
                bad_py.append(p.name)
        cases.append(
            ("脚本可解析", not bad_py, f"py {len(py)} 个" + (f"，失败 {bad_py}" if bad_py else "，全部通过"))
        )

        print(f"\n== {sk.name}")
        for title, passed, detail in cases:
            print(f"   {'PASS' if passed else 'FAIL'}  {title}｜{detail}")
            if not passed:
                failures += 1

    print(f"\n共 {len(skills)} 个 skill，硬失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
