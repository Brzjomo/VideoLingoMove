"""转录缓存的回归测试。

重点保护一处**与上游不同的关键设计**：缓存身份必须包含 asr_engine。
上游只有 local/elevenlabs 两种运行时，而 dev 是 whisper/volcano；两者分段命名
完全相同（都是 f"{start}_{end}"、都从 (0, duration) 起步），不区分引擎就会把
火山的结果喂给 Whisper 流程、反之亦然。
"""

import math
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.all_whisper_methods import transcription_cache as tc  # noqa: E402


class CacheKeyTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.media = self.tmpdir / "video.mp4"
        self.media.write_bytes(b"FAKEVIDEO" * 1000)
        self._orig_cache_dir = tc.CACHE_DIR
        tc.CACHE_DIR = self.tmpdir / "asr_cache"
        self.whisper = {"asr_engine": "whisper", "model": "large-v3",
                        "language": "zh", "demucs": False}

    def tearDown(self):
        tc.CACHE_DIR = self._orig_cache_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_same_settings_same_key(self):
        self.assertEqual(tc.cache_key(self.media, self.whisper),
                         tc.cache_key(self.media, self.whisper))

    def test_engine_is_part_of_identity(self):
        volcano = dict(self.whisper, asr_engine="volcano",
                       volcano={"model_version": "400"})
        self.assertNotEqual(tc.cache_key(self.media, self.whisper),
                            tc.cache_key(self.media, volcano))

    def test_volcano_params_are_part_of_identity(self):
        a = dict(self.whisper, asr_engine="volcano", volcano={"model_version": "400"})
        b = dict(self.whisper, asr_engine="volcano", volcano={"model_version": "310"})
        self.assertNotEqual(tc.cache_key(self.media, a), tc.cache_key(self.media, b))

    def test_model_and_language_are_part_of_identity(self):
        self.assertNotEqual(tc.cache_key(self.media, self.whisper),
                            tc.cache_key(self.media, dict(self.whisper, model="large-v3-turbo")))
        self.assertNotEqual(tc.cache_key(self.media, self.whisper),
                            tc.cache_key(self.media, dict(self.whisper, language="en")))

    def test_rename_does_not_change_key(self):
        renamed = self.tmpdir / "renamed.mp4"
        shutil.copy(self.media, renamed)
        self.assertEqual(tc.cache_key(self.media, self.whisper),
                         tc.cache_key(renamed, self.whisper))

    def test_different_content_changes_key(self):
        other = self.tmpdir / "other.mp4"
        other.write_bytes(b"OTHER" * 1000)
        self.assertNotEqual(tc.cache_key(self.media, self.whisper),
                            tc.cache_key(other, self.whisper))


class CacheValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_cache_dir = tc.CACHE_DIR
        tc.CACHE_DIR = self.tmpdir / "asr_cache"
        self.key = "k" * 64
        self.good = {"segments": [
            {"start": 0.0, "end": 1.0, "text": "hi",
             "words": [{"word": "hi", "start": 0.0, "end": 1.0}]}
        ], "language": "zh"}

    def tearDown(self):
        tc.CACHE_DIR = self._orig_cache_dir
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_round_trip(self):
        tc.write_result(self.key, "complete", self.good, "zh")
        entry = tc.read_result(self.key, "complete")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["result"], self.good)

    def test_missing_part_returns_none(self):
        self.assertIsNone(tc.read_result(self.key, "0.00_10.00"))

    def test_invalid_results_rejected(self):
        cases = {
            "missing segments": {"language": "zh"},
            "nan timestamp": {"segments": [{"start": float("nan"), "end": 1.0,
                                            "text": "x", "words": []}]},
            "inf timestamp": {"segments": [{"start": 0.0, "end": float("inf"),
                                            "text": "x", "words": []}]},
            "end before start": {"segments": [{"start": 5.0, "end": 1.0,
                                              "text": "x", "words": []}]},
            "negative start": {"segments": [{"start": -1.0, "end": 1.0,
                                             "text": "x", "words": []}]},
            "word not str": {"segments": [{"start": 0.0, "end": 1.0, "text": "x",
                                           "words": [{"word": 123}]}]},
            "segment not dict": {"segments": ["nope"]},
            "words not list": {"segments": [{"start": 0.0, "end": 1.0,
                                             "text": "x", "words": "nope"}]},
        }
        for name, result in cases.items():
            with self.subTest(name=name):
                self.assertFalse(tc.valid_result(result))
                # 非法结果绝不写入
                tc.write_result(self.key, "bad", result, "zh")
                self.assertIsNone(tc.read_result(self.key, "bad"))

    def test_corrupt_json_returns_none(self):
        directory = tc.CACHE_DIR / self.key
        directory.mkdir(parents=True)
        (directory / "complete.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(tc.read_result(self.key, "complete"))

    def test_schema_mismatch_returns_none(self):
        directory = tc.CACHE_DIR / self.key
        directory.mkdir(parents=True)
        (directory / "complete.json").write_text(
            '{"schema": 0, "key": "%s", "language": "zh", "result": {"segments": []}}' % self.key,
            encoding="utf-8")
        self.assertIsNone(tc.read_result(self.key, "complete"))

    def test_clear_cache(self):
        tc.write_result(self.key, "complete", self.good, "zh")
        self.assertEqual(tc.clear_cache(), 1)
        self.assertIsNone(tc.read_result(self.key, "complete"))


if __name__ == "__main__":
    unittest.main()
