"""批量模式默认输入目录的测试。

背景：`batch/input/` 在 `.gitignore` 里（`.gitignore` 的 `batch/input/`），
所以刚克隆或刚搬迁的仓库里没有这个目录。旧实现只在下游
`video_processor.py` 里 `os.makedirs`，而界面的默认路径分支更早就
`if not os.path.exists(folder_path): st.error(...); return` —— 于是必然报
「❌ 目录不存在: <项目根>/batch/input」，永远走不到创建那一步。

逻辑被抽到 `batch/utils/batch_paths.py`（零依赖），因此这里不需要
torch / streamlit 就能测。
"""

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "batch", "utils"))

import batch_paths  # noqa: E402


class TestDefaultInputDir(unittest.TestCase):
    def test_relative_location_is_batch_input(self):
        self.assertEqual(batch_paths.DEFAULT_INPUT_RELATIVE,
                         os.path.join("batch", "input"))

    def test_default_dir_under_project_root(self):
        path = batch_paths.default_input_dir("/tmp/some-project")
        self.assertEqual(path, os.path.join("/tmp/some-project", "batch", "input"))

    def test_root_dir_points_at_this_repo(self):
        """ROOT_DIR 应该是项目根（含 core/ 与 batch/），不是 batch 或 utils。"""
        root = pathlib.Path(batch_paths.ROOT_DIR)
        self.assertTrue((root / "core").is_dir(), root)
        self.assertTrue((root / "batch").is_dir(), root)
        self.assertTrue((root / "installer.py").is_file(), root)

    def test_default_dir_matches_gui_expectation(self):
        """必须与界面算出来的默认路径一致：<root>/batch/input。"""
        expected = os.path.join(batch_paths.ROOT_DIR, "batch", "input")
        self.assertEqual(batch_paths.default_input_dir(), expected)


class TestEnsureDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_missing_dir(self):
        target = self.root / "batch" / "input"
        path, created, error = batch_paths.ensure_dir(str(target))
        self.assertTrue(created)
        self.assertIsNone(error)
        self.assertTrue(target.is_dir())
        self.assertEqual(path, str(target))

    def test_idempotent(self):
        target = self.root / "batch" / "input"
        batch_paths.ensure_dir(str(target))
        _path, created, error = batch_paths.ensure_dir(str(target))
        self.assertFalse(created, "第二次调用不应报告“新建”")
        self.assertIsNone(error)

    def test_existing_dir_is_left_alone(self):
        target = self.root / "keep"
        target.mkdir()
        (target / "existing.txt").write_text("x", encoding="utf-8")
        _path, created, error = batch_paths.ensure_dir(str(target))
        self.assertFalse(created)
        self.assertIsNone(error)
        self.assertTrue((target / "existing.txt").is_file(), "不应动已有内容")

    def test_failure_returns_error_instead_of_raising(self):
        """失败必须返回错误信息（界面要显示出来），而不是抛异常崩掉整页。"""
        if os.name == "nt":
            # Windows 上用一个非法字符造出必然失败的路径
            bad = str(self.root / "bad<>:|?*name")
        else:
            bad = "/proc/nonexistent/cannot/create"
        _path, created, error = batch_paths.ensure_dir(bad)
        self.assertFalse(created)
        self.assertIsNotNone(error, "应返回错误信息")
        self.assertIsInstance(error, str)

    def test_nested_creation(self):
        target = self.root / "a" / "b" / "c"
        _path, created, error = batch_paths.ensure_dir(str(target))
        self.assertTrue(created)
        self.assertIsNone(error)
        self.assertTrue(target.is_dir())


class TestEnsureDefaultInputDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_creates_batch_input_under_given_root(self):
        path, created, error = batch_paths.ensure_default_input_dir(self.root)
        self.assertTrue(created)
        self.assertIsNone(error)
        self.assertEqual(path, os.path.join(self.root, "batch", "input"))
        self.assertTrue(os.path.isdir(path))

    def test_second_call_is_noop(self):
        batch_paths.ensure_default_input_dir(self.root)
        _path, created, error = batch_paths.ensure_default_input_dir(self.root)
        self.assertFalse(created)
        self.assertIsNone(error)

    def test_real_repo_root_case(self):
        """回归：仓库里 batch/input 不存在时，也必须能被创建出来。

        这条用例直接对着真实项目根跑 —— 因为 bug 的现象正是
        「默认路径必然报目录不存在」。测完复原，不留痕迹。
        """
        target = pathlib.Path(batch_paths.default_input_dir())
        pre_existing = target.is_dir()
        if pre_existing:
            self.skipTest("batch/input 已存在，跳过（避免动用户数据）")
        try:
            path, created, error = batch_paths.ensure_default_input_dir()
            self.assertTrue(created, "默认输入目录应被自动创建")
            self.assertIsNone(error)
            self.assertTrue(os.path.isdir(path))
        finally:
            shutil.rmtree(target, ignore_errors=True)
            self.assertFalse(target.exists(), "测试后应清理掉临时创建的目录")


if __name__ == "__main__":
    unittest.main(verbosity=2)
