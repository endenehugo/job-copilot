"""pytest 公共夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证从任意目录运行 pytest 时都能导入 backend/app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.core.config import settings
from app.utils import ResourceUtils


@pytest.fixture(scope="session", autouse=True)
def _isolated_resources(tmp_path_factory):
    """把资源目录指到临时目录，避免单测读写真实 resources/。"""
    ResourceUtils.init(str(tmp_path_factory.mktemp("resources")))
    yield


@pytest.fixture(autouse=True)
def _no_llm_usage_log(monkeypatch):
    """默认关掉 LLM 计量落库：没有 DB 打桩的测试（perf_trace 系）否则会把
    llm_usage 行写进本机真实 MySQL——既污染开发库，每次落库还要等连接。
    需要真跑计量的用例（test_llm_usage_quota）自己用 monkeypatch 打开。"""
    monkeypatch.setattr(settings, "llm_usage_log", False)


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """默认关掉限流：app.main 是全套件共享的单例，计数跨测试累计，
    第 11 次 chat 就会把无关用例打成 429。限流自身的测试（test_rate_limit）
    自建独立 app，并在自己的夹具里显式打开。"""
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
