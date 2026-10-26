"""比较多轮压测的 *_ai_config.json，只看会影响结果可比性的配置差异。

    python diff_config.py reports/20261026/ai-s2-r1_ai_config.json reports/20261026/ai-s2-r2_ai_config.json

以第一个文件为基准逐个比较。所有 config_id 都相同时退出码为 0，否则为 1。
config_id 相同说明目标、模型、并发、请求数、问题集、脚本内容和被测服务版本都一致；
不同时会列出具体哪一项变了。run 段的信息（git、时间、实际请求数）只作对照展示。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from run_manifest import diff

SHOWN_RUN_KEYS = ("label", "started_at", "planned_chat_requests", "actual_chat_requests", "ended_reason")


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run_view(manifest: dict) -> dict:
    run = manifest.get("run", {})
    view = {key: run.get(key) for key in SHOWN_RUN_KEYS}
    git = run.get("git", {})
    view["git"] = f"{git.get('commit')}{' (dirty)' if git.get('dirty_files') else ''}"
    return view


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    base_path, others = argv[1], argv[2:]
    base = _load(base_path)
    print(f"基准：{base_path}\n  config_id={base.get('config_id')}  {_run_view(base)}")
    identical = True
    for path in others:
        other = _load(path)
        changes = diff(base.get("comparable", {}), other.get("comparable", {}))
        same = not changes
        identical &= same
        print(f"\n对比：{path}\n  config_id={other.get('config_id')}  {_run_view(other)}")
        if same:
            print("  配置一致，可直接比较耗时。")
            continue
        print("  配置不一致，直接比较耗时前先解释下列差异：")
        for key, left, right in changes:
            print(f"    - {key}: {left!r} → {right!r}")
    return 0 if identical else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
