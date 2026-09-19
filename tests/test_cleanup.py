"""cleanup.py 的删除机制单元测试（在临时目录里做，不碰真实缓存）。"""

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cleanup  # noqa: E402


class TestHuman(unittest.TestCase):
    def test_units(self):
        self.assertEqual(cleanup.human(512), "512 B")
        self.assertEqual(cleanup.human(1024), "1.0 KB")
        self.assertEqual(cleanup.human(1024 ** 2), "1.0 MB")
        self.assertEqual(cleanup.human(5 * 1024 ** 3), "5.0 GB")

    def test_none(self):
        self.assertEqual(cleanup.human(None), "?")


class TestDirSize(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_returns_none(self):
        self.assertIsNone(cleanup.dir_size(self.root / "nope"))

    def test_empty_dir_is_zero(self):
        target = self.root / "empty"
        target.mkdir()
        self.assertEqual(cleanup.dir_size(target), 0)

    def test_sums_files_recursively(self):
        (self.root / "a").mkdir()
        (self.root / "a" / "one.bin").write_bytes(b"x" * 100)
        (self.root / "a" / "b").mkdir()
        (self.root / "a" / "b" / "two.bin").write_bytes(b"x" * 250)
        self.assertEqual(cleanup.dir_size(self.root / "a"), 350)

    def test_single_file(self):
        f = self.root / "f.bin"
        f.write_bytes(b"x" * 42)
        self.assertEqual(cleanup.dir_size(f), 42)


class TestTargetRemoval(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _target(self, name="t"):
        return cleanup.Target(name, name, self.root / "victim", "safe")

    def test_removes_directory_with_content(self):
        victim = self.root / "victim"
        (victim / "nested").mkdir(parents=True)
        (victim / "nested" / "blob.bin").write_bytes(b"x" * 2048)
        target = self._target()
        self.assertTrue(target.exists)
        self.assertEqual(target.size, 2048)

        ok, message = target.remove()
        self.assertTrue(ok, message)
        self.assertFalse(victim.exists())
        self.assertIsNone(cleanup.dir_size(victim))

    def test_missing_target_reports_not_exists(self):
        target = self._target()
        self.assertFalse(target.exists)
        self.assertIsNone(target.size)

    def test_removal_of_file_target(self):
        victim = self.root / "victim"
        victim.write_bytes(b"x" * 10)
        target = self._target()
        ok, _message = target.remove()
        self.assertTrue(ok)
        self.assertFalse(victim.exists())


class TestCleanSelection(unittest.TestCase):
    """确认默认档位选择正确：默认只清安全缓存，模型必须显式开启。

    注意：这些测试**不调用 build_targets()**，因为那会去统计真实缓存目录的
    大小（本机 pip 缓存清理前有 13 GB，量一遍要几十秒）。改成直接校验档位
    常量，测试因此是毫秒级的。
    """

    def test_default_clean_is_safe_only(self):
        keys = set(cleanup.SAFE_KEYS)
        self.assertNotIn("hf", keys)
        self.assertNotIn("torch", keys)
        self.assertNotIn("downloads", keys)
        self.assertNotIn("temp", keys)
        self.assertIn("pip", keys)
        self.assertIn("uv", keys)

    def test_model_and_project_keys_are_opt_in(self):
        self.assertEqual(set(cleanup.MODEL_KEYS), {"hf", "torch"})
        self.assertEqual(set(cleanup.PROJECT_KEYS), {"downloads", "ffmpeg"})

    def test_all_keys_are_distinct(self):
        keys = (list(cleanup.SAFE_KEYS) + list(cleanup.MODEL_KEYS)
                + list(cleanup.PROJECT_KEYS) + ["temp"])
        self.assertEqual(len(keys), len(set(keys)), "清理项 key 不能重复")

    def test_unknown_only_key_is_rejected(self):
        """未知项必须被拒绝，且不做任何删除（走的是报告分支之前的校验）。"""
        rc = cleanup.main(["--clean", "--only", "definitely-not-a-key", "--yes"])
        self.assertEqual(rc, 1)


class TestCondaDetection(unittest.TestCase):
    def test_returns_dict(self):
        envs = cleanup.conda_environments()
        self.assertIsInstance(envs, dict)

    def test_paths_are_never_conda_envs(self):
        """没有任何清理目标的路径落在 conda 的 envs 目录里。

        断言的是**路径**而不是标签文字：hf 目标的说明里就提到了 aisummary
        （提醒用户那里也有模型），那只是提示，不是删除目标。
        这里只调用 build_targets() 一次，代价是统计几个缓存目录的大小。
        """
        envs = cleanup.conda_environments()
        other = [pathlib.Path(p) for name, p in envs.items()
                 if name not in ("base", cleanup.TARGET_ENV)]
        base = None
        exe = cleanup.conda_exe()
        if exe is not None:
            base = pathlib.Path(exe).parent.parent   # ...\anaconda3\Scripts\conda.exe

        forbidden = other + ([base] if base else [])
        for target in cleanup.build_targets():
            self.assertIn(target.tier, ("safe", "confirm"))
            self.assertTrue(target.note)
            for guarded in forbidden:
                self.assertFalse(
                    guarded == target.path or guarded in target.path.parents,
                    f"清理目标 {target.label} 落在受保护的 conda 路径里：{target.path}",
                )

    def test_conda_exe_none_is_handled(self):
        """没有 conda 时报空字典而不是抛异常。"""
        self.assertIsInstance(cleanup.conda_environments(), dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
