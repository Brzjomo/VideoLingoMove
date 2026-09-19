"""源语言解析的回归测试（对应 get_source_language / update_key 同步）。

用标准库 unittest：dev 没有引入 pytest，测试不该为此新增依赖。
运行：python -m unittest discover -s tests -v
"""

import importlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MINIMAL_CONFIG = """\
whisper:
  model: 'large-v3'
  language: 'zh'
  detected_language: 'zh'
api:
  key: 'k'
  base_url: 'https://api.deepseek.com'
  model: 'deepseek-flash'
target_language: '简体中文'
"""


class SourceLanguageTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.config_path = self.tmpdir / "config.yaml"
        self.config_path.write_text(MINIMAL_CONFIG, encoding="utf-8")
        self._old_env = os.environ.get("VIDEOLINGO_CONFIG")
        os.environ["VIDEOLINGO_CONFIG"] = str(self.config_path)
        # CONFIG_PATH 在模块导入时求值，必须 reload 才能生效
        import core.config_utils as config_utils
        self.config_utils = importlib.reload(config_utils)

    def tearDown(self):
        if self._old_env is None:
            os.environ.pop("VIDEOLINGO_CONFIG", None)
        else:
            os.environ["VIDEOLINGO_CONFIG"] = self._old_env
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_explicit_language_wins_over_detected(self):
        """language 是明确语言时，必须以它为准（而不是残留的 detected_language）。"""
        self.config_utils.update_key("whisper.language", "en")
        self.assertEqual(self.config_utils.get_source_language(), "en")

    def test_update_key_syncs_detected_language(self):
        """切换识别语言时必须原子同步 detected_language。

        这正是修复前缺失的一步：侧边栏只写 language，而 5 处提示词与
        load_nlp_model 读的是 detected_language，于是"切成 en 之后提示词仍
        声称源语言是 zh、spaCy 仍加载中文模型"。
        """
        self.config_utils.update_key("whisper.language", "ja")
        self.assertEqual(self.config_utils.load_key("whisper.detected_language"), "ja")

    def test_auto_does_not_clobber_detected_language(self):
        """切到 auto 时不能覆盖 detected_language —— 应留给下一次转录写真实值。"""
        self.config_utils.update_key("whisper.language", "en")
        self.config_utils.update_key("whisper.language", "auto")
        self.assertEqual(self.config_utils.load_key("whisper.detected_language"), "en")
        # auto 时回退到上次检测值
        self.assertEqual(self.config_utils.get_source_language(), "en")

    def test_unknown_language_raises(self):
        """language 与 detected_language 都不可用时必须明确报错，而不是猜一个。"""
        data = self.config_path.read_text(encoding="utf-8").replace("language: 'zh'", "language: 'auto'")
        data = data.replace("detected_language: 'zh'", "detected_language: 'auto'")
        self.config_path.write_text(data, encoding="utf-8")
        with self.assertRaises(ValueError):
            self.config_utils.get_source_language()

    def test_load_key_or_returns_default_for_missing_key(self):
        """模板新增的键在旧 config.yaml 里不存在时不应抛 KeyError。"""
        self.assertEqual(self.config_utils.load_key_or("youtube.proxy", "FALLBACK"), "FALLBACK")
        self.assertIsNone(self.config_utils.load_key_or("no.such.key"))


if __name__ == "__main__":
    unittest.main()
