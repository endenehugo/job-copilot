"""pytest 公共夹具。"""

from __future__ import annotations

import sys
from pathlib import Path

# 保证从任意目录运行 pytest 时都能导入 backend/app 包
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.utils import ResourceUtils


@pytest.fixture(scope="session", autouse=True)
def _isolated_resources(tmp_path_factory):
    """把资源目录指到临时目录，避免单测读写真实 resources/。"""
    ResourceUtils.init(str(tmp_path_factory.mktemp("resources")))
    yield
