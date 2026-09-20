"""提示词与消费端契约的回归测试。

两处必须"成对"的东西，一旦只改一边就会在运行期爆炸或静默出错：

  1. 断句提示词要求 split1/split2/assess/choice，而 step3_2 的 valid_split
     据此校验并取 split{choice}。只改提示词 → 每个长句都校验失败重试 3 次；
     只改校验 → KeyError。
  2. summary 的 JSON 键：dev 的提示词与消费端都用 `topic`（上游 3.x 改成了
     `theme`）。**故意不跟上游改** —— 跟了才会让主题上下文恒为 None。
"""

import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CONFIG = """\
whisper:
  model: 'large-v3'
  language: 'zh'
  detected_language: 'zh'
target_language: '简体中文'
api:
  key: 'k'
  base_url: 'https://api.deepseek.com'
  model: 'deepseek-flash'
summary_length: 8000
"""


class PromptContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp())
        config_path = cls.tmpdir / "config.yaml"
        config_path.write_text(CONFIG, encoding="utf-8")
        cls._old_env = os.environ.get("VIDEOLINGO_CONFIG")
        os.environ["VIDEOLINGO_CONFIG"] = str(config_path)
        import core.config_utils as config_utils
        importlib.reload(config_utils)
        import core.prompts_storage as prompts_storage
        cls.prompts = importlib.reload(prompts_storage)

    @classmethod
    def tearDownClass(cls):
        if cls._old_env is None:
            os.environ.pop("VIDEOLINGO_CONFIG", None)
        else:
            os.environ["VIDEOLINGO_CONFIG"] = cls._old_env
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    # ---------------- 断句提示词 ----------------

    def test_split_prompt_requests_two_candidates(self):
        prompt = self.prompts.get_split_prompt("a long sentence here", 2, 20)
        for key in ('"split1"', '"split2"', '"assess"', '"choice"', '"analysis"'):
            self.assertIn(key, prompt)

    def test_split_prompt_no_longer_requests_single_split(self):
        """旧契约的单候选键必须消失，否则消费端会取到不存在的键。"""
        prompt = self.prompts.get_split_prompt("a long sentence here", 2, 20)
        self.assertNotIn('"split":', prompt)

    # ---------------- summary 键名 ----------------

    def test_summary_keeps_topic_key(self):
        prompt = self.prompts.get_summary_prompt("some source text")
        self.assertIn('"topic"', prompt)
        self.assertNotIn('"theme"', prompt)

    # ---------------- 对齐提示词：第 3 条按 align_allow_rewrite 分支 ----------------
    #
    # 2026-09-20 用户纠正过一次措辞：字幕是**跨条连读**的，所以提示词不能要求"每行单独读起来是
    # 通顺的完整句"——那会诱发跨行语义重复与凭空增补（补主语/宾语/谓语）。第 3 条只约束
    # **衔接是否悬空**，允许的手段限于"移动边界 / 移动虚词 / 最多补一个虚词"。

    def _align_prompt(self, allow):
        from unittest import mock
        with mock.patch.object(self.prompts, "align_allow_rewrite", return_value=allow):
            return self.prompts.get_align_prompt("src line", "这是译文整句，写得好好的。", "src\nline")

    def test_align_prompt_light_mode_is_attachment_only(self):
        prompt = self._align_prompt(True)
        self.assertIn("never complete a cue", prompt)
        self.assertIn("dangling", prompt)
        self.assertIn("parsable", prompt)                       # 第 7 条：可解析 ≠ 语法完整
        self.assertIn("简体中文", prompt)                        # 占位符已替换
        self.assertNotIn("{rule3}", prompt)

    def test_align_prompt_never_asks_for_self_contained_lines(self):
        """旧措辞（要求每行自足/语法完整）不得回来。"""
        for allow in (True, False):
            prompt = self._align_prompt(allow)
            self.assertNotIn("reads as a natural cue on its own", prompt)
            self.assertNotIn("Every part must be grammatical on its own", prompt)
            self.assertNotIn("so that **each part", prompt)

    def test_align_prompt_strict_mode_forbids_any_change(self):
        prompt = self._align_prompt(False)
        self.assertIn("DO NOT rewrite, add or drop a single word", prompt)
        self.assertNotIn("never complete a cue", prompt)
        # 严格模式也必须保留"移动边界不算改写"这条许可，否则悬空切点无解
        self.assertIn("moving the boundary", prompt)

    def test_align_prompt_defaults_to_light_mode_without_the_key(self):
        """旧 config.yaml 没有这个键时（本测试的临时配置就没有）必须落在轻改写版。"""
        prompt = self.prompts.get_align_prompt("src line", "这是译文整句，写得好好的。", "src\nline")
        self.assertIn("never complete a cue", prompt)

    def test_summary_prompt_drops_proper_noun_clause(self):
        """该条要求会阻止专有名词翻译（上游 a3b87fe 已删除）。"""
        prompt = self.prompts.get_summary_prompt("some source text")
        self.assertNotIn("Keep abbreviations and proper nouns unchanged", prompt)
        self.assertIn("Extract less than 15 terms", prompt)

    # ---------------- 译文抑制性要求 ----------------

    def test_expressiveness_prompt_drops_length_clause(self):
        """删除"译文长度要向原文看齐"——它是译文生硬的已知诱因（上游 9a71ce0）。"""
        result = {"1": {"origin": "o", "direct": "d"}}
        prompt = self.prompts.get_prompt_expressiveness(result, "o", "shared")
        self.assertNotIn("close to the original text in length", prompt)


class SplitConsumerContractTest(unittest.TestCase):
    """直接对 step3_2 的 valid_split / 取键逻辑做桩测试（不导入 spacy）。"""

    @classmethod
    def setUpClass(cls):
        import ast
        src = (REPO_ROOT / "core" / "step3_2_splitbymeaning.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "split_sentence")
        cls.canned = {}

        class _Console:
            def print(self, *a, **k):
                pass

        class _Table:
            def __init__(self, *a, **k):
                pass

            def add_column(self, *a, **k):
                pass

            def add_row(self, *a, **k):
                pass

        def fake_ask_gpt(prompt, response_json=True, valid_def=None,
                         log_title="", bypass_cache=False):
            response = cls.canned["response"]
            if valid_def is not None:
                verdict = valid_def(response)
                if verdict.get("status") != "success":
                    raise AssertionError(verdict.get("message", "validation failed"))
            return response

        namespace = {
            "get_split_prompt": lambda *a, **k: "PROMPT",
            "ask_gpt": fake_ask_gpt,
            "find_split_positions": lambda original, modified: [],
            "console": _Console(),
            "Table": _Table,
            # split_sentence 入口会调 eu.check_cancel()（暂停/停止钩子）。
            # 桩掉它：本测试只关心 choice 契约，不关心取消语义。
            "eu": types.SimpleNamespace(check_cancel=lambda: None),
        }
        module = ast.Module(body=[fn], type_ignores=[])
        exec(compile(module, "<split_sentence>", "exec"), namespace)
        cls.split_sentence = staticmethod(namespace["split_sentence"])

    def _expect_accept(self, response, expected):
        self.canned["response"] = response
        self.assertEqual(self.split_sentence("original sentence", 2, 20), expected)

    def _expect_reject(self, response):
        self.canned["response"] = response
        with self.assertRaises(AssertionError):
            self.split_sentence("original sentence", 2, 20)

    def test_choice_as_string(self):
        self._expect_accept({"choice": "1", "split1": "x [br] y",
                             "split2": "p [br] q"}, "x [br] y")

    def test_choice_as_int(self):
        self._expect_accept({"choice": 2, "split1": "x [br] y",
                             "split2": "p [br] q"}, "p [br] q")

    def test_choice_with_surrounding_whitespace(self):
        self._expect_accept({"choice": " 2 ", "split1": "x [br] y",
                             "split2": "p [br] q"}, "p [br] q")

    def test_rejects_missing_choice(self):
        self._expect_reject({"split1": "x [br] y"})

    def test_rejects_out_of_range_choice(self):
        self._expect_reject({"choice": "3", "split3": "x [br] y"})

    def test_rejects_missing_selected_split(self):
        self._expect_reject({"choice": "1", "split2": "x [br] y"})

    def test_rejects_split_without_br_tag(self):
        self._expect_reject({"choice": "1", "split1": "x y"})


if __name__ == "__main__":
    unittest.main()
