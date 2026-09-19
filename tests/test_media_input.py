"""输入媒体识别的回归测试（find_media_file / input_manifest / 产物过滤）。

覆盖两处曾经真实出错的地方：
  1. find_video_files 的过滤条件 `not file.startswith("output/output")` 两个
     方向都错（误删标题以 output 开头的合法输入；误收大写扩展名的自家产物）。
  2. 上传音频时被包成 black_screen.mp4，下游只能靠文件名特判。
"""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core import step1_ytdlp  # noqa: E402


class MediaInputTest(unittest.TestCase):
    # 测试用格式白名单。用桩替换 step1_ytdlp.load_key，让本模块完全不依赖
    # config.yaml / config.example.yaml（否则测试会因为找不到配置而报
    # FileNotFoundError，与"没有视频文件"的 FileNotFoundError 混淆）。
    VIDEO_FORMATS = ["mp4", "mov", "avi", "mkv", "flv", "wmv", "webm"]
    AUDIO_FORMATS = ["wav", "mp3", "flac", "m4a"]

    def setUp(self):
        self._orig_load_key = step1_ytdlp.load_key

        def fake_load_key(key):
            if key == "allowed_video_formats":
                return self.VIDEO_FORMATS
            if key == "allowed_audio_formats":
                return self.AUDIO_FORMATS
            return self._orig_load_key(key)

        step1_ytdlp.load_key = fake_load_key

        self.tmpdir = Path(tempfile.mkdtemp())
        self.output = self.tmpdir / "output"
        self.output.mkdir()
        self._orig_cwd = os.getcwd()
        os.chdir(self.tmpdir)  # find_* 默认 save_path='output' 是相对路径

    def tearDown(self):
        step1_ytdlp.load_key = self._orig_load_key
        os.chdir(self._orig_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _touch(self, name, data=b"x"):
        path = self.output / name
        path.write_bytes(data)
        return str(path)

    # ---------------- 产物过滤 ----------------

    def test_output_prefixed_input_is_kept(self):
        """标题以 output 开头的合法输入不能被误删（旧前缀过滤的 bug）。"""
        self._touch("output tutorial.mp4")
        found = step1_ytdlp.find_video_files()
        self.assertTrue(found.endswith("output tutorial.mp4"))

    def test_generated_outputs_are_excluded(self):
        self._touch("clip.mp4")
        self._touch("output_sub.mp4")
        self._touch("output_dub.mp4")
        self.assertTrue(step1_ytdlp.find_video_files().endswith("clip.mp4"))

    def test_generated_outputs_excluded_case_insensitively(self):
        """大写扩展名的自家产物也必须被排除（旧前缀过滤会误收）。"""
        self._touch("clip.mp4")
        self._touch("OUTPUT_SUB.MP4")
        self.assertTrue(step1_ytdlp.find_video_files().endswith("clip.mp4"))

    def test_audio_placeholder_is_still_a_valid_source(self):
        """black_screen.mp4 是旧路径上传音频产生的合法源文件，不能排除。"""
        self._touch("black_screen.mp4")
        self.assertTrue(step1_ytdlp.find_video_files().endswith("black_screen.mp4"))

    # ---------------- 音频输入 ----------------

    def test_audio_only_input_detected_without_manifest(self):
        self._touch("podcast.mp3")
        path, media_type = step1_ytdlp.find_media_file()
        self.assertEqual(media_type, "audio")
        self.assertTrue(step1_ytdlp.is_audio_only_input())

    def test_manifest_takes_precedence_over_extension_scan(self):
        audio = self._touch("podcast.mp3")
        self._touch("clip.mp4")
        step1_ytdlp.write_input_manifest(audio, "audio")
        _, media_type = step1_ytdlp.find_media_file()
        self.assertEqual(media_type, "audio")

    def test_stale_manifest_self_heals(self):
        """清单指向已删除的文件时应自动失效并回退，而不是报错。"""
        self._touch("clip.mp4")
        step1_ytdlp.write_input_manifest(str(self.output / "gone.mp3"), "audio")
        path, media_type = step1_ytdlp.find_media_file()
        self.assertEqual(media_type, "video")
        self.assertTrue(path.endswith("clip.mp4"))

    def test_generated_audio_excluded_from_audio_scan(self):
        self._touch("podcast.mp3")
        self._touch("dub.mp3")
        self.assertTrue(step1_ytdlp.find_audio_files().endswith("podcast.mp3"))

    def test_no_media_raises_file_not_found(self):
        """没有素材时必须抛 FileNotFoundError —— UI 据此显示上传界面。"""
        empty = self.tmpdir / "empty"
        empty.mkdir()
        with self.assertRaises(FileNotFoundError):
            step1_ytdlp.find_media_file(str(empty))

    # ---------------- 时长探测 ----------------

    def test_probe_duration_raises_clear_error_on_missing_file(self):
        with self.assertRaises(RuntimeError):
            step1_ytdlp.probe_duration(str(self.output / "nope.mp4"))


if __name__ == "__main__":
    unittest.main()
