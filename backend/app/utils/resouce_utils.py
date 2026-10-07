import json
import os

from app.core.config import settings


class ResourceUtils:
    """资源目录定位工具。

    默认使用 settings.resources_root（backend/resources）；
    测试或自定义部署可通过 init() 覆盖为任意目录。
    """

    _RESOURCE_PATH: str | None = None

    @classmethod
    def init(cls, resource_root: str) -> None:
        cls._RESOURCE_PATH = os.path.abspath(resource_root)

    @classmethod
    def get_resource_path(cls, filename: str) -> str:
        root = cls._RESOURCE_PATH or settings.resources_root
        return os.path.join(root, filename)

    @classmethod
    def ensure_resource_dir(cls, dirname: str) -> str:
        path = cls.get_resource_path(dirname)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def load_json_resource(cls, filename: str):
        with open(cls.get_resource_path(filename), "r", encoding="utf-8") as f:
            return json.load(f)

    @classmethod
    def load_text_resource(cls, filename: str) -> list[str]:
        with open(cls.get_resource_path(filename), "r", encoding="utf-8") as f:
            return f.readlines()
