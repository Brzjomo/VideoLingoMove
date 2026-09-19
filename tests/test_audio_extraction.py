"""音频抽取的回归测试。

保护两项容易被"顺手改回去"的东西：

  1. 时间轴对齐标志 `-af aresample=async=1:first_pts=0`（上游 3ab60de）。
     缺少它时，压缩帧解码出的采样点多于容器时长，逐点拼接会把识别时钟越推
     越后，表现为字幕整体逐渐偏移；因为成片用的是同一条时钟，下游无法补救。

  2. raw.mp3 必须**直接从源媒体编码**。修复前它是从 16kHz 的 raw.wav 转码
     出来的 —— 虽然写着 `-ar 32000`，实际带宽被卡在 8kHz 以内，参数是假的。
     本测试用 12kHz 正弦实测：旧路径的高频会被抹掉，新路径保留。
     同时 raw.wav 必须保持 16kHz/单声道/s16，那是火山 ASR 用 ffprobe 校验的硬契约。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# 被测模块会打印 emoji；Windows 控制台默认 GBK，不先修编码会在打印时抛
# UnicodeEncodeError（测试进程不是那些模块的 __main__，所以守卫不会自动生效）。
from easy_util import ensure_utf8_console  # noqa: E402

ensure_utf8_console()

WHISPER_UTILS = REPO_ROOT / "core" / "all_whisper_methods" / "whisperX_utils.py"
TIMELINE_FLAG = "aresample=async=1:first_pts=0"

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _code_only(path):
    """去掉注释并归一化空白后的源码。

    两步都是必要的：
      * 去注释 —— 否则"注释里提到过该标志"会被误判成"代码里有"；
      * 归一化空白 —— tokenize 会把 `'-ar', '16000'` 还原成
        `'-ar' , '16000'`，直接做子串断言会失败。
    """
    import io
    import tokenize
    text = path.read_text(encoding="utf-8")
    parts = [tok.string for tok in tokenize.generate_tokens(io.StringIO(text).readline)
             if tok.type != tokenize.COMMENT]
    joined = " ".join(parts)
    joined = re.sub(r"\s*,\s*", ", ", joined)
    return re.sub(r"\s+", " ", joined)


class ExtractionSourceTest(unittest.TestCase):
    def test_timeline_flag_present_on_both_commands(self):
        code = _code_only(WHISPER_UTILS)
        self.assertEqual(code.count(TIMELINE_FLAG), 2,
                         "raw.wav 与 raw.mp3 两条抽取命令都必须带时间轴对齐标志")

    def test_raw_mp3_no_longer_derived_from_raw_wav(self):
        """这是"假 32kHz"的根因：raw.mp3 若以 raw.wav 为输入，带宽就已被限死。"""
        code = _code_only(WHISPER_UTILS)
        self.assertNotIn("'-i', RAW_AUDIO_WAV_FILE", code)
        # raw.mp3 必须以源媒体为输入
        self.assertIn("'-i', video_file", code)

    def test_volcano_format_contract_intact(self):
        """火山侧 ffprobe 校验 16kHz/单声道/s16，这三个参数不能动。"""
        code = _code_only(WHISPER_UTILS)
        self.assertIn("'-ar', '16000'", code)
        self.assertIn("'-ac', '1'", code)
        self.assertIn("'pcm_s16le'", code)

    def test_encoder_fallback_present(self):
        """conda-forge 等构建没有 libmp3lame，必须有回退而不是直接失败。"""
        code = _code_only(WHISPER_UTILS)
        self.assertIn("_ffmpeg_has_encoder", code)
        self.assertIn("libmp3lame", code)


@unittest.skipUnless(HAS_FFMPEG, "需要 ffmpeg / ffprobe")
class ExtractionBehaviorTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_cwd = os.getcwd()
        # convert_video_to_audio() 用相对路径，必须在临时目录里跑
        os.chdir(self.tmpdir)
        (self.tmpdir / "output" / "audio").mkdir(parents=True)
        self.env = os.environ.get("VIDEOLINGO_CONFIG")

    def tearDown(self):
        os.chdir(self._orig_cwd)
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _ffprobe(self, path, entries):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", entries,
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True).stdout.split()
        return out

    def _high_freq_energy(self, path):
        """返回 10kHz 以上残留的平均电平（dB）。越接近 0 表示高频保留越多。"""
        out = subprocess.run(
            ["ffmpeg", "-v", "info", "-i", str(path),
             "-af", "highpass=f=10000,volumedetect", "-f", "null", "-"],
            capture_output=True, text=True).stderr
        match = re.search(r"mean_volume:\s*(-?[\d.]+)", out)
        return float(match.group(1)) if match else None

    def _make_source(self, freq):
        src = self.tmpdir / "src.m4a"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", f"sine=frequency={freq}:duration=4",
             "-c:a", "aac", "-b:a", "192k", str(src)],
            check=True, capture_output=True)
        return src

    def _import_module(self):
        from core.all_whisper_methods import whisperX_utils
        return whisperX_utils

    def test_volcano_wav_contract_holds(self):
        module = self._import_module()
        module.convert_video_to_audio(str(self._make_source(440)))
        values = self._ffprobe("output/audio/raw.wav",
                               "stream=sample_rate,channels,bits_per_sample")
        self.assertEqual(values, ["16000", "1", "16"],
                         "火山 ASR 的 ffprobe 校验要求 16kHz/单声道/16bit")

    def test_raw_mp3_is_genuinely_32khz(self):
        module = self._import_module()
        module.convert_video_to_audio(str(self._make_source(440)))
        values = self._ffprobe("output/audio/raw.mp3", "stream=sample_rate,channels")
        self.assertEqual(values, ["32000", "1"])

    def test_high_frequency_is_preserved(self):
        """12kHz 音调必须活下来 —— 这证明 raw.mp3 来自源媒体而非 16kHz 的 raw.wav。"""
        module = self._import_module()
        module.convert_video_to_audio(str(self._make_source(12000)))
        energy = self._high_freq_energy("output/audio/raw.mp3")
        self.assertIsNotNone(energy)
        # 旧路径下这里约为 -87dB（高频被抹平）；新路径约 -20dB
        self.assertGreater(energy, -40.0,
                           f"10kHz 以上残留仅 {energy}dB，raw.mp3 可能又退回从 "
                           f"16kHz 的 raw.wav 转码了")


if __name__ == "__main__":
    unittest.main()
