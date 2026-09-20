"""按语言的字幕长度档位：档位表、语言归一化、两侧取档、以及"段数恒等"不变量。

背景（2026-09-20）：这两个参数原本是**所有语言共用一个数**（`max_length: 75`），而且
源文侧按字符数、译文侧按显示宽度×multiplier —— 同一块屏幕两套尺子：中文源文要 75 个字
才触发切分（常规是 12–16 字/行），中文译文 ≈36 字就触发、英文译文 ≈62 字符。
现在改成"统一度量（显示宽度）+ 两侧各按自己语言的档位"，由 `core/subtitle_limits.py`
提供，`step3_2` / `step5` 调它。

顺带钉住一个既有隐患：`split_text_evenly` 以前在"译文比要切的段数还短"或"某段 strip 后为空"
时返回**少于 n 段**，两侧段数不等 → 拍平后所有后续行错位，写 xlsx 时还会抛
`ValueError: arrays must all be same length`。现在它要么返回**恰好 n 段**，要么返回 None
（调用方整行保留、两列成对），`split_align_subs` 出口再加一道不变量。

全部离线：不联网、不读真实 config（需要配置的地方都用 mock 顶掉）。
"""

import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import subtitle_limits as sl  # noqa: E402
from core import step5_splitforsub as s5  # noqa: E402


def limits_for(src, tgt, mono=False, **kwargs):
    """按语言解析档位（显式传参，不读 config）。"""
    return sl.resolve_limits(source_language=src, target_language=tgt,
                             transcription_only=mono, auto=True,
                             manual={}, overrides=kwargs.pop("overrides", {}) or {})


class TestProfiles(unittest.TestCase):
    def test_table_and_fallback_are_sane(self):
        for code, (width, tokens) in sl.LENGTH_PROFILES.items():
            with self.subTest(language=code):
                self.assertGreaterEqual(width, 30, "太窄会把整句切碎（见模块 docstring 的实战回修）")
                self.assertLessEqual(width, 70, "比历史默认 75 还宽就没有约束力了")
                self.assertGreaterEqual(tokens, 12)
                self.assertLessEqual(tokens, 30)

    def test_calibrated_values(self):
        """2026-09-20 两次实战回修后的校准值：**整句优先**。

        第一次按 Netflix 取 26（≈15 字/行）→ 一句话被切成 2~3 条；
        第二次取 42 → 45~80 宽度的句子仍在切（用户："还是不少比较碎的"）；
        现在取 60（≈34 字/行）+ 15% 宽容量（实际阈值 69 宽度 ≈ 39 字）。
        改这些值请同时读 `core/subtitle_limits.py` 的 docstring。
        """
        self.assertEqual(sl.LENGTH_PROFILES["ja"][0], 60)
        self.assertEqual(sl.LENGTH_PROFILES["zh"][0], 60)
        self.assertEqual(sl.GRACE, 1.15)
        self.assertEqual(sl.chars_cap("ja", sl.LENGTH_PROFILES["ja"][0]), 34)   # ≈34 字/行
        self.assertEqual(sl.chars_cap("zh", sl.LENGTH_PROFILES["zh"][0]), 34)

    def test_cjk_limits_are_narrower_than_latin(self):
        """显示宽度单位下：中日 60 宽度 ≈ 34 字、拉丁 70 字符 —— 数字不同才等价。"""
        zh_width, _ = sl.LENGTH_PROFILES["zh"]
        en_width, _ = sl.LENGTH_PROFILES["en"]
        self.assertLess(zh_width, en_width)
        self.assertEqual(sl.chars_cap("ko", sl.LENGTH_PROFILES["ko"][0]), 32)
        self.assertEqual(sl.chars_cap("en", en_width), en_width)

    def test_language_overrides(self):
        width, tokens, fallback = sl.profile_for(
            "zh", {"zh": {"max_length": 30, "max_split_length": 22, "别的键": 1}})
        self.assertEqual((width, tokens, fallback), (30.0, 22, False))
        # 非法类型要被忽略，不能把上限改成字符串（回落内置档位）
        width, tokens, _ = sl.profile_for("zh", {"zh": {"max_length": "宽"}})
        self.assertEqual((width, tokens), (float(sl.LENGTH_PROFILES["zh"][0]),
                                           sl.LENGTH_PROFILES["zh"][1]))
        self.assertTrue(sl.profile_for("xx", None)[2], "未知语言应回退兜底档")


class TestNormalizeLanguage(unittest.TestCase):
    def test_codes_and_aliases(self):
        for raw, expected in (("ja", "ja"), ("JA", "ja"), ("zh-CN", "zh"), ("en-US", "en"),
                              ("pt-BR", "pt"), ("ko_KR", "ko"), ("jp", "ja")):
            with self.subTest(raw=raw):
                self.assertEqual(sl.normalize_language(raw), expected)

    def test_free_text_names(self):
        """`target_language` 是自由文本（默认 '简体中文'），靠关键词命中。"""
        for raw, expected in (("简体中文", "zh"), ("繁體中文", "zh"), ("中文", "zh"),
                              ("日语", "ja"), ("日本語", "ja"), ("英语", "en"),
                              ("English", "en"), ("韩语", "ko"), ("한국어", "ko"),
                              ("西班牙语", "es"), ("俄语", "ru")):
            with self.subTest(raw=raw):
                self.assertEqual(sl.normalize_language(raw), expected)

    def test_unknown_is_none_not_guessed(self):
        """识别不出就返回 None —— 调用方据此**不动配置**，绝不猜错语言。"""
        for raw in ("auto", "", "   ", "自动检测", "火星文", "klingon", None, 42):
            with self.subTest(raw=raw):
                self.assertIsNone(sl.normalize_language(raw))


class TestResolveLimits(unittest.TestCase):
    def test_bilingual_uses_each_side_language(self):
        limits = limits_for("en", "简体中文")
        self.assertTrue(limits.auto)
        self.assertEqual((limits.source_code, limits.target_code), ("en", "zh"))
        self.assertEqual(limits.src_limit, sl.LENGTH_PROFILES["en"][0])
        self.assertEqual(limits.tr_limit, sl.LENGTH_PROFILES["zh"][0])
        self.assertEqual(limits.max_split_length, sl.LENGTH_PROFILES["en"][1])  # 粗切看源文
        self.assertEqual((limits.src_char_cap, limits.tr_char_cap),
                         (sl.chars_cap("en", sl.LENGTH_PROFILES["en"][0]),
                          sl.chars_cap("zh", sl.LENGTH_PROFILES["zh"][0])))

    def test_transcription_only_uses_recognition_language_for_both_sides(self):
        limits = limits_for("ja", "简体中文", mono=True)
        self.assertEqual((limits.source_code, limits.target_code), ("ja", "ja"))
        self.assertEqual((limits.src_limit, limits.tr_limit),
                         (float(sl.LENGTH_PROFILES["ja"][0]),
                          float(sl.LENGTH_PROFILES["ja"][0])))
        self.assertEqual(limits.max_split_length, sl.LENGTH_PROFILES["ja"][1])
        self.assertFalse(limits.fallback)

    def test_unknown_language_falls_back(self):
        limits = limits_for("", "")
        self.assertTrue(limits.fallback)
        self.assertEqual((limits.src_limit, limits.tr_limit),
                         (float(sl.FALLBACK_PROFILE[0]), float(sl.FALLBACK_PROFILE[0])))

    def test_manual_mode_keeps_old_behaviour(self):
        """关掉开关时行为必须与改动前**逐字一致**：源文按字符、译文按宽度×multiplier。"""
        limits = sl.resolve_limits(
            source_language="ja", target_language="简体中文", transcription_only=False,
            auto=False, overrides={},
            manual={"max_length": 75, "max_split_length": 20, "target_multiplier": 1.2})
        self.assertFalse(limits.auto)
        self.assertEqual(limits.src_unit, "chars")
        self.assertEqual(limits.src_limit, 75)
        self.assertAlmostEqual(limits.tr_limit, 75 / 1.2)
        self.assertEqual(limits.max_split_length, 20)

    def test_exceeds_is_per_side(self):
        limits = limits_for("ja", "简体中文")
        limit = limits.src_limit          # 60 → 实际阈值 60×1.15 = 69
        self.assertFalse(sl.exceeds(limit, limit, limits))
        self.assertTrue(sl.exceeds(limit * 1.25, 10, limits))    # 源文明显超
        self.assertTrue(sl.exceeds(10, limit * 1.25, limits))    # 译文明显超

    def test_grace_keeps_borderline_lines_whole(self):
        """只超一点点不切：为 1~2 个字符把一句话拆成两条，观感更差。"""
        limits = limits_for("ja", "简体中文")
        limit = limits.src_limit
        self.assertFalse(sl.exceeds(limit * 1.10, 0, limits), "超 10% 应当整条保留")
        self.assertTrue(sl.exceeds(limit * 1.20, 0, limits), "超 20% 才切")
        self.assertFalse(sl.exceeds(0, limit * 1.10, limits))
        self.assertTrue(sl.exceeds(0, limit * 1.20, limits))

    def test_parts_needed_takes_the_stricter_side_and_caps_at_three(self):
        limits = limits_for("ja", "简体中文")     # 60 / 60
        self.assertEqual(sl.parts_needed(30, 30, limits), 1)
        self.assertEqual(sl.parts_needed(70, 10, limits), 2)      # 70/60 → 2
        self.assertEqual(sl.parts_needed(10, 130, limits), 3)     # 译文侧更严
        self.assertEqual(sl.parts_needed(400, 400, limits), 3)    # 上限 3 段

    def test_real_world_japanese_sentences_stay_whole(self):
        """实战回修的直接回归：这些句子各占一条字幕，**不许**被切。

        数据来自两次真实运行（第一版 26 / 第二版 42 都被切碎，用户两次反馈）。
        """
        limits = limits_for("ja", "简体中文")
        for sentence in ("こんにちは、フリーのフィギュア原型師のミロです",
                         "ミロという名義で商業原型師として活動しています",
                         "ワンダーフェスティバルに参加するようになりました",
                         "大学時代は彫刻を専攻しており3年生の時にガレージキットを作り始め",
                         "フィギュアメーカーや原型製作会社に就職したことはありませんが、",
                         "美少女フィギュアのデータ原型制作をメインに行っており",
                         "またガレージキットやフィギュアの原型を作る上で重要なのが分割の知識です",
                         "3Dプリントが可能であることシリコンの2面型で複製ができるような分割であること"):
            with self.subTest(sentence=sentence):
                self.assertFalse(sl.exceeds(sl.measure(sentence), 0, limits),
                                 f"{sentence} 应当整条保留")
        # 真正的长句仍然要切（88 宽度 → 2 段；146 宽度 → 3 段）
        for long_one, expected in (
                ("2020年に制作したアグネスタキオンのガレージキットはアルター様から"
                 "完成品フィギュアとして発売されました", 2),
                ("個人活動としてのガレージキット製作は、大学3年生の頃から始め、版権物や"
                 "オリジナル作品を製作し、今も好きなものを作って、ワンダーフェスティバル"
                 "などのイベントに参加しています。", 3)):
            with self.subTest(sentence=long_one[:12]):
                units = sl.measure(long_one)
                self.assertTrue(sl.exceeds(units, 0, limits))
                self.assertEqual(sl.parts_needed(units, 0, limits), expected)

    def test_config_values_store_the_target_side(self):
        """写回 config 时单行上限取"主行"（双语=译文；单语=同一种语言）。"""
        self.assertEqual(sl.config_values(limits_for("en", "简体中文")),
                         {"subtitle.max_length": sl.LENGTH_PROFILES["zh"][0],
                          "max_split_length": sl.LENGTH_PROFILES["en"][1]})
        self.assertEqual(sl.config_values(limits_for("ja", "简体中文", mono=True)),
                         {"subtitle.max_length": sl.LENGTH_PROFILES["ja"][0],
                          "max_split_length": sl.LENGTH_PROFILES["ja"][1]})


class TestSplitTextEvenly(unittest.TestCase):
    """"段数恰好是 n" 是不变量：段数不等会让后面所有行错位、写 xlsx 时直接抛错。"""

    def test_exactly_n_parts(self):
        for text, n in (("你好世界，这是测试", 2), ("abcd", 2), ("a b c d", 3),
                        ("ab", 2), ("一二三四五", 3)):
            with self.subTest(text=text, n=n):
                parts = s5.split_text_evenly(text, n)
                self.assertIsNotNone(parts, f"{text!r} 应当能切成 {n} 段")
                self.assertEqual(len(parts), n)
                self.assertTrue(all(part.strip() for part in parts), "不允许空段")

    def test_returns_none_when_impossible(self):
        """可见字符比段数还少时**不许假装成功**（否则会出现"只显示一侧"的字幕）。"""
        for text, n in (("x", 2), ("", 2), ("  ", 3), ("a", 3)):
            with self.subTest(text=text, n=n):
                self.assertIsNone(s5.split_text_evenly(text, n))

    def test_no_split_needed_returns_none(self):
        self.assertIsNone(s5.split_text_evenly("随便什么", 1))
        self.assertIsNone(s5.split_text_evenly("随便什么", 0))

    def test_content_is_preserved_without_whitespace(self):
        parts = s5.split_text_evenly("一二三四五六", 3)
        self.assertEqual("".join(parts), "一二三四五六")


class TestAlignSubsInvariant(unittest.TestCase):
    """`split_align_subs` 必须保持"源/译段数相等"，否则整行保留。"""

    def setUp(self):
        self.limits = sl.SubtitleLimits(
            auto=True, source_code="zh", target_code="zh",
            max_split_length=sl.LENGTH_PROFILES["zh"][1],
            src_limit=sl.LENGTH_PROFILES["zh"][0], tr_limit=sl.LENGTH_PROFILES["zh"][0],
            src_unit="width",
            src_char_cap=sl.chars_cap("zh", sl.LENGTH_PROFILES["zh"][0]),
            tr_char_cap=sl.chars_cap("zh", sl.LENGTH_PROFILES["zh"][0]),
        )

    def _run(self, src, tr):
        """只跑机械路径（use_llm=False），避免任何 LLM 调用；max_workers 固定 1。

        ⚠️ 必须连 `get_source_language` / `_boundary_window` 一起打桩：它们会读真实
        `config.yaml` 与 cwd（全量跑时别的用例会改 cwd），那样这条用例的结果会随环境漂移
        —— 之前就是因此在本文件单跑通过、全量跑却失败。
        """
        with mock.patch.object(s5, "resolve_limits", return_value=self.limits), \
                mock.patch.object(s5, "use_llm_sentence_split", return_value=False), \
                mock.patch.object(s5, "get_source_language", return_value="zh"), \
                mock.patch.object(s5, "_boundary_window", return_value=0.15), \
                mock.patch.object(s5, "load_key", return_value=1):
            return s5.split_align_subs(list(src), list(tr))

    def test_long_rows_are_split_and_stay_paired(self):
        # 这条要有意超过"上限×宽容量"（60×1.15=69 宽度 ≈ 39 字）才会被切
        src = ["这是一句很长的中文源文，需要被切开才行，否则一行放不下啊，真的放不下，"
               "而且还要更长一点才能超过宽容量。"]
        tr = ["这是一句很长的中文译文，需要被切开才行，否则一行放不下啊，真的放不下，"
              "而且还要更长一点才能超过宽容量。"]
        out_src, out_tr, _ = self._run(src, tr)
        self.assertEqual(len(out_src), len(out_tr), "两侧段数必须相等")
        self.assertGreater(len(out_src), 1, "超长行应该被切开")
        self.assertTrue(all(part.strip() for part in out_src + out_tr))

    def test_one_char_translation_keeps_the_row_whole(self):
        """译文只有 1 个字符、源文很长：整行保留（两侧都还在），绝不产出空的一侧。"""
        src = ["这是一句很长的中文源文，需要被切开才行，否则一行放不下啊，真的放不下，"
               "而且还要更长一点才能超过宽容量。"]
        tr = ["好"]
        out_src, out_tr, _ = self._run(src, tr)
        self.assertEqual((out_src, out_tr), (src, tr))
        self.assertEqual(len(out_src), len(out_tr))

    def test_short_rows_are_untouched(self):
        src, tr = ["短句"], ["短句"]
        out_src, out_tr, _ = self._run(src, tr)
        self.assertEqual((out_src, out_tr), (src, tr))

    def test_row_overflows_uses_limits(self):
        short = "短"
        # "很长的一句中文文本"×4 = 36 字 ≈ 63 宽度 → 在 60×1.15 之内，仍算整条保留
        borderline = "很长的一句中文文本" * 4
        # ×6 = 54 字 ≈ 94.5 宽度 → 明确超限
        too_long = "很长的一句中文文本" * 6
        self.assertFalse(s5.row_overflows(short, short, self.limits))
        self.assertFalse(s5.row_overflows(borderline, short, self.limits),
                         "只超一点点（宽容量内）不该切")
        self.assertTrue(s5.row_overflows(too_long, short, self.limits))
        self.assertTrue(s5.row_overflows(short, too_long, self.limits))


class TestApplyLanguageProfile(unittest.TestCase):
    """切语言时的写回：打开开关**无条件覆盖**；关闭则一个字节都不动。"""

    def _apply(self, auto, **kwargs):
        written = {}
        config = {
            "subtitle.auto_length_by_language": auto,
            "transcription_only": False,
            "subtitle.length_profiles": {},
            "whisper.language": "ja",
            "whisper.detected_language": "ja",
            "target_language": "简体中文",
            "subtitle.max_length": 75,
            "max_split_length": 20,
            "subtitle.target_multiplier": 1.2,
        }

        def fake_update(key, value):
            written[key] = value
            return True

        def fake_load(key, default=None):
            return config.get(key, default)

        # 用假 config 顶掉读写：既验证"按语言算出来的值"，也不会碰真实 config.yaml
        with mock.patch("core.config_utils.update_key", side_effect=fake_update), \
                mock.patch("core.config_utils.load_key_or", side_effect=fake_load):
            limits, changed = sl.apply_language_profile(**kwargs)
        return limits, changed, written

    def test_auto_on_overwrites(self):
        limits, changed, written = self._apply(auto=True)
        self.assertTrue(limits.auto)
        expected = {"subtitle.max_length": sl.LENGTH_PROFILES["zh"][0],
                    "max_split_length": sl.LENGTH_PROFILES["ja"][1]}
        self.assertEqual(written, expected)
        self.assertEqual(changed, written)

    def test_auto_off_writes_nothing(self):
        _, changed, written = self._apply(auto=False)
        self.assertEqual(written, {})
        self.assertEqual(changed, {})

    def test_force_applies_profile_even_when_auto_is_off(self):
        """「恢复当前语言推荐值」按钮：开关关着也要能一键套用档位。"""
        _, changed, written = self._apply(auto=False, force=True)
        self.assertEqual(written, {"subtitle.max_length": sl.LENGTH_PROFILES["zh"][0],
                                   "max_split_length": sl.LENGTH_PROFILES["ja"][1]})
        self.assertEqual(changed, written)


class TestStep1EntrypointsWired(unittest.TestCase):
    """静态守卫：step3_2 / step5 必须走 resolve_limits，别再直接读那两个键。"""

    def test_step3_2_uses_resolved_token_limit(self):
        src = pathlib.Path("core/step3_2_splitbymeaning.py").read_text(encoding="utf-8")
        self.assertIn("limits = resolve_limits()", src)
        self.assertIn("max_length=limits.max_split_length", src)
        self.assertNotIn('max_length=load_key("max_split_length")', src)

    def test_step5_has_no_direct_max_length_reads(self):
        src = pathlib.Path("core/step5_splitforsub.py").read_text(encoding="utf-8")
        self.assertNotIn("MAX_SUB_LENGTH", src)
        self.assertNotIn("TARGET_SUB_MULTIPLIER", src)
        self.assertIn("row_overflows", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
