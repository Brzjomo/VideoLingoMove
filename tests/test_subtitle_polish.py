"""step5.2 字幕润色：护栏、开关语义与"读哪张表"的回归测试（2026-09-21 用户要求）。

背景：用户先要求"恢复顺句能力"，随后亲自纠正"不许要求每行自足"，最后明确：
"要的，但是你要在侧边栏合适的位置加个开关，打开后才做这步润色优化"。
于是润色被做成**独立可选步骤**：关着时零 LLM 调用；打开后逐行润色 + 机械护栏 + 批量审校。

这个文件钉住三件事：
  1. `subtitle_split.polish_ok`：润色允许改写措辞（这是它的目的），但拦住超长/丢信息/丢数字/重复；
  2. `config_utils.polish_translation()`：缺键必须默认**关**（老 config 不能因为读键失败就偷偷调 LLM）；
  3. `step6.pick_translation_file()`：开关关 / 文件不存在 / 行数不一致 → 一律回到未润色字幕表。
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import config_utils  # noqa: E402
from core import step5_2_polish_subs as p5  # noqa: E402
from core import step6_generate_final_timeline as s6  # noqa: E402
from core import subtitle_split as ss  # noqa: E402


class TestPolishGuard(unittest.TestCase):
    ORIGINAL = "作为自由原型师约6年，一直从事手办造型工作"

    def test_natural_rewording_is_accepted(self):
        """润色的目的就是改措辞 —— 换语序、补虚词、删冗余都该放行。"""
        for polished in ("从事手办造型工作已经约6年了，作为自由原型师",
                         "作为自由原型师已经约6年，一直在做手办造型",
                         "做自由原型师约6年，手办造型也一直没放下"):
            ok, reason = ss.polish_ok(self.ORIGINAL, polished)
            self.assertTrue(ok, f"{polished} -> {reason}")

    def test_unchanged_line_is_accepted(self):
        ok, reason = ss.polish_ok(self.ORIGINAL, self.ORIGINAL)
        self.assertTrue(ok, reason)

    def test_empty_line_is_rejected(self):
        self.assertFalse(ss.polish_ok(self.ORIGINAL, "   ")[0])

    def test_lost_number_is_rejected(self):
        """`约6年` → `好几年`：润色最常见的语义漂移，必须拦。"""
        ok, reason = ss.polish_ok(self.ORIGINAL, "作为自由原型师好几年了，一直从事手办造型工作")
        self.assertFalse(ok)
        self.assertIn("6", reason)

    def test_changed_number_is_rejected(self):
        """改成别的数字同样是丢信息：原文的 `6年` 不见了。"""
        ok, reason = ss.polish_ok(self.ORIGINAL, "作为自由原型师约8年，一直从事手办造型工作")
        self.assertFalse(ok)
        self.assertIn("6", reason)

    def test_longer_line_is_rejected(self):
        """字幕长度是硬约束：润色把行撑长会让后面又要重切。"""
        ok, reason = ss.polish_ok(self.ORIGINAL, "作为自由原型师已经整整约6年时间了，"
                                                 "并且一直从事手办造型相关的工作内容")
        self.assertFalse(ok)
        self.assertTrue("longer" in reason or "display columns" in reason, reason)

    def test_exceeding_profile_width_is_rejected(self):
        ok, reason = ss.polish_ok("软件有好几款", "能制作手办和车库套件原型的数字软件其实真的有好几款",
                                  max_width=20)
        self.assertFalse(ok)
        self.assertIn("limit", reason)

    def test_information_dropped_is_rejected(self):
        """长度相近、但内容被换掉 → 覆盖率兜底（这条专门走 coverage 分支）。"""
        ok, reason = ss.polish_ok("能制作手办和车库套件原型的软件有好几款", "这种工具大家都在用而且评价很好")
        self.assertFalse(ok)
        self.assertIn("survive", reason)

    def test_much_shorter_line_is_rejected(self):
        ok, reason = ss.polish_ok("能制作手办和车库套件原型的软件有好几款", "做手办")
        self.assertFalse(ok)
        self.assertIn("length", reason)

    def test_repeated_wording_is_rejected(self):
        ok, reason = ss.polish_ok("重视能将其魅力发挥到何种程度，同时也看重它作为立体作品本身的魅力",
                                  "重视能将其魅力发挥到何种程度 同时，同时也看重它作为立体作品本身的魅力")
        self.assertFalse(ok)
        self.assertIn("同时", reason)

    def test_nan_polished_is_rejected(self):
        self.assertFalse(ss.polish_ok(self.ORIGINAL, float("nan"))[0])

    def test_missing_numbers_helper(self):
        self.assertEqual(ss.missing_numbers("约6年，100% 手办", "约6年，100% 手办"), [])
        self.assertEqual(ss.missing_numbers("约6年", "好几年"), ["6"])
        self.assertEqual(ss.missing_numbers("没有数字", "还是没有"), [])


class TestPolishSwitchDefaultsOff(unittest.TestCase):
    def test_missing_key_means_off(self):
        """老 config 没有这个键时，绝不能因为读键失败就偷偷调用 LLM。"""
        with mock.patch.object(config_utils, "load_key", side_effect=KeyError("subtitle")):
            self.assertFalse(config_utils.polish_translation())

    def test_explicit_true_and_false(self):
        with mock.patch.object(config_utils, "load_key", return_value=True):
            self.assertTrue(config_utils.polish_translation())
        with mock.patch.object(config_utils, "load_key", return_value=False):
            self.assertFalse(config_utils.polish_translation())


class TestPickTranslationFile(unittest.TestCase):
    """开关关 / 无产物 / 行数不一致 → 必须回到未润色字幕表（错位的字幕比不够顺的字幕糟得多）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.plain = self.tmp / "plain.xlsx"
        self.polished = self.tmp / "polished.xlsx"
        pd.DataFrame({'Source': ['a', 'b'], 'Translation': ['甲', '乙']}).to_excel(self.plain, index=False)
        self._patch(s6, "TRANSLATION_RESULTS_FOR_SUBTITLES_FILE", str(self.plain))
        self._patch(s6, "TRANSLATION_POLISHED_FILE", str(self.polished))

    def _patch(self, module, name, value):
        patcher = mock.patch.object(module, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _set_enabled(self, value):
        patcher = mock.patch.object(s6.config_utils, "polish_translation", return_value=value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_switch_off_uses_plain_file(self):
        self._set_enabled(False)
        pd.DataFrame({'Source': ['a', 'b'], 'Translation': ['甲改', '乙改']}).to_excel(self.polished, index=False)
        self.assertEqual(s6.pick_translation_file(), str(self.plain))

    def test_switch_on_without_product_falls_back(self):
        self._set_enabled(True)
        self.assertEqual(s6.pick_translation_file(), str(self.plain))

    def test_switch_on_with_matching_rows_uses_polished(self):
        self._set_enabled(True)
        pd.DataFrame({'Source': ['a', 'b'], 'Translation': ['甲改', '乙改']}).to_excel(self.polished, index=False)
        self.assertEqual(s6.pick_translation_file(), str(self.polished))

    def test_row_count_mismatch_falls_back(self):
        self._set_enabled(True)
        pd.DataFrame({'Source': ['a'], 'Translation': ['甲改']}).to_excel(self.polished, index=False)
        self.assertEqual(s6.pick_translation_file(), str(self.plain))


class TestPolishLinesFallback(unittest.TestCase):
    """LLM 不可用 / 行数对不上时：整批保持原译文，绝不丢内容。

    提示词构造与取消检查都注入 mock：`get_polish_prompt` 会读 config.yaml（full suite 里别的
    测试会把 VIDEOLINGO_CONFIG 指到已删除的临时文件 → FileNotFoundError），`eu.check_cancel`
    读的是全局停止标志。这两样都不该让"护栏与回退"的测试变成环境依赖。
    """

    def setUp(self):
        for target, name, value in ((p5, "get_polish_prompt", lambda *a, **k: "PROMPT"),
                                    (p5, "get_polish_audit_prompt", lambda *a, **k: "PROMPT"),
                                    (p5.eu, "check_cancel", lambda *a, **k: None)):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_batch_failure_keeps_original_translations(self):
        with mock.patch.object(p5, "ask_gpt", side_effect=RuntimeError("boom")):
            polished, stats = p5.polish_lines(["src1", "src2"], ["原文一", "原文二"])
        self.assertEqual(polished, ["原文一", "原文二"])
        self.assertEqual(stats['failed_batches'], 1)
        self.assertEqual(stats['changed'], 0)

    def test_guard_rejection_keeps_original_row(self):
        response = {'lines': [{'id': 0, 'polished': "好几年了", 'changed': True},
                              {'id': 1, 'polished': "作为自由原型师，约6年", 'changed': True}]}
        with mock.patch.object(p5, "ask_gpt", return_value=response), \
                mock.patch.object(p5, "_audit_info_changes", return_value=set()):
            polished, stats = p5.polish_lines(["src", "src2"], ["约6年的原型师经历", "作为自由原型师约6年"])
        self.assertEqual(polished[0], "约6年的原型师经历")     # 丢数字 → 回退
        self.assertEqual(polished[1], "作为自由原型师，约6年")
        self.assertEqual(stats['rejected'], 1)
        self.assertEqual(stats['changed'], 1)

    def test_audit_flagged_row_is_reverted(self):
        response = {'lines': [{'id': 0, 'polished': "作为自由原型师，约6年", 'changed': True}]}
        with mock.patch.object(p5, "ask_gpt", return_value=response), \
                mock.patch.object(p5, "_audit_info_changes", return_value={0}):
            polished, stats = p5.polish_lines(["src"], ["作为自由原型师约6年"])
        self.assertEqual(polished, ["作为自由原型师约6年"])
        self.assertEqual(stats.get('reverted'), 1)

    def test_stats_line_mentions_counts(self):
        text = p5.polish_stats_line({"rows": 10, "batches": 1, "changed": 3, "rejected": 1,
                                     "failed_batches": 0, "reverted": 0})
        self.assertIn("10 行", text)
        self.assertIn("改动 3 行", text)
        self.assertIn("护栏拦下 1 行", text)


if __name__ == "__main__":
    unittest.main()
