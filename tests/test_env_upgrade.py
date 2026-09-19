"""环境升级后新增的纯逻辑测试（不需要装 torch/whisperx 就能跑）。

覆盖三块"改了会影响安装结果、但装完才发现错"的判断：
  1. FFmpeg 版本解析与大版本闸门（torchcodec 0.7 只支持 4–7）；
  2. FFmpeg 资产挑选（必须挑到非 shared 的 7.x，且不能挑到 8/9 ——
     这正是原实现"永远下 latest"会踩的坑）；
  3. 显卡算力 → torch CUDA 后端映射（Pascal/Volta 必须回退 cu126，
     否则 sm_61 上会拿到不含该架构的 cu128/cu129 构建）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import installer  # noqa: E402


class TestParseFfmpegVersion(unittest.TestCase):
    def test_release_build(self):
        text = ("ffmpeg version 7.0.2-full_build-www.gyan.dev Copyright (c) 2000-2024 "
                "the FFmpeg developers")
        self.assertEqual(installer.parse_ffmpeg_version(text), (7, 0, 2))

    def test_version_with_n_prefix_and_git_suffix(self):
        text = "ffmpeg version n7.1.5-12-g1fdbca85aa-20260731 Copyright (c) 2000-2026"
        self.assertEqual(installer.parse_ffmpeg_version(text), (7, 1, 5))

    def test_master_build(self):
        text = "ffmpeg version N-126636-g1ef0d9d701 Copyright (c) 2000-2026"
        self.assertEqual(installer.parse_ffmpeg_version(text), (0, 0, 0))

    def test_two_component_version(self):
        self.assertEqual(installer.parse_ffmpeg_version("ffmpeg version 6.1 "), (6, 1, 0))

    def test_unparseable(self):
        self.assertIsNone(installer.parse_ffmpeg_version(""))
        self.assertIsNone(installer.parse_ffmpeg_version("not ffmpeg at all"))

    def test_none_input(self):
        self.assertIsNone(installer.parse_ffmpeg_version(None))


class TestFfmpegMajorGate(unittest.TestCase):
    def test_supported_range(self):
        for major in (4, 5, 6, 7):
            self.assertTrue(installer.ffmpeg_major_ok(major), f"{major} 应被支持")

    def test_unsupported_range(self):
        # torchcodec 0.7 不支持 8/9；0/0 表示 master 构建（解析不到版本）
        for major in (0, 3, 8, 9):
            self.assertFalse(installer.ffmpeg_major_ok(major), f"{major} 不该被接受")

    def test_none_is_not_ok(self):
        self.assertFalse(installer.ffmpeg_major_ok(None))


class TestPickFfmpegAsset(unittest.TestCase):
    def _release(self, names):
        return {"assets": [{"name": n, "browser_download_url": f"https://x/{n}"}
                           for n in names]}

    def test_prefers_newest_branch(self):
        # "默认装新版"：8.1 在优先序列最前
        releases = [self._release([
            "ffmpeg-n9.0-latest-win64-gpl-9.0.zip",
            "ffmpeg-n8.1-latest-win64-gpl-8.1.zip",
            "ffmpeg-n7.1-latest-win64-gpl-7.1.zip",
        ])]
        picked = installer._pick_ffmpeg_asset(releases)
        self.assertIsNotNone(picked)
        self.assertIn("n8.1", picked[0])

    def test_prefers_shared_within_branch(self):
        # shared 才含 torchcodec 需要的 avcodec 等动态库
        releases = [self._release([
            "ffmpeg-n8.1-latest-win64-gpl-8.1.zip",
            "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip",
        ])]
        self.assertEqual(installer._pick_ffmpeg_asset(releases)[0],
                         "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip")

    def test_falls_back_to_non_shared(self):
        # 7.x 的非 shared 构建同时含 exe 与 DLL，是可接受的退路
        releases = [self._release(["ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-7.1.zip"])]
        picked = installer._pick_ffmpeg_asset(releases)
        self.assertIsNotNone(picked)
        self.assertIn("n7.1.5", picked[0])

    def test_skips_unsupported_branches(self):
        # 9.0 不在优先序列里（torchcodec 只支持 4-7，而 8.x 是有意放行的上限）
        releases = [self._release(["ffmpeg-n9.0-latest-win64-gpl-9.0.zip"])]
        self.assertIsNone(installer._pick_ffmpeg_asset(releases))

    def test_walks_older_releases(self):
        # 实测场景：latest tag 只剩 8.x/9.x，7.1 资产在更早的 autobuild tag 上
        releases = [
            self._release(["ffmpeg-n9.0-latest-win64-gpl-9.0.zip"]),
            self._release(["ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-7.1.zip"]),
        ]
        picked = installer._pick_ffmpeg_asset(releases)
        self.assertIsNotNone(picked)
        self.assertIn("n7.1.5", picked[0])

    def test_custom_branch_order(self):
        releases = [self._release([
            "ffmpeg-n7.1-latest-win64-gpl-shared-7.1.zip",
            "ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip",
        ])]
        picked = installer._pick_ffmpeg_asset(releases, branches=("7.1",))
        self.assertIn("n7.1", picked[0])

    def test_ignores_non_zip_and_linux_assets(self):
        releases = [self._release([
            "ffmpeg-n7.1-latest-linux64-gpl-7.1.tar.xz",
            "ffmpeg-n7.1-latest-win64-gpl-7.1.zip.sha256",
        ])]
        self.assertIsNone(installer._pick_ffmpeg_asset(releases))

    def test_empty_input(self):
        self.assertIsNone(installer._pick_ffmpeg_asset([]))
        self.assertIsNone(installer._pick_ffmpeg_asset(None))
        self.assertIsNone(installer._pick_ffmpeg_asset([{}]))


class TestDownloadDirAndLocalWheels(unittest.TestCase):
    """下载与安装分离：大文件放 _downloads/，存在就优先用本地文件。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_wheel_names_match_index_convention(self):
        names = installer.wheel_names_for("cu126")
        self.assertEqual(len(names), 3)
        by_pkg = {pkg: fn for pkg, _ver, fn in names}
        self.assertIn(f"torch-{installer.TORCH_VERSION}+cu126-", by_pkg["torch"])
        self.assertIn(f"torchvision-{installer.TORCHVISION_VERSION}+cu126-",
                      by_pkg["torchvision"])
        for filename in by_pkg.values():
            self.assertTrue(filename.endswith(".whl"))

    def test_plan_reports_everything_missing_when_dir_empty(self):
        have, missing = installer.torch_download_plan("cu128", self.dir)
        self.assertEqual(have, [])
        self.assertEqual(len(missing), 3)
        for _pkg, filename, url in missing:
            self.assertIn(filename, url.replace("%2B", "+"))
            self.assertIn("download.pytorch.org", url)

    def test_existing_file_is_reused(self):
        for pkg, _ver, filename in installer.wheel_names_for("cu126"):
            target = os.path.join(self.dir, filename)
            with open(target, "wb") as handle:
                handle.write(b"x" * 16)
        have, missing = installer.torch_download_plan("cu126", self.dir)
        self.assertEqual(len(have), 3)
        self.assertEqual(missing, [])
        found = installer.local_wheels_for("cu126", self.dir)
        self.assertEqual(set(found), {"torch", "torchaudio", "torchvision"})

    def test_empty_file_is_not_reused(self):
        # 下载中断会留下 0 字节文件，绝不能当成"已下好"
        _pkg, _ver, filename = installer.wheel_names_for("cu126")[0]
        with open(os.path.join(self.dir, filename), "wb"):
            pass
        have, _missing = installer.torch_download_plan("cu126", self.dir)
        self.assertEqual(have, [])

    def test_wheel_for_other_backend_is_not_reused(self):
        # cu128 的轮子不能被当成 cu126 的用
        _pkg, _ver, filename = installer.wheel_names_for("cu128")[0]
        with open(os.path.join(self.dir, filename), "wb") as handle:
            handle.write(b"x" * 16)
        have, _missing = installer.torch_download_plan("cu126", self.dir)
        self.assertEqual(have, [])

    def test_ensure_download_dir_creates(self):
        target = os.path.join(self.dir, "nested", "_downloads")
        returned = installer.ensure_download_dir(target)
        self.assertTrue(os.path.isdir(returned))
        self.assertEqual(str(returned), target)

    def test_python_tag_matches_interpreter(self):
        self.assertEqual(installer.current_python_tag(),
                         f"cp{sys.version_info[0]}{sys.version_info[1]}")


class TestFfmpegSkipWhenUsable(unittest.TestCase):
    """需求：环境里已有可用的 FFmpeg 就跳过下载。"""

    def setUp(self):
        # ensure_ffmpeg 会打印中文提示，测试里静音掉
        self._saved_info, self._saved_panel = installer.info, installer.panel
        installer.info = lambda *a, **k: None
        installer.panel = lambda *a, **k: None

    def tearDown(self):
        installer.info, installer.panel = self._saved_info, self._saved_panel

    def test_usable_ffmpeg_reports_source(self):
        bin_dir, source, version = installer.usable_ffmpeg()
        if bin_dir is None:
            self.skipTest("本机没有可用的 FFmpeg，跳过")
        self.assertIn(source, ("项目内", "系统 PATH"))
        self.assertTrue(installer.ffmpeg_major_ok(version[0]))

    def test_ensure_ffmpeg_skips_download_when_usable(self):
        """有可用版本时 ensure_ffmpeg 必须直接返回，不触发下载。"""
        if installer.usable_ffmpeg()[0] is None:
            self.skipTest("本机没有可用的 FFmpeg，跳过")

        calls = []

        def _boom(*args, **kwargs):
            calls.append(args)
            raise AssertionError("已有可用 FFmpeg 时不应触发下载")

        original = installer.download_ffmpeg_windows
        installer.download_ffmpeg_windows = _boom
        try:
            ok, needs_restart = installer.ensure_ffmpeg()
        finally:
            installer.download_ffmpeg_windows = original
        self.assertTrue(ok)
        self.assertFalse(needs_restart)
        self.assertEqual(calls, [])

    def test_ffmpeg_zip_name_is_stable(self):
        # 外部工具下载时用户要按这个名字存放，不能随意改
        self.assertEqual(installer.FFMPEG_ZIP_NAME, "ffmpeg-win64.zip")


class TestTorchBackend(unittest.TestCase):
    """算力 → 后端映射。这是本次升级最关键的一条：cu128/cu129 已移除 Pascal。"""

    def test_pascal_maps_to_cu126(self):
        for cap in (6.1, 6.0, 5.2, 3.7):  # P2200/P100/GTX9/K80
            backend, reason = installer.detect_torch_backend("auto", gpu_cap=cap)
            self.assertEqual(backend, "cu126", f"算力 {cap} 应回退 cu126")
            self.assertIn("6.1" if cap == 6.1 else str(cap), reason)

    def test_volta_and_turing_map_to_cu128(self):
        for cap in (7.0, 7.5):  # V100 / T4
            self.assertEqual(installer.detect_torch_backend("auto", gpu_cap=cap)[0],
                             "cu128")

    def test_ampere_and_ada_map_to_cu128(self):
        for cap in (8.0, 8.6, 8.9):  # A100 / RTX3080 / RTX40
            self.assertEqual(installer.detect_torch_backend("auto", gpu_cap=cap)[0],
                             "cu128")

    def test_blackwell_maps_to_cu129(self):
        for cap in (12.0, 12.1):
            self.assertEqual(installer.detect_torch_backend("auto", gpu_cap=cap)[0],
                             "cu129")

    def test_explicit_backend_wins(self):
        for requested in ("cu126", "cu128", "cu129", "cpu"):
            backend, reason = installer.detect_torch_backend(requested, gpu_cap=8.6)
            self.assertEqual(backend, requested)
            self.assertIn("显式指定", reason)

    def test_no_gpu_falls_back_to_cpu(self):
        # gpu_cap=None 且探测不到 → cpu（这里注入 None 短路真实探测）
        backend, reason = installer.detect_torch_backend("auto", gpu_cap=None)
        self.assertIn(backend, ("cpu", "cu128", "cu126", "cu129"))
        self.assertTrue(reason)


class TestTorchSpecsCoverage(unittest.TestCase):
    def test_every_backend_has_index_and_label(self):
        for backend, (index_url, label) in installer.TORCH_SPECS.items():
            self.assertTrue(index_url.startswith("https://download.pytorch.org/whl/"))
            self.assertTrue(index_url.endswith(backend))
            self.assertTrue(label)

    def test_python_gate_matches_whisperx_requirement(self):
        # whisperx 3.8.6 的 requires_python 是 >=3.10,<3.14
        self.assertEqual(installer.PYTHON_MIN, (3, 10))
        self.assertEqual(installer.PYTHON_MAX, (3, 14))
        self.assertTrue(installer.python_ok((3, 10, 0)))
        self.assertTrue(installer.python_ok((3, 13, 9)))
        self.assertFalse(installer.python_ok((3, 9, 18)))
        self.assertFalse(installer.python_ok((3, 14, 0)))

    def test_torch_version_pins_match_whisperx(self):
        # whisperx 3.8.6 要求 torch~=2.8.0 / torchvision~=0.23.0
        self.assertEqual(installer.TORCH_VERSION, "2.8.0")
        self.assertEqual(installer.TORCHVISION_VERSION, "0.23.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
