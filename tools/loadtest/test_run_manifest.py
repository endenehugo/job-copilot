from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import diff_config
from run_manifest import ABSENT, diff, env_snapshot, git_info, make_config_id, runtime_info, sanitize_host, write_json


class EnvSnapshotTests(unittest.TestCase):
    def test_masks_credentials_but_keeps_plain_settings(self) -> None:
        snapshot = env_snapshot(
            {
                "LT_ADMIN_TOKEN": "secret-token",
                "LT_BASIC_AUTH_USER": "alice",
                "LT_BASIC_AUTH_PASSWORD": "pw",
                "LT_TOKEN": "",
                "LT_USERS": "2",
                "LT_MODEL": "qwen3.8-flash",
                "PATH": "/usr/bin",
            }
        )
        self.assertEqual(snapshot["LT_ADMIN_TOKEN"], "<set>")
        self.assertEqual(snapshot["LT_BASIC_AUTH_USER"], "<set>")
        self.assertEqual(snapshot["LT_BASIC_AUTH_PASSWORD"], "<set>")
        self.assertEqual(snapshot["LT_TOKEN"], "")
        # LT_USERS 是并发数，不能因为名字含 USER 被当成凭据遮蔽
        self.assertEqual(snapshot["LT_USERS"], "2")
        self.assertEqual(snapshot["LT_MODEL"], "qwen3.8-flash")
        self.assertNotIn("PATH", snapshot)
        self.assertNotIn("secret-token", json.dumps(snapshot))


class HostTests(unittest.TestCase):
    def test_strips_credentials_and_path(self) -> None:
        self.assertEqual(sanitize_host("https://user:pw@displaydemo.top/api"), "https://displaydemo.top")
        self.assertEqual(sanitize_host("http://127.0.0.1:18080"), "http://127.0.0.1:18080")
        self.assertEqual(sanitize_host(""), "")


class ConfigIdTests(unittest.TestCase):
    def test_stable_across_key_order_and_sensitive_to_values(self) -> None:
        base = {"users": 2, "model": "m", "queries": ["a", "b"]}
        reordered = {"queries": ["a", "b"], "model": "m", "users": 2}
        self.assertEqual(make_config_id(base), make_config_id(reordered))
        self.assertNotEqual(make_config_id(base), make_config_id({**base, "users": 3}))
        self.assertNotEqual(make_config_id(base), make_config_id({**base, "queries": ["b", "a"]}))

    def test_diff_flattens_nested_values_and_marks_absent(self) -> None:
        left = {"model": "a", "timeouts": {"read": 120, "connect": 5}, "queries": ["x"]}
        right = {"model": "b", "timeouts": {"read": 60, "connect": 5}, "extra": 1}
        changes = {key: (old, new) for key, old, new in diff(left, right)}
        self.assertEqual(
            changes,
            {
                "model": ("a", "b"),
                "timeouts.read": (120, 60),
                "queries": (["x"], ABSENT),
                "extra": (ABSENT, 1),
            },
        )
        self.assertEqual(diff(left, left), [])


class GitInfoTests(unittest.TestCase):
    def test_outside_a_repository_returns_empty_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            info = git_info(temp)
        self.assertEqual(set(info), {"commit", "branch", "dirty_files"})
        self.assertEqual(info["dirty_files"], [])


class RuntimeInfoTests(unittest.TestCase):
    def test_includes_hostname_and_platform(self) -> None:
        info = runtime_info()
        self.assertTrue(info["hostname"])
        self.assertTrue(info["platform"])


class DiffConfigCliTests(unittest.TestCase):
    @staticmethod
    def _manifest(directory: Path, name: str, comparable: dict, label: str) -> str:
        path = directory / name
        write_json(
            path,
            {
                "config_id": make_config_id(comparable),
                "comparable": comparable,
                "run": {"label": label, "git": {"commit": "abc", "dirty_files": []}},
            },
        )
        return str(path)

    def _run(self, *paths: str) -> tuple[int, str]:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = diff_config.main(["diff_config.py", *paths])
        return code, buffer.getvalue()

    def test_identical_configs_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            comparable = {"model": "m", "users": 2}
            a = self._manifest(root, "a_ai_config.json", comparable, "r1")
            b = self._manifest(root, "b_ai_config.json", comparable, "r2")
            code, output = self._run(a, b)
        self.assertEqual(code, 0)
        self.assertIn("可直接比较", output)

    def test_changed_configs_list_the_difference_and_exit_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            a = self._manifest(root, "a_ai_config.json", {"model": "m1", "users": 2}, "r1")
            b = self._manifest(root, "b_ai_config.json", {"model": "m2", "users": 2}, "r2")
            code, output = self._run(a, b)
        self.assertEqual(code, 1)
        self.assertIn("model: 'm1' → 'm2'", output)
        self.assertNotIn("users", output.split("配置不一致", 1)[1])


if __name__ == "__main__":
    unittest.main()
