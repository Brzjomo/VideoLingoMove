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
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import subtitle_limits as sl  # noqa: E402
from core import subtitle_split as ss  # noqa: E402
from core import step5_splitforsub as s5  # noqa: E402


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


class TestTranslationPartsGuard(unittest.TestCase):
    """译文对齐结果的机械校验：只拦"确定坏了"的结果（2026-09-20 用户实测两类问题）。

    ① 重复连接词：`…程度 同时` + `同时也看重…` → 拼接出现两个"同时"；
    ② 孤立碎片：某段只剩 1~2 个字。
    ⚠️ 仍**不**拦"数字 | 软件"这种"切开固定短语"里"下一段以普通字开头"的情形：机械校验看不出来
    （两侧都不算碎片），它由提示词里的黑名单规则负责（见 core/prompts_storage.get_align_prompt 第 5 条）。
    但**以悬空助词开头**的切法（`…原型 | 的软件`、`… | を大切に`）自 2026-09-20 起由
    `subtitle_split.dangling_start` 拦下 —— 见 TestAlignPartsRewriteGuard。
    """

    def test_repeated_connective_is_rejected(self):
        parts = ["重视能将其魅力发挥到何种程度 同时", "同时也看重它作为立体作品本身的魅力"]
        self.assertFalse(ss.translation_parts_ok(parts, "重视能将其魅力发挥到何种程度，同时也看重它作为立体作品本身的魅力"))

    def test_dropped_words_are_rejected(self):
        self.assertFalse(ss.translation_parts_ok(["能制作手办", "原型的软件有好几款"],
                                                 "能制作手办和车库套件原型的软件有好几款"))

    def test_rewritten_part_is_rejected(self):
        self.assertFalse(ss.translation_parts_ok(["能制作手办和 GK 原型的软件", "大约有好几款"],
                                                 "能制作手办和 GK 原型的软件有好几款"))

    def test_isolated_fragment_is_rejected(self):
        self.assertFalse(ss.translation_parts_ok(["能制作手办和 GK 原型的", "软件"],
                                                 "能制作手办和 GK 原型的软件"))

    def test_clean_split_is_accepted(self):
        parts = ["能制作手办和车库套件（GK）原型的数字", "软件有好几款"]
        self.assertTrue(ss.translation_parts_ok(parts, "能制作手办和车库套件（GK）原型的数字软件有好几款"),
                        "两侧都不算碎片时不该被拦（这类交给提示词黑名单，不是机械能判的）")

    def test_punctuation_and_spacing_are_normalized(self):
        self.assertTrue(ss.translation_parts_ok(["你好，世界有多大", "欢迎回来看看"],
                                                "你好，世界有多大 欢迎回来看看"))

    def test_empty_or_single_part_is_rejected(self):
        self.assertFalse(ss.translation_parts_ok([], "任意"))
        self.assertFalse(ss.translation_parts_ok(["只有一段"], "只有一段"))
        self.assertFalse(ss.translation_parts_ok(["前半", "   "], "前半"))


class TestAlignPartsRewriteGuard(unittest.TestCase):
    """B 方案（2026-09-20 用户批准）：允许在切点**轻改写**，但仍拦住重复/漏译/长度暴涨/碎片。

    用户原话："就拿'作为自由原型师约6年，一直从事手办造型工作。'为例，翻译的没错啊，只是不够好"，
    同时要求"按你这方案，重复这个问题应该也不会再发了"。因此这里钉住两件事：
      * 轻改写必须能过（补/删连接词、补足助词，让一行单独读起来成句）；
      * 重复照样拦：原文已有的说法在拼接里变多（历史事故"同时"），以及**原文没有、拼接里却出现
        两次**的说法（`同时同时也…`，旧规则按原文计数会漏掉它）。
    """

    SENT = "作为自由原型师约6年，一直从事手办造型工作。"

    def test_verbatim_split_passes(self):
        ok, reason = ss.check_align_parts(["作为自由原型师约6年", "一直从事手办造型工作"], self.SENT)
        self.assertTrue(ok, reason)

    def test_light_rewrite_is_allowed(self):
        """补"已经"、删"一直" —— 正是用户想要的顺句，不该被拦。"""
        ok, reason = ss.check_align_parts(["作为自由原型师已经约6年", "从事手办造型工作"], self.SENT)
        self.assertTrue(ok, reason)

    def test_connective_added_at_the_join_is_allowed(self):
        original = "能制作手办和车库套件原型的软件有好几款"
        ok, reason = ss.check_align_parts(["能制作手办和车库套件原型的软件", "目前有好几款"], original)
        self.assertTrue(ok, reason)

    def test_dangling_particle_start_is_rejected(self):
        """目标语以悬空助词开头（`…原型 | 的软件`）→ 拦下，且要求"移动边界"而不是"补全句子"。"""
        original = "能制作手办和车库套件原型的软件有好几款"
        ok, reason = ss.check_align_parts(["能制作手办和车库套件原型", "的软件有好几款"], original)
        self.assertFalse(ok)
        self.assertIn("dangling", reason)
        self.assertIn("Move the boundary", reason)

    def test_dangling_start_is_rejected_in_strict_mode_too(self):
        original = "能制作手办和车库套件原型的软件有好几款"
        ok, reason = ss.check_align_parts(["能制作手办和车库套件原型", "的软件有好几款"],
                                          original, allow_rewrite=False)
        self.assertFalse(ok)
        self.assertIn("dangling", reason)

    def test_dangling_start_helper_stays_narrow(self):
        """窄集合：只收 を/の/的；能起头的假名与"但是我也……"这类合法开头一律不拦。"""
        self.assertEqual(ss.dangling_start("を大切にしています"), "を")
        self.assertEqual(ss.dangling_start("の软件"), "の")
        self.assertEqual(ss.dangling_start("的软件"), "的")
        for safe in ("的确如此", "的话，我就去", "ので、そうします", "でも、それは",
                     "软件有好几款", "但是我也去", "できるようになった", "はじめまして"):
            self.assertEqual(ss.dangling_start(safe), "", safe)

    def test_repeated_connective_is_still_rejected(self):
        """历史事故：原文 1 个"同时"、拼接 2 个 → 必须拦，且原因里点名那个词。"""
        original = "重视能将其魅力发挥到何种程度，同时也看重它作为立体作品本身的魅力"
        parts = ["重视能将其魅力发挥到何种程度 同时", "同时也看重它作为立体作品本身的魅力"]
        ok, reason = ss.check_align_parts(parts, original)
        self.assertFalse(ok)
        self.assertIn("同时", reason)

    def test_new_phrase_repeated_twice_is_rejected(self):
        """原文写的是"同样"、改写后冒出两个"同时" —— 按原文计数抓不到，必须靠"新说法出现两次"。"""
        original = "他同样很重视手办作品本身的魅力，这一点一直没变过"
        parts = ["他同样很重视手办作品本身的魅力", "同时同时这一点一直没变过"]
        ok, reason = ss.check_align_parts(parts, original)
        self.assertFalse(ok)
        self.assertIn("同时", reason)

    def test_original_own_repetition_is_not_punished(self):
        """原文本身就重复的词（"东京…东京…"）不算重复，别误伤。"""
        original = "我们去了东京，东京的街道非常热闹，人也很多"
        parts = ["我们去了东京", "东京街道非常热闹，人也很多"]
        ok, reason = ss.check_align_parts(parts, original)
        self.assertTrue(ok, reason)

    def test_gross_omission_is_rejected(self):
        original = "能制作手办和车库套件原型的软件有好几款"
        parts = ["这种软件大家都在用", "评价一直都挺不错"]
        ok, reason = ss.check_align_parts(parts, original)
        self.assertFalse(ok)
        self.assertIn("content", reason)

    def test_length_inflation_is_rejected(self):
        original = "软件有好几款"
        parts = ["能制作手办和 GK 原型的软件", "软件有好几款而且都非常好用"]
        ok, reason = ss.check_align_parts(parts, original)
        self.assertFalse(ok)
        self.assertIn("length", reason)

    def test_isolated_fragment_is_rejected(self):
        ok, reason = ss.check_align_parts(["作为自由原型师约6年", "的"], self.SENT)
        self.assertFalse(ok)
        self.assertIn("fragment", reason)

    def test_strict_mode_still_forbids_any_rewrite(self):
        ok, reason = ss.check_align_parts(["作为自由原型师已经约6年", "从事手办造型工作"],
                                          self.SENT, allow_rewrite=False)
        self.assertFalse(ok)
        self.assertIn("exactly", reason)

    def test_latin_duplicated_word_is_rejected(self):
        original = "The software that can make figures and garage kits is quite common"
        parts = ["The software that can make figures and garage kits", "also also is quite common"]
        ok, reason = ss.check_align_parts(parts, original)
        self.assertFalse(ok)
        self.assertIn("also", reason)

    def test_latin_light_rewrite_is_allowed(self):
        original = "The software that can make figures and garage kits is quite common"
        parts = ["The software that can make figures and garage kits", "in fact is quite common"]
        ok, reason = ss.check_align_parts(parts, original)
        self.assertTrue(ok, reason)


class TestAlignStats(unittest.TestCase):
    """跑完打印的"拦下/回退"计数（判断 B 方案值不值的依据，用户 2026-09-20 要求）。"""

    def test_summary_is_silent_when_nothing_happened(self):
        s5.reset_align_stats()
        self.assertEqual(s5.align_stats_summary(), "")

    def test_summary_reports_rewrite_and_fallback(self):
        s5.reset_align_stats()
        s5._bump_align_stat("rows")
        s5._bump_align_stat("rewritten")
        s5._bump_align_stat("rejected", "'同时' appears 2 times")
        s5._bump_align_stat("fallback", "第 3 行 Exception")
        text = s5.align_stats_summary()
        self.assertIn("轻改写 1 行", text)
        self.assertIn("拦下 1 次", text)
        self.assertIn("退回机械切分 1 行", text)
        self.assertIn("同时", text)
        s5.reset_align_stats()


class TestAlignFallback(unittest.TestCase):
    """LLM 对齐不可用时（校验连续失败/网络失败）退回机械等分，绝不写出重复/碎片。"""

    def test_falls_back_to_even_split_when_align_raises(self):
        limits = limits_for()
        src = ["デジタルでフィギュアやガレージキットの原型を作ることができるソフトは複数存在しますが、"]
        tr = ["能制作手办和车库套装（GK）原型的数字软件有好几款"]
        with mock.patch.object(s5, "resolve_limits", return_value=limits), \
                mock.patch.object(s5, "use_llm_sentence_split", return_value=True), \
                mock.patch.object(s5, "get_source_language", return_value="ja"), \
                mock.patch.object(s5, "_boundary_window", return_value=0.15), \
                mock.patch.object(s5, "split_sentence", return_value="前半\n后半"), \
                mock.patch.object(s5, "ask_gpt", side_effect=ValueError("validation failed")), \
                mock.patch.object(s5, "load_key", return_value=1):
            out_src, out_tr, _ = s5.split_align_subs(list(src), list(tr))
        self.assertEqual(len(out_src), len(out_tr))
        self.assertGreaterEqual(len(out_tr), 2, "应当退回等分切分而不是放弃切分")
        self.assertEqual("".join(out_tr), tr[0], "等分切分必须拼接回原译文（无重复/漏词）")


class TestMergeBrokenCuts(unittest.TestCase):
    """出口守卫：不许把词切开（2026-09-20 事故 —— step3_1 把「として」劈成两行）。"""

    def test_cut_inside_a_word_is_merged(self):
        lines = ["そのキャラクターやイラストの魅力を探り、フィギュアとし",
                 "てそのキャラクターやイラストの魅力をどこまで引き出せるかを重視しつつ、"]
        merged = ss.merge_broken_cuts(lines, lambda text: set())   # 没有任何合法切点
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0], lines[0] + lines[1])

    def test_legal_boundary_is_kept(self):
        lines = ["最初の文です。", "次の文です。"]
        merged = ss.merge_broken_cuts(lines, lambda text: set(range(len(text) + 1)))
        self.assertEqual(merged, lines)

    def test_leading_particle_is_always_merged(self):
        """即使看似在 token 边界上，下一行以助词/接续开头也判为坏切点。"""
        lines = ["これはテスト", "ですが、続きます。"]      # 以助词「で」开头
        merged = ss.merge_broken_cuts(lines, lambda text: set(range(len(text) + 1)))
        self.assertEqual(len(merged), 1)

    def test_particle_rule_can_be_disabled(self):
        lines = ["これはテスト", "ですが、続きます。"]
        merged = ss.merge_broken_cuts(lines, lambda text: set(range(len(text) + 1)),
                                      join_leading_particles=False)
        self.assertEqual(len(merged), 2)

    def test_blank_lines_are_skipped(self):
        merged = ss.merge_broken_cuts(["A文です。", "  ", "B文です。"],
                                      lambda text: set(range(len(text) + 1)))
        self.assertEqual(merged, ["A文です。", "B文です。"])

    def test_boundary_function_failure_keeps_lines(self):
        """拿不到边界信息时不乱并（宁可少并，也不要把正常句子缝在一起）。"""
        def boom(_text):
            raise RuntimeError("nlp 不可用")

        lines = ["A文です。", "B文です。"]
        self.assertEqual(ss.merge_broken_cuts(lines, boom), lines)


class TestStripTerminalPunctuation(unittest.TestCase):
    """只去**行尾**句末标点（原文/译文都去），句中一律保持现状（2026-09-20 用户要求）。"""

    def test_cjk_terminal_marks_removed(self):
        for text, expected in (("これは文です。", "これは文です"),
                               ("本当に？", "本当に"),
                               ("すごい！", "すごい"),
                               ("そうですね…", "そうですね"),
                               ("続きます、", "続きます"),
                               ("也看重它作为立体作品本身的魅力。", "也看重它作为立体作品本身的魅力")):
            with self.subTest(text=text):
                self.assertEqual(ss.strip_terminal_punctuation(text), expected)

    def test_latin_terminal_marks_removed(self):
        self.assertEqual(ss.strip_terminal_punctuation("Hello world."), "Hello world")
        self.assertEqual(ss.strip_terminal_punctuation("Really?!"), "Really")

    def test_closing_quote_removed_only_after_punctuation(self):
        self.assertEqual(ss.strip_terminal_punctuation("これは文です。」"), "これは文です")
        self.assertEqual(ss.strip_terminal_punctuation("「はい」"), "「はい」",
                         "单独出现（前面不是标点）的收尾引号要保留")

    def test_mid_sentence_punctuation_is_kept(self):
        for text in ("そのキャラクターやイラストの魅力を探り、フィギュアとして",
                     "重视能将其魅力发挥到何种程度 同时",
                     "Hello, and welcome"):
            with self.subTest(text=text):
                self.assertEqual(ss.strip_terminal_punctuation(text), text)

    def test_no_punctuation_is_unchanged(self):
        self.assertEqual(ss.strip_terminal_punctuation("短句"), "短句")
        self.assertEqual(ss.strip_terminal_punctuation(""), "")

    def test_none_and_nan_become_empty_not_the_string_nan(self):
        """空单元不能变成字符串 "nan"/"None" 写进字幕。"""
        self.assertEqual(ss.strip_terminal_punctuation(None), "")
        self.assertEqual(ss.strip_terminal_punctuation(float("nan")), "")

    def test_repeated_marks_are_all_removed(self):
        self.assertEqual(ss.strip_terminal_punctuation("本当に！！"), "本当に")
        self.assertEqual(ss.strip_terminal_punctuation("本当に。」  "), "本当に")


if __name__ == "__main__":
    unittest.main(verbosity=2)
