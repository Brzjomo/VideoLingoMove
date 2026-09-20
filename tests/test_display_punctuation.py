"""显示层标点：CJK 目标语不再把句中 `，` 换成空格（2026-09-21 用户实测事故）。

事故现象（用户原话："这不还是老样子"）：
    translation_results_for_subtitles.xlsx: `作为自由原型师约6年，一直从事手办造型工作。`
    output/src_trans.srt:                   `作为自由原型师约 6 年 一直从事手办造型工作`
分句之间的逗号被换成了空格 → 两个分句连成一串，读起来是断的。用户在 2026-09-20 已经说过
"句中的没事，维持现状就好"，所以这是显示层的 bug，不是切分或翻译的问题：

* `re.sub(r'[，。]', ' ', …)`（`core/step6_generate_final_timeline.py`）原本是给**拉丁语目标语**
  写的规则（英文里混进中文标点要清掉，逗号变空格正好）；
* 对 CJK 目标语它是有害的：中文/日文的分句逗号是句法信息，换成空格等于删掉它。

修法：按目标语分流（`is_cjk_target` / `polish_translation_for_display`），CJK 保留句中标点，
行尾标点仍由 `subtitle_split.strip_terminal_punctuation` 去掉（用户 2026-09-20 的另一条要求）。
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import step6_generate_final_timeline as s6  # noqa: E402
from core import subtitle_split as ss  # noqa: E402


class TestCjkTargetDetection(unittest.TestCase):
    """`target_language` 是自由文本（默认 '简体中文'），判定必须走 subtitle_limits 的归一表。"""

    def _with_target(self, value):
        with mock.patch.object(s6, "load_key", return_value=value):
            return s6.is_cjk_target()

    def test_cjk_targets(self):
        for value in ("简体中文", "繁體中文", "中文", "日本語", "한국어", "ja", "zh"):
            self.assertTrue(self._with_target(value), value)

    def test_non_cjk_targets(self):
        for value in ("English", "English (US)", "русский", "Español", "العربية"):
            self.assertFalse(self._with_target(value), value)

    def test_missing_key_falls_back_to_latin_behaviour(self):
        """读不到配置时保持旧行为（逗号→空格），不能让老流程因为读配置失败而改变输出。"""
        with mock.patch.object(s6, "load_key", side_effect=KeyError("target_language")):
            self.assertFalse(s6.is_cjk_target())


class TestPolishTranslationForDisplay(unittest.TestCase):
    SENT = "作为自由原型师约6年，一直从事手办造型工作。"

    def test_cjk_keeps_the_mid_sentence_comma(self):
        self.assertEqual(s6.polish_translation_for_display(self.SENT, True), self.SENT)

    def test_latin_target_still_clears_chinese_punctuation(self):
        self.assertEqual(s6.polish_translation_for_display(self.SENT, False),
                         "作为自由原型师约6年 一直从事手办造型工作")

    def test_terminal_punctuation_is_still_removed_for_cjk(self):
        """保留句中逗号 ≠ 保留行尾标点：行尾仍按用户 2026-09-20 的要求去掉。"""
        kept = s6.polish_translation_for_display(self.SENT, True)
        self.assertEqual(ss.strip_terminal_punctuation(kept),
                         "作为自由原型师约6年，一直从事手办造型工作")

    def test_nan_and_none_do_not_leak_into_display(self):
        self.assertEqual(s6.polish_translation_for_display(None, True), "")
        self.assertEqual(ss.strip_terminal_punctuation(s6.polish_translation_for_display(float("nan"), True)), "")


if __name__ == "__main__":
    unittest.main()
