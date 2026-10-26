"""压测配置留档：记录一次运行的全部可变因素，用于复现和跨轮次对比。

清单分两段：
- comparable：决定 config_id 的配置（目标、模型、并发、请求数、问题集、脚本哈希、被测服务版本…）。
  两轮的 config_id 相同，耗时才可以直接比较；不同就用 diff_config.py 看是哪一项变了。
- run：只用于追溯的信息（起止时间、git、工具版本、环境变量快照、实际请求数与结束原因），不参与指纹。
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = 1
ABSENT = "<absent>"
_SECRET_WORDS = ("TOKEN", "PASSWORD", "SECRET", "KEY")
_MASKED_NAMES = {"LT_BASIC_AUTH_USER"}


def env_snapshot(environ: dict[str, str] | None = None) -> dict[str, str]:
    """LT_* 环境变量快照；凭据类只记录“是否设置”，不写入值。"""
    source = os.environ if environ is None else environ
    snapshot: dict[str, str] = {}
    for name, value in sorted(source.items()):
        if not name.startswith("LT_"):
            continue
        masked = name in _MASKED_NAMES or any(word in name for word in _SECRET_WORDS)
        snapshot[name] = "<set>" if masked and value else value
    return snapshot


def sanitize_host(url: str) -> str:
    """去掉 URL 里可能携带的账号口令，只保留 scheme://host[:port]。"""
    try:
        parts = urlsplit(url or "")
        netloc = parts.hostname or ""
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
    except ValueError:
        return ""
    return f"{parts.scheme}://{netloc}" if netloc else ""


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def file_sha256(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.rstrip("\r\n") if result.returncode == 0 else None


def git_info(directory: str | Path) -> dict[str, Any]:
    """压测脚本所在仓库的版本；有未提交改动时列出文件（reports/ 下的产物不算）。"""
    cwd = Path(directory)
    status = _git(["status", "--porcelain", "--", ".", ":(exclude)reports"], cwd)
    return {
        "commit": _git(["rev-parse", "--short=12", "HEAD"], cwd),
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd),
        "dirty_files": [line[3:] for line in status.splitlines()] if status else [],
    }


def runtime_info() -> dict[str, str]:
    try:
        import locust

        locust_version = locust.__version__
    except (ImportError, AttributeError):
        locust_version = ""
    return {
        "locust": locust_version,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "hostname": platform.node(),
        "argv": " ".join(sys.argv[1:]),
    }


def canonical(data: Any) -> str:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def make_config_id(comparable: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(comparable).encode("utf-8")).hexdigest()[:12]


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """嵌套 dict 展平成 a.b.c 形式；列表和标量整体作为叶子比较。"""
    if isinstance(data, dict) and data:
        flat: dict[str, Any] = {}
        for key, value in data.items():
            flat.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
        return flat
    return {prefix: data}


def diff(left: dict[str, Any], right: dict[str, Any]) -> list[tuple[str, Any, Any]]:
    flat_left, flat_right = flatten(left), flatten(right)
    return [
        (key, flat_left.get(key, ABSENT), flat_right.get(key, ABSENT))
        for key in sorted(set(flat_left) | set(flat_right))
        if flat_left.get(key, ABSENT) != flat_right.get(key, ABSENT)
    ]


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
