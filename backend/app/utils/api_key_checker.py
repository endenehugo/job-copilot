r"""DashScope API Key 检测工具。

提供两种模式：
1. 模块级：直接 import 后调用 `check_all`，适合在启动脚本里用
2. 命令行：`python -m app.utils.api_key_checker`，可直接在终端运行
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CheckResult:
    name: str
    """检测项名称，例如 'Embedding API'"""

    passed: bool = False
    """是否通过"""

    message: str = ""
    """一行结果说明"""

    detail: str = ""
    """额外诊断信息（异常栈、原始错误码等）"""


@dataclass
class CheckReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    def add(self, name: str, passed: bool, message: str, detail: str = ""):
        self.results.append(
            CheckResult(name=name, passed=passed, message=message, detail=detail),
        )

    def format_text(self) -> str:
        lines = []
        lines.append("=" * 60)
        lines.append("  DashScope API Key 检测报告")
        lines.append("=" * 60)
        for r in self.results:
            status = "✅ PASS" if r.passed else "❌ FAIL"
            lines.append(f"  {status}  {r.name}")
            lines.append(f"         {r.message}")
            if r.detail:
                for d in r.detail.strip().splitlines():
                    lines.append(f"         {d}")
        lines.append("-" * 60)
        if self.all_passed:
            lines.append("  结论：所有检测通过，API Key 可用。")
        else:
            lines.append("  结论：存在检测失败项，请检查配置。")
            lines.append("  提示：在终端执行以下命令设置有效的 Key 后重启服务 →")
            lines.append('        $env:DASHSCOPE_API_KEY="你的有效key"')
        lines.append("=" * 60)
        return "\n".join(lines)


def _get_api_key() -> tuple[str | None, str, list[str]]:
    """按项目实际加载顺序获取 API Key 及其来源。"""
    sources: list[str] = []
    final_key: str | None = None

    # 1. 检查 .env 文件（load_dotenv 会从 CWD 向上搜索）
    try:
        from dotenv import dotenv_values, find_dotenv

        dotenv_path = find_dotenv(usecwd=True)
        if dotenv_path:
            vals = dotenv_values(dotenv_path)
            if "DASHSCOPE_API_KEY" in vals:
                final_key = vals["DASHSCOPE_API_KEY"]
                sources.append(f".env ({dotenv_path})")
    except Exception:
        pass

    # 2. 检查操作系统环境变量（包括 conda activate / VS Code 设置的）
    env_val = os.environ.get("DASHSCOPE_API_KEY")
    if env_val:
        sources.append(f"系统环境变量 DASHSCOPE_API_KEY")
        final_key = env_val  # 环境变量优先于 .env

    # 3. 兜底：config_dev.py 默认值
    if not final_key:
        try:
            import importlib

            cfg = importlib.import_module("config.config_dev")
            default_key = getattr(cfg, "DASHSCOPE_API_KEY", None)
            if default_key:
                final_key = default_key
                sources.append("config_dev.py 默认值（无环境变量时使用）")
        except Exception:
            pass

    masked = _mask_key(final_key) if final_key else "--未配置--"
    return final_key, masked, sources


# 删除旧的单返回值版本（已内联到上面）
# _mask_key 移到 check_all 之后复用


def check_embedding_api(key: str) -> CheckResult:
    """用最简单的 embedding 请求验证 Key 有效性。"""
    try:
        import dashscope
    except ImportError:
        return CheckResult(
            name="Embedding API",
            passed=False,
            message="dashscope 未安装，请执行 pip install dashscope",
        )

    try:
        resp: Any = dashscope.TextEmbedding.call(
            model="text-embedding-v3",
            input=["ping"],
            api_key=key,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="Embedding API",
            passed=False,
            message="请求发送失败（网络/证书/超时）。",
            detail=str(exc),
        )

    code = _safe_status_code(resp)
    if code == 200:
        return CheckResult(
            name="Embedding API",
            passed=True,
            message="DashScope Key 有效，文本 Embedding 可正常调用。",
        )
    return CheckResult(
        name="Embedding API",
        passed=False,
        message=_describe_response(resp),
    )


def check_multimodal_api(key: str) -> CheckResult:
    """用 qwen-vl-plus 最小多模态请求验证 Key 权限。"""
    try:
        import dashscope
    except ImportError:
        return CheckResult(
            name="多模态 API",
            passed=False,
            message="dashscope 未安装。",
        )

    try:
        resp: Any = dashscope.MultiModalConversation.call(
            model="qwen-vl-plus",
            messages=[
                {
                    "role": "user",
                    "content": [{"text": "ping"}],
                },
            ],
            api_key=key,
        )
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="多模态 API",
            passed=False,
            message="请求发送失败。",
            detail=str(exc),
        )

    code = _safe_status_code(resp)
    if code == 200:
        return CheckResult(
            name="多模态 API",
            passed=True,
            message="qwen-vl-plus 多模态模型可正常调用，支持图片识别。",
        )
    return CheckResult(
        name="多模态 API",
        passed=False,
        message=_describe_response(resp),
    )


def check_all(key: str | None = None) -> CheckReport:
    """执行全部检测并返回格式化报告。"""
    if key:
        final_key = key
        sources: list[str] = ["手动指定"]
    else:
        final_key, _, sources = _get_api_key()

    report = CheckReport()

    if not final_key:
        report.add(
            name="Key 配置",
            passed=False,
            message="未找到 DASHSCOPE_API_KEY。请在环境变量或 config_dev.py 中配置。",
        )
        return report

    masked = _mask_key(final_key)
    source_info = "、".join(sources) if sources else "未知来源"
    report.add(name="Key 配置", passed=True, message=f"Key: {masked}  |  来源: {source_info}")

    report.results.append(check_embedding_api(final_key))
    report.results.append(check_multimodal_api(final_key))
    return report


def _mask_key(key: str) -> str:
    if len(key) <= 8:
        return "***"
    return key[:4] + "****" + key[-4:]


def _safe_status_code(resp: Any) -> int:
    """兼容 dashscope 不同版本返回对象的 status_code 取法。"""
    for attr in ("status_code", "code"):
        val = getattr(resp, attr, None)
        if isinstance(val, int):
            return val
    if isinstance(resp, dict):
        return int(resp.get("status_code") or resp.get("code") or 0)
    return 0


def _describe_response(resp: Any) -> str:
    code = _safe_status_code(resp)
    msg = getattr(resp, "message", "") or ""
    if not msg and isinstance(resp, dict):
        msg = resp.get("message", "")
    return f"HTTP {code}  {msg}".strip()


# ---------------------------------------------------------------
# 命令行入口：python -m app.utils.api_key_checker
# ---------------------------------------------------------------
if __name__ == "__main__":
    report = check_all()
    print(report.format_text())
    sys.exit(0 if report.all_passed else 1)
