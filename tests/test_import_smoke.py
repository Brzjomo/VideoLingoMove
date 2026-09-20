"""入口模块的"导入冒烟"：任何管道模块缺名/缺 import，这里必须红。

背景（2026-09-20 实测事故）：给 `core/step6_generate_final_timeline.py` 加
`load_key_or(...)` 时漏了那句 import，而**全量 289 例测试全绿** —— 因为没有任何用例
import 过 step6，直到用户跑到「翻译并压制字幕」那一步才炸：

    NameError: name 'load_key_or' is not defined

本文件把"所有会在真实流程里被 import 的模块"逐个 import 一遍。它不能替代行为测试，
但能挡住这类"改名/漏 import/写错属性"的低级错误 —— 代价只是几秒钟。

注意：这里**不启动** Streamlit、不跑流水线、不读写项目文件；`st_components.*` 只 import
模块本身（`st` 在裸模式下可 import，只是 `st.*` 调用会告警，见 devdocs）。
"""

import importlib
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 主流程与界面层里会被真正 import 的模块（新增模块请加进来）
PIPELINE_MODULES = (
    "core.config_utils",
    "core.subtitle_limits",
    "core.subtitle_split",
    "core.prompts_storage",
    "core.step1_ytdlp",
    "core.step2_whisperX",
    "core.step3_1_spacy_split",
    "core.step3_2_splitbymeaning",
    "core.step4_1_summarize",
    "core.step4_2_translate_all",
    "core.step5_splitforsub",
    "core.step5_2_polish_subs",
    "core.step6_generate_final_timeline",
    "core.step7_merge_sub_to_vid",
    "core.subtitle_trim",
    "core.onekeycleanup",
    "core.align_model",
    "easy_util",
    "runtime_libraries",
    "st_components.sidebar_setting",
    "st_components.download_video_section",
    "st_components.imports_and_utils",
    "st_components.task_runner",
)


class TestPipelineModulesImport(unittest.TestCase):
    def test_every_pipeline_module_imports(self):
        failures = []
        for name in PIPELINE_MODULES:
            try:
                importlib.import_module(name)
            except Exception as exc:  # noqa: BLE001 - 收集全部失败再一次性报出
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        self.assertEqual(failures, [], "以下模块 import 失败：\n" + "\n".join(failures))

    def test_step6_exposes_the_keys_it_uses(self):
        """step6 用到 `load_key_or` 与 `subtitle_split` —— 光 import 模块不够，这里点名。

        （2026-09-20 曾因漏 import `load_key_or` 而 NameError：函数体内的名字 import 时查不出来，
        所以这条必须显式断言属性存在。）
        """
        step6 = importlib.import_module("core.step6_generate_final_timeline")
        self.assertTrue(hasattr(step6, "load_key_or"),
                        "step6 必须 import load_key_or（2026-09-20 曾因此 NameError）")
        self.assertTrue(hasattr(step6, "subtitle_split"),
                        "step6 必须 import core.subtitle_split（去行尾标点要用它的纯函数）")
        self.assertFalse(step6.load_key_or("subtitle.strip_punctuation_in_source", False),
                         "默认应当是「原文行保留标点」")
        self.assertEqual(step6.subtitle_split.strip_terminal_punctuation("これは文です。"),
                         "これは文です")


if __name__ == "__main__":
    unittest.main(verbosity=2)
