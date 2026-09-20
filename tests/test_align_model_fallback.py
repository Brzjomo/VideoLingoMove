"""对齐模型获取的容错：本地目录优先、多端点回退、失败给可照做的提示。

背景（2026-09-20 实测，日语视频跑到对齐一步失败）
-------------------------------------------------
控制台里是这两句：

    Can't load feature extractor for 'jonatasgrosman/wav2vec2-large-xlsr-53-japanese'.
    … make sure you don't have a local directory with the same name. Otherwise, make
    sure '<repo>' is the correct path to a directory containing a
    preprocessor_config.json file

    ValueError: The chosen align_model "<repo>" could not be found in huggingface … or
    torchaudio

两句都在暗示"模型名 / 路径写错了"，真因却是**网络**：这个词条不在本地缓存、又拿不到
文件元数据时，`transformers/feature_extraction_utils.py` 把 `cached_file()` 的 None
吞成空列表（`IndexError: list index out of range` 就在它上一行），再包装成上面那句
OSError；`whisperx/alignment.py` 又包装一次。仓库名没错、模型也还在，重试/换端点/预下载
都能解决。

`core/align_model.py` 负责：本地目录 `<model_dir>/align/<语言>/` 优先 → 官方与
hf-mirror 逐个试 → 全失败时把「去哪下、要哪些文件、放到哪个目录」写进错误消息。

本文件**全部离线**：
  * 下载与"是否已缓存"两个副作用都用注入替身（`download=` / `cached=` / `mock.patch`）；
  * 语种表用 `align_model._ALIGN_MODELS_CACHE` 打桩，不 import whisperx（拖 torch，9 秒）；
  * 真实仓库名用 AST 静态读 whisperx 源码来钉（见 `TestWhisperxTablePinned`）。
"""

import ast
import contextlib
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import align_model  # noqa: E402

JA_REPO = "jonatasgrosman/wav2vec2-large-xlsr-53-japanese"
ZH_REPO = "jonatasgrosman/wav2vec2-large-xlsr-53-chinese-zh-cn"
OFFICIAL = align_model.HF_OFFICIAL_ENDPOINT
MIRROR = align_model.HF_MIRROR_ENDPOINT


class FakeWhisperX:
    """whisperx 替身：只记录调用参数并返回占位模型。"""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def load_align_model(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return ("fake-align-model", {"language": kwargs.get("language_code")})


def make_download(failing=()):
    """下载替身：记录 (repo, model_dir, endpoint)，对指定端点抛错。"""
    calls = []

    def download(repo, model_dir, endpoint):
        calls.append((repo, str(model_dir), endpoint))
        if endpoint in failing:
            raise OSError(f"simulated failure at {endpoint}")

    download.calls = calls
    return download


def quiet(_message):
    """吞掉日志（测试里只关心行为与最终报错）。"""


class AlignModelTestBase(unittest.TestCase):
    def setUp(self):
        # 不 import whisperx：直接给语种表打桩（ja/zh 走 HF，en 走 torchaudio）
        patcher = mock.patch.object(align_model, "_ALIGN_MODELS_CACHE",
                                    {"ja": JA_REPO, "zh": ZH_REPO})
        patcher.start()
        self.addCleanup(patcher.stop)

    def tmpdir(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    def write_local_model(self, model_dir, language="ja", weight=b"x" * 8):
        target = align_model.local_align_dir(model_dir, language)
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_text("{}", encoding="utf-8")
        (target / "preprocessor_config.json").write_text("{}", encoding="utf-8")
        (target / "pytorch_model.bin").write_bytes(weight)
        return target


class TestEndpointOrder(AlignModelTestBase):
    def test_default_order_prefers_official_then_mirror(self):
        """不设 HF_ENDPOINT 时：官方在前、镜像在后（实测官方通、hf-mirror 走 hub 会失败）。"""
        self.assertEqual(align_model.candidate_endpoints(preset=""),
                         [OFFICIAL, MIRROR])

    def test_preset_first_and_deduped(self):
        """启动前设了 HF_ENDPOINT → 它排第一；与内置值重复时不出现两次。"""
        self.assertEqual(
            align_model.candidate_endpoints(preset="https://example.com/"),
            ["https://example.com", OFFICIAL, MIRROR])
        self.assertEqual(align_model.candidate_endpoints(preset=OFFICIAL),
                         [OFFICIAL, MIRROR])


class TestLocalModelDirectory(AlignModelTestBase):
    def test_incomplete_or_empty_files_are_rejected(self):
        """只判断目录存在会让"下到一半"的目录通过 —— 必须查文件齐全且非空。"""
        tmp = self.tmpdir()
        target = align_model.local_align_dir(tmp, "ja")
        target.mkdir(parents=True)
        self.assertFalse(align_model.local_align_model_ready(target))

        (target / "config.json").write_text("{}", encoding="utf-8")
        self.assertFalse(align_model.local_align_model_ready(target),
                         "缺 preprocessor_config.json 应当判为不可用")

        (target / "preprocessor_config.json").write_text("{}", encoding="utf-8")
        self.assertFalse(align_model.local_align_model_ready(target), "缺权重")

        (target / "pytorch_model.bin").write_bytes(b"")
        self.assertFalse(align_model.local_align_model_ready(target), "0 字节权重")

        (target / "pytorch_model.bin").write_bytes(b"x" * 8)
        self.assertTrue(align_model.local_align_model_ready(target))

    def test_local_dir_is_passed_as_model_name(self):
        """手动放好的目录会被当作 model_name 传给 whisperx，且完全不联网。"""
        tmp = self.tmpdir()
        target = self.write_local_model(tmp)
        wx = FakeWhisperX()
        download = make_download()
        with mock.patch.object(align_model, "_snapshot_download", download):
            align_model.load_align_model("ja", "cuda", tmp, whisperx_module=wx, log=quiet)
        self.assertEqual(download.calls, [], "本地目录已完整，不该再联网")
        self.assertEqual(len(wx.calls), 1)
        self.assertEqual(wx.calls[0]["model_name"], str(target))
        self.assertEqual(wx.calls[0]["model_dir"], tmp)


class TestFallbackAndErrors(AlignModelTestBase):
    def test_first_endpoint_failure_falls_back_to_the_next(self):
        download = make_download(failing={OFFICIAL})
        endpoint = align_model.ensure_align_model_cached(
            JA_REPO, self.tmpdir(), endpoints=[OFFICIAL, MIRROR], download=download,
            cached=lambda repo, model_dir: False, log=quiet, language_code="ja")
        self.assertEqual(endpoint, MIRROR)
        self.assertEqual([call[2] for call in download.calls], [OFFICIAL, MIRROR])

    def test_already_cached_skips_downloading(self):
        download = make_download()
        endpoint = align_model.ensure_align_model_cached(
            JA_REPO, self.tmpdir(), download=download,
            cached=lambda repo, model_dir: True, log=quiet, language_code="ja")
        self.assertIsNone(endpoint)
        self.assertEqual(download.calls, [])

    def test_all_endpoints_failing_gives_actionable_message(self):
        """核心诉求：失败时必须说清"是网络问题"，并给出下载地址/文件/目录。"""
        tmp = self.tmpdir()
        download = make_download(failing={OFFICIAL, MIRROR})
        with self.assertRaises(align_model.AlignModelUnavailable) as ctx:
            align_model.ensure_align_model_cached(
                JA_REPO, tmp, endpoints=[OFFICIAL, MIRROR], download=download,
                cached=lambda repo, model_dir: False, log=quiet, language_code="ja")

        message = str(ctx.exception)
        expected = [
            JA_REPO,                                   # 是哪个仓库
            "ja",                                      # 哪个语种
            "网络问题",                                  # 定性：不是模型名写错
            OFFICIAL, MIRROR,                          # 试过哪些端点
            "simulated failure",                       # 各自失败原因
            f"https://hf-mirror.com/{JA_REPO}/tree/main",   # 去哪下载
            "config.json", "preprocessor_config.json",      # 要哪些文件
            "pytorch_model.bin",
            str(pathlib.Path(tmp) / "align" / "ja"),   # 放到哪个目录
            "core.align_model ja",                     # 项目自带命令
        ]
        for needle in expected:
            self.assertIn(needle, message, f"失败提示里缺少：{needle}")
        self.assertIn("flax_model.msgpack", message, "要提醒 flax 权重不用下（省 1.27 GB）")

    def test_download_then_offline_load(self):
        """下载成功后用 local_files_only 加载：网络再抖也不会卡在这一步。"""
        tmp = self.tmpdir()
        wx = FakeWhisperX()
        download = make_download()
        with mock.patch.object(align_model, "_cached_locally", return_value=False), \
                mock.patch.object(align_model, "_snapshot_download", download):
            align_model.load_align_model("ja", "cuda", tmp, whisperx_module=wx, log=quiet)
        self.assertEqual([call[2] for call in download.calls], [OFFICIAL])
        self.assertEqual(wx.calls[-1]["model_cache_only"], True)

    def test_torchaudio_language_skips_the_hf_path(self):
        """en/fr/de/es/it 走 torchaudio（不经过 HF）：不该触发任何 HF 下载。"""
        tmp = self.tmpdir()
        wx = FakeWhisperX()
        with mock.patch.object(align_model, "_snapshot_download",
                               side_effect=AssertionError("en 不该走 HF 下载")):
            align_model.load_align_model("en", "cpu", tmp, whisperx_module=wx, log=quiet)
        self.assertEqual(wx.calls,
                         [{"language_code": "en", "device": "cpu", "model_dir": tmp}])

    def test_offline_load_failure_also_gets_guidance(self):
        """缓存已有但加载失败：也要给指引，而不是把上游那句误导性报错丢出去。"""
        tmp = self.tmpdir()
        wx = FakeWhisperX(error=RuntimeError("can't load"))
        with mock.patch.object(align_model, "_cached_locally", return_value=True):
            with self.assertRaises(align_model.AlignModelUnavailable) as ctx:
                align_model.load_align_model("ja", "cpu", tmp, whisperx_module=wx, log=quiet)
        message = str(ctx.exception)
        self.assertIn("本地缓存已存在但加载失败", message)
        self.assertIn("can't load", message)
        self.assertIn(str(pathlib.Path(tmp) / "align" / "ja"), message)


class TestCli(AlignModelTestBase):
    """CLI（`python -m core.align_model <语言>`）：把 stdout 收走，别混进测试输出。"""

    def _run_cli(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = align_model._main(argv)
        return code, buffer.getvalue()

    def test_language_argument_downloads(self):
        tmp = self.tmpdir()
        download = make_download()
        with mock.patch.object(align_model, "_snapshot_download", download):
            code, out = self._run_cli(["ja", "--model-dir", tmp])
        self.assertEqual(code, 0)
        self.assertEqual([call[0] for call in download.calls], [JA_REPO])
        self.assertIn("已就绪", out)

    def test_torchaudio_language_is_a_no_op(self):
        code, out = self._run_cli(["en", "--model-dir", self.tmpdir()])
        self.assertEqual(code, 0)
        self.assertIn("不需要", out)

    def test_all_failures_exit_nonzero_with_message(self):
        tmp = self.tmpdir()
        download = make_download(failing={OFFICIAL, MIRROR})
        with mock.patch.object(align_model, "_snapshot_download", download):
            code, out = self._run_cli(["ja", "--model-dir", tmp])
        self.assertEqual(code, 1, "全失败必须非零退出，好让脚本/用户注意到")
        self.assertIn("网络问题", out)
        self.assertIn(str(pathlib.Path(tmp) / "align" / "ja"), out)


class TestWhisperxTablePinned(unittest.TestCase):
    """钉住 whisperx 的「语种 → 仓库」表：提示里的下载地址就是照它给的。

    静态读源码（`ast`）而不是 import：import whisperx 要拖 torch，实测约 9 秒。
    """

    def _table(self):
        spec = importlib.util.find_spec("whisperx.alignment")
        if spec is None or not spec.origin:
            self.skipTest("当前解释器未安装 whisperx")
        tree = ast.parse(pathlib.Path(spec.origin).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            names = [getattr(target, "id", "") for target in node.targets]
            if "DEFAULT_ALIGN_MODELS_HF" in names:
                return ast.literal_eval(node.value)
        self.fail("whisperx/alignment.py 里没有 DEFAULT_ALIGN_MODELS_HF")

    def test_japanese_and_chinese_repos(self):
        table = self._table()
        self.assertEqual(table.get("ja"), JA_REPO)
        self.assertEqual(table.get("zh"), ZH_REPO)

    def test_torchaudio_languages_are_not_in_the_hf_table(self):
        table = self._table()
        for code in ("en", "fr", "de", "es", "it"):
            self.assertNotIn(code, table, f"{code} 走 torchaudio，不该出现在 HF 表里")


if __name__ == "__main__":
    unittest.main(verbosity=2)
