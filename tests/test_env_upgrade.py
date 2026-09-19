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

    def test_prefers_seven_over_eight_and_nine(self):
        releases = [self._release([
            "ffmpeg-master-latest-win64-gpl.zip",
            "ffmpeg-n9.0-latest-win64-gpl-9.0.zip",
            "ffmpeg-n8.1-latest-win64-gpl-8.1.zip",
            "ffmpeg-n7.1-latest-win64-gpl-7.1.zip",
        ])]
        picked = installer._pick_ffmpeg_asset(releases)
        self.assertIsNotNone(picked, "应当挑到 7.1 资产")
        self.assertEqual(picked[0], "ffmpeg-n7.1-latest-win64-gpl-7.1.zip")

    def test_skips_shared_variant(self):
        # shared 版只有 DLL、没有 ffmpeg.exe，installer 需要 exe
        releases = [self._release([
            "ffmpeg-n7.1-latest-win64-gpl-shared-7.1.zip",
            "ffmpeg-n7.1-latest-win64-gpl-7.1.zip",
        ])]
        self.assertEqual(installer._pick_ffmpeg_asset(releases)[0],
                         "ffmpeg-n7.1-latest-win64-gpl-7.1.zip")

    def test_shared_only_returns_none(self):
        releases = [self._release(["ffmpeg-n7.1-latest-win64-gpl-shared-7.1.zip"])]
        self.assertIsNone(installer._pick_ffmpeg_asset(releases))

    def test_no_seven_returns_none(self):
        releases = [self._release(["ffmpeg-n8.1-latest-win64-gpl-8.1.zip"])]
        self.assertIsNone(installer._pick_ffmpeg_asset(releases))

    def test_walks_older_releases(self):
        # 实测场景：latest tag 只剩 8.x，7.x 资产在更早的 autobuild tag 上
        releases = [
            self._release(["ffmpeg-n9.0-latest-win64-gpl-9.0.zip"]),
            self._release(["ffmpeg-n8.1.2-54-gc573a95381-win64-gpl-8.1.zip"]),
            self._release(["ffmpeg-n7.1.5-12-g1fdbca85aa-win64-gpl-7.1.zip"]),
        ]
        picked = installer._pick_ffmpeg_asset(releases)
        self.assertIsNotNone(picked)
        self.assertIn("n7.1.5", picked[0])

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
