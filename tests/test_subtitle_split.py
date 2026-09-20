"""句法边界切分 + 极短字幕合并（`core/subtitle_split.py`）的离线回归。

背景（2026-09-20，日语视频 ja→简体中文）：Whisper 在 `initial_prompt=""` 下几乎不输出标点
（6 分钟只有 27 个「。」），step5 只能按显示宽度硬切 → "一句话被切成两半，前半接上一条、
后半接下一条"。这里钉住三件事：

1. `split_at_boundaries`：切点在理想位置 ±window 内**改挑句法边界**；没边界就退回原位；
   不许把相邻段压成"两个字"的碎片。
2. `merge_short_cues`：时长过短的字幕并入相邻条（用户要求："否则没看清就消失了"）；
   但有护栏 —— 合并后不许超限、间隔不许太远、没有合适邻居就保持原样。
3. `parse_timestamps` / `rows_from_pairs`：时间戳解析（step5 的 `timestamp` 列是
   `"HH:MM:SS,mmm --> HH:MM:SS,mmm"` 字符串）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import subtitle_limits as sl  # noqa: E402
from core import subtitle_split as ss  # noqa: E402


def limits_for(src="ja", tgt="简体中文", mono=False):
    return sl.resolve_limits(source_language=src, target_language=tgt,
                             transcription_only=mono, auto=True, manual={}, overrides={})


class TestBoundaryCandidates(unittest.TestCase):
    def test_hard_boundaries_after_punctuation(self):
        text = "こんにちは。今日はいい天気ですね。"
        candidates = ss.boundary_candidates(text, "ja")
        self.assertIn(text.index("。") + 1, candidates)
        self.assertIn(len(text), candidates)          # 末尾句号之后

    def test_soft_boundaries_for_cjk_without_punctuation(self):
        """没有标点时用日语接续/助词边界（这是本次优化的关键）。"""
        text = "大学時代は彫刻を専攻しており3年生の時にガレージキットを作り始め"
        candidates = ss.boundary_candidates(text, "ja")
        self.assertTrue(candidates, "无标点日语也应有软边界候选")
        self.assertTrue(all(0 < pos < len(text) for pos in candidates))

    def test_latin_conjunctions(self):
        text = "I started making kits and then I joined the event"
        self.assertIn(text.index(" and ") + 1, ss.boundary_candidates(text, "en"))


class TestSplitAtBoundaries(unittest.TestCase):
    def test_picks_boundary_near_the_ideal_cut(self):
        text = "大学時代は彫刻を専攻しており、3年生の時にガレージキットを作り始めました"
        parts = ss.split_at_boundaries(text, 2, window=0.15, language="ja")
        self.assertEqual(len(parts), 2)
        self.assertEqual("".join(parts), text, "切分不能改动原文（step6 靠 Source 匹配）")
        self.assertTrue(parts[0].endswith("、") or parts[0].endswith("て"),
                        f"应当切在句法边界上，实际切在 {parts[0][-4:]!r} 之后")

    def test_window_bounds_the_shift(self):
        """切点最多偏离理想位置 ±window（不会为了边界把一段拉得很长）。"""
        text = "あ" * 40 + "。" + "い" * 40
        parts = ss.split_at_boundaries(text, 2, window=0.15, language="ja")
        self.assertAlmostEqual(len(parts[0]), 41, delta=40 * 0.15 + 2)

    def test_no_boundary_falls_back_to_even_cut(self):
        text = "あ" * 60                       # 无标点、无接续词
        parts = ss.split_at_boundaries(text, 2, window=0.15, language="ja")
        self.assertEqual([len(p) for p in parts], [30, 30])

    def test_never_creates_tiny_parts(self):
        """边界若会把相邻段压成碎片，就退回理想位置。"""
        text = "あ" * 29 + "。" + "い" * 31     # 边界在 30，理想也是 30 → 正常
        parts = ss.split_at_boundaries(text, 2, window=0.15, language="ja")
        self.assertTrue(min(len(p) for p in parts) >= 8)

    def test_single_part_is_untouched(self):
        self.assertEqual(ss.split_at_boundaries("短い", 1), ["短い"])


class TestTimestamps(unittest.TestCase):
    def test_parse_arrow_format(self):
        start, end = ss.parse_timestamps("00:01:02,500 --> 00:01:04,250")
        self.assertAlmostEqual(start, 62.5)
        self.assertAlmostEqual(end, 64.25)

    def test_parse_dot_milliseconds_and_garbage(self):
        self.assertAlmostEqual(ss.parse_timestamps("00:00:01.250 --> 00:00:02.000")[0], 1.25)
        self.assertIsNone(ss.parse_timestamps(""))
        self.assertIsNone(ss.parse_timestamps(float("nan")))

    def test_rows_from_pairs(self):
        rows = ss.rows_from_pairs(["あ"], ["啊"], ["00:00:01,000 --> 00:00:02,000"])
        self.assertEqual(rows[0]["source"], "あ")
        self.assertEqual((rows[0]["start"], rows[0]["end"]), (1.0, 2.0))


class TestMergeShortCues(unittest.TestCase):
    """极短字幕合并：默认开启，但只在"确实看不清"且邻居合适时才并。"""

    def setUp(self):
        self.limits = limits_for()

    def row(self, source, translation, start, end):
        return {"source": source, "translation": translation, "start": start, "end": end}

    def test_very_short_cue_is_merged_forward(self):
        rows = [self.row("そうですね。", "原来如此。", 0.0, 0.5),
                self.row("では始めます。", "那么我们开始吧。", 0.6, 3.0)]
        merged = ss.merge_short_cues(rows, self.limits, min_duration=0.8, max_gap=0.35)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["translation"], "原来如此。那么我们开始吧。")
        self.assertEqual((merged[0]["start"], merged[0]["end"]), (0.0, 3.0))

    def test_normal_length_cue_is_untouched(self):
        rows = [self.row("はい。", "是的。", 0.0, 1.2),
                self.row("では始めます。", "那么我们开始吧。", 1.3, 3.0)]
        self.assertEqual(len(ss.merge_short_cues(rows, self.limits)), 2)

    def test_far_apart_cues_are_not_merged(self):
        rows = [self.row("はい。", "是的。", 0.0, 0.5),
                self.row("では始めます。", "那么我们开始吧。", 5.0, 8.0)]
        merged = ss.merge_short_cues(rows, self.limits, min_duration=0.8, max_gap=0.35)
        self.assertEqual(len(merged), 2, "间隔太远不该合并")

    def test_merge_into_previous_when_no_next(self):
        rows = [self.row("では始めます。", "那么我们开始吧。", 0.0, 3.0),
                self.row("はい。", "是的。", 3.1, 3.5)]
        merged = ss.merge_short_cues(rows, self.limits, min_duration=0.8, max_gap=0.35)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["translation"], "那么我们开始吧。是的。")

    def test_merge_skipped_when_result_would_overflow(self):
        long_src = "あ" * 40                      # 70 宽度 → 再并入短句就超过 60×1.15
        long_tr = "啊" * 40
        rows = [self.row("はい。", "是的。", 0.0, 0.5),
                self.row(long_src, long_tr, 0.6, 4.0)]
        merged = ss.merge_short_cues(rows, self.limits, min_duration=0.8, max_gap=0.35)
        self.assertEqual(len(merged), 2, "合并后会超限就不许并")

    def test_single_row_never_merged(self):
        rows = [self.row("はい。", "是的。", 0.0, 0.4)]
        self.assertEqual(len(ss.merge_short_cues(rows, self.limits)), 1)

    def test_real_world_pair(self):
        """真实数据里那种"0.5 s 的短句 + 紧跟下一句"要被并成一条。"""
        rows = [self.row("ただ、難しい機能を使わなくても、", "不过 就算不使用复杂的功能", 213.8, 214.3),
                self.row("ある程度はご利用しで形を作ることが可能です。",
                         "也能在一定程度上把造型做出来", 214.4, 217.0)]
        merged = ss.merge_short_cues(rows, self.limits, min_duration=0.8, max_gap=0.35)
        self.assertEqual(len(merged), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
