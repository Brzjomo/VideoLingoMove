import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings("ignore")

import functools

import torch


# ---------------------------------------------------------------- torch.load 兼容垫片
# torch ≥ 2.6 把 torch.load 的 weights_only 默认值从 False 改成了 True。
# WhisperX 3.8 的 VAD 走 pyannote-audio 4，而 pyannote 的 checkpoint 里含
# omegaconf 对象（OmegaConf / ListConfig），会被 weights_only=True 的
# unpickler 按"安全机制"拒掉，报 UnpicklingError。上游
# core/asr_backend/whisperX_local.py 顶部就是这段垫片。
#
# 必须在 `import whisperx` **之前**打上：whisperx 在 import 期就会
# import pyannote.audio，之后任何一次 torch.load 调用都要走这里。
#
# 只包一层、只改默认值：调用方显式传 weights_only=True 时依然尊重，
# 所以并不会削弱"我们主动要求安全加载"的路径。
#: 按语种给 Whisper 的中性 initial_prompt —— 目的只是让输出**带标点**。
#: 句子必须与视频内容无关，否则模型会"顺着提示词续写"。
_INITIAL_PROMPTS = {
    "ja": "こんにちは。今日はいい天気ですね。",
    "zh": "大家好，今天我们来讲一个话题。",
    "en": "Hello, and welcome. Today we are going to talk about something.",
    "ko": "안녕하세요. 오늘은 날씨가 좋네요.",
    "es": "Hola, ¿qué tal? Hoy hace buen tiempo.",
    "fr": "Bonjour. Aujourd'hui, il fait beau.",
    "de": "Hallo. Heute ist das Wetter schön.",
    "it": "Ciao. Oggi il tempo è bello.",
    "pt": "Olá. Hoje o tempo está bom.",
    "ru": "Здравствуйте. Сегодня хорошая погода.",
}


def default_initial_prompt(language) -> str:
    """按语种取中性的 initial_prompt；未知语种返回空串（＝历史行为）。"""
    return _INITIAL_PROMPTS.get(str(language or "").strip().lower(), "")


def _patch_torch_load_weights_only():
    original = torch.load
    if getattr(original, "_videolingo_weights_only_shim", False):
        return original

    @functools.wraps(original)
    def patched(*args, **kwargs):
        kwargs.setdefault("weights_only", False)
        return original(*args, **kwargs)

    patched._videolingo_weights_only_shim = True
    torch.load = patched
    return original


_patch_torch_load_weights_only()

import whisperx
from whisperx.audio import load_audio as _whisperx_load_audio
from typing import Dict
import librosa
from rich import print as rprint
import subprocess
import tempfile
import time
import gc
import numpy as np

from core.config_utils import load_key, load_key_or
import easy_util as eu
from core import align_model as align_model_utils
from core.all_whisper_methods import transcription_cache
from core.all_whisper_methods.demucs_vl import demucs_main, RAW_AUDIO_FILE, VOCAL_AUDIO_FILE
from core.all_whisper_methods.whisperX_utils import process_transcription, convert_video_to_audio, split_audio, save_results, save_language, compress_audio, compute_normalization_gain, CLEANED_CHUNKS_EXCEL_PATH, RAW_AUDIO_WAV_FILE
from core.step1_ytdlp import find_video_files, find_media_file

# 尝试导入火山引擎ASR
try:
    from core.all_whisper_methods.volcano_asr import VolcanoASR
    VOLCANO_ASR_AVAILABLE = True
except ImportError:
    VOLCANO_ASR_AVAILABLE = False
    rprint("[yellow]⚠️ 火山引擎ASR模块导入失败，确保volcano_asr.py文件存在[/yellow]")

# 注意：`ENHANCED_VOCAL_PATH` 只在 demucs=true 时被 enhance_vocals() 使用。
MODEL_DIR = load_key("model_dir")
WHISPER_FILE = "output/audio/for_whisper.mp3"
ENHANCED_VOCAL_PATH = "output/audio/enhanced_vocals.mp3"

# 把实际生效的 whisperx 版本打进日志：升级到 3.8 之后，出问题时第一件要
# 确认的就是"跑的到底是哪一版"（旧环境的 3.2 与 3.8 的 API/行为都不同）。
#
# ⚠️ 别写 `getattr(whisperx, '__version__', '未知版本')`：上游 whisperx **不定义**
# 这个属性（2026-09-20 实测 3.8.6：属性缺失），那样写这行日志永远显示"未知版本"，
# 而版本其实一直在发行元数据里（`whisperx-3.8.6.dist-info`）。
# easy_util.package_version 先看属性、再看 importlib.metadata，见其 docstring。
rprint(f"[cyan]🔧 whisperx {eu.package_version('whisperx', whisperx) or '未知版本'} | "
       f"torch {torch.__version__} | "
       f"weights_only 垫片：{'已启用' if getattr(torch.load, '_videolingo_weights_only_shim', False) else '未启用'}[/cyan]")

# 镜像探测结果缓存：同一进程只探测一次。
# 原实现把探测放在 transcribe_audio_with_whisper() 里，而该函数是按音频分段
# 反复调用的（长视频几十段），于是每一段都要 ping 两个域名、最坏各等 3 秒。
_HF_ENDPOINT_CACHE = None

def check_hf_mirror() -> str:
    """探测并返回最快的 HuggingFace 镜像（同进程只探测一次）。

    若用户已显式设置 HF_ENDPOINT，则直接尊重该值、不做探测。
    """
    global _HF_ENDPOINT_CACHE
    if _HF_ENDPOINT_CACHE is not None:
        return _HF_ENDPOINT_CACHE

    preset = os.environ.get('HF_ENDPOINT', '').strip()
    if preset:
        rprint(f"[cyan]🌐 Using preset HF_ENDPOINT:[/cyan] {preset}")
        _HF_ENDPOINT_CACHE = preset
        return _HF_ENDPOINT_CACHE

    mirrors = {
        'Official': 'huggingface.co',
        'Mirror': 'hf-mirror.com'
    }
    fastest_url = f"https://{mirrors['Official']}"
    best_time = float('inf')
    rprint("[cyan]🔍 Checking HuggingFace mirrors...[/cyan]")
    for name, domain in mirrors.items():
        try:
            if os.name == 'nt':
                cmd = ['ping', '-n', '1', '-w', '3000', domain]
            else:
                cmd = ['ping', '-c', '1', '-W', '3', domain]
            start = time.time()
            result = subprocess.run(cmd, capture_output=True, text=True)
            response_time = time.time() - start
            if result.returncode == 0:
                if response_time < best_time:
                    best_time = response_time
                    fastest_url = f"https://{domain}"
                rprint(f"[green]✓ {name}:[/green] {response_time:.2f}s")
        except:
            rprint(f"[red]✗ {name}:[/red] Failed to connect")
    if best_time == float('inf'):
        rprint("[yellow]⚠️ All mirrors failed, using default[/yellow]")
    rprint(f"[cyan]🚀 Selected mirror:[/cyan] {fastest_url} ({best_time:.2f}s)")
    _HF_ENDPOINT_CACHE = fastest_url
    return _HF_ENDPOINT_CACHE

# 一个可用的本地 whisper 模型目录必须同时具备这三个非空文件。
# 只判断 os.path.exists(目录) 会让"下载到一半"的目录通过检查，
# 之后 whisperx 要么重新联网、要么抛出难以定位的错误。
_REQUIRED_MODEL_FILES = ("config.json", "model.bin", "tokenizer.json")


def _complete_model_directory(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    for name in _REQUIRED_MODEL_FILES:
        candidate = os.path.join(path, name)
        if not os.path.isfile(candidate) or os.path.getsize(candidate) == 0:
            return False
    return True


def load_whisper_model_name(language: str) -> str:
    """中文强制使用带标点的 Belle 模型，其余用配置里的模型名。"""
    if language == 'zh':
        return "Huan69/Belle-whisper-large-v3-zh-punct-fasterwhisper"
    return load_key("whisper.model")


def resolve_whisper_model(model_name: str, model_dir: str) -> str:
    """决定交给 whisperx 的模型标识。

    优先用本地已下载且**完整**的目录（避免重复联网 / 避免半成品目录），
    否则返回原始名字，由 whisperx 下载到 model_dir。
    """
    candidates = []
    if os.path.isdir(model_name):
        candidates.append(model_name)  # 调用方直接给了路径
    candidates.append(os.path.join(model_dir, model_name))  # 约定的本地缓存目录

    for candidate in candidates:
        if _complete_model_directory(candidate):
            rprint(f"[green]📥 Loading local WHISPER model:[/green] {candidate} ...")
            return candidate

    for candidate in candidates:
        if os.path.isdir(candidate):
            rprint(f"[yellow]⚠️ 本地模型目录不完整（缺少 {'/'.join(_REQUIRED_MODEL_FILES)} 之一），"
                   f"将重新获取：{candidate}[/yellow]")
    rprint(f"[green]📥 Using WHISPER model from HuggingFace:[/green] {model_name} ...")
    return model_name


def transcribe_audio_with_whisper(audio_file: str, start: float, end: float) -> Dict:
    """
    使用WhisperX转录音频

    Args:
        audio_file: 音频文件路径
        start: 开始时间（秒）
        end: 结束时间（秒）

    Returns:
        dict: 转录结果
    """
    os.environ['HF_ENDPOINT'] = check_hf_mirror()
    WHISPER_LANGUAGE = load_key("whisper.language")
    device = "cuda" if torch.cuda.is_available() else "cpu"
    rprint(f"🚀 Starting WhisperX using device: {device} ...")

    if device == "cuda":
        gpu_mem = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        batch_size = 16 if gpu_mem > 8 else 2
        compute_type = "float16" if torch.cuda.is_bf16_supported() else "int8"
        rprint(f"[cyan]🎮 GPU memory:[/cyan] {gpu_mem:.2f} GB, [cyan]📦 Batch size:[/cyan] {batch_size}, [cyan]⚙️ Compute type:[/cyan] {compute_type}")
    else:
        batch_size = 1
        compute_type = "int8"
        rprint(f"[cyan]📦 Batch size:[/cyan] {batch_size}, [cyan]⚙️ Compute type:[/cyan] {compute_type}")
    rprint(f"[green]▶️ Starting WhisperX for segment {start:.2f}s to {end:.2f}s...[/green]")
    
    try:
        model_name = load_whisper_model_name(WHISPER_LANGUAGE)
        model_name = resolve_whisper_model(model_name, MODEL_DIR)

        vad_options = {"vad_onset": 0.500,"vad_offset": 0.363}
        # Whisper 的标点风格高度依赖 initial_prompt：留空时日语几乎不出「、」「。」
        # （2026-09-20 实测：一支 6 分钟视频只有 27 个「。」、50 个「、」）→ 下游 step3 的
        # 标点/接续切分几乎没有边界可用，最后只能按显示宽度硬切，出现"一句话被切成两半、
        # 前半接上一条后半接下一条"。这里按语种给一句**中性**示例（与视频内容无关，
        # 避免模型顺着提示词续写）。要关掉/自定义：`whisper.initial_prompt`（留空=旧行为）。
        asr_options = {"temperatures": [0],
                       "initial_prompt": (load_key_or("whisper.initial_prompt", "")
                                          or default_initial_prompt(WHISPER_LANGUAGE)),}
        whisper_language = None if 'auto' in WHISPER_LANGUAGE else WHISPER_LANGUAGE
        rprint("[bold yellow]**You can ignore warning of `Model was trained with torch 1.10.0+cu102, yours is 2.0.0+cu118...`**[/bold yellow]")
        model = whisperx.load_model(model_name, device, compute_type=compute_type, language=whisper_language, vad_options=vad_options, asr_options=asr_options, download_root=MODEL_DIR)

        # Create temp file with wav format for better compatibility
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_audio:
            temp_audio_path = temp_audio.name
        
        # Extract audio segment using ffmpeg
        # Ensure minimum duration of 0.5 seconds
        MIN_DURATION = 0.5  # minimum duration in seconds
        duration = end - start
        if duration < MIN_DURATION:
            rprint(f"[yellow]⚠️ Audio segment too short ({duration:.3f}s), extending to {MIN_DURATION}s...[/yellow]")
            end = start + MIN_DURATION
        
        ffmpeg_cmd = f'ffmpeg -y -i "{audio_file}" -ss {start} -t {end-start} -vn -ar 16000 -ac 1 "{temp_audio_path}"'
        rprint(f"[cyan]Executing ffmpeg command: {ffmpeg_cmd}[/cyan]")
        process = subprocess.run(ffmpeg_cmd, shell=True, capture_output=True, text=True)
        
        if process.returncode != 0:
            rprint(f"[red]FFmpeg error: {process.stderr}[/red]")
            raise RuntimeError(f"FFmpeg failed with error: {process.stderr}")
        
        if not os.path.exists(temp_audio_path) or os.path.getsize(temp_audio_path) == 0:
            rprint("[red]FFmpeg output file is empty or does not exist![/red]")
            raise RuntimeError("FFmpeg failed to create output file")
        
        try:
            # whisperx 的音频解码入口。3.8 把它放在 whisperx.audio 下（本模块
            # 顶部已按该路径导入），语义与旧版 whisperx.load_audio 相同：
            # 调用 ffmpeg CLI 解成 16kHz 单声道 float32 numpy。
            audio_numpy = _whisperx_load_audio(temp_audio_path)
            if audio_numpy.size == 0 or len(audio_numpy) < 100:  # 100 samples at 16kHz = 6.25ms
                rprint("[yellow]⚠️ WhisperX load_audio returned empty or too short array, falling back to librosa...[/yellow]")
                audio_numpy, _ = librosa.load(temp_audio_path, sr=16000)
            
            # Ensure numpy array is float32 and not empty
            audio_numpy = audio_numpy.astype(np.float32)
            if audio_numpy.size == 0 or len(audio_numpy) < 100:
                rprint(f"[red]Audio segment too short: {len(audio_numpy)/16000:.6f}s[/red]")
                raise ValueError("Audio segment too short for processing")
            
            # Create tensor version for alignment
            audio_tensor = torch.from_numpy(audio_numpy)
            if audio_tensor.dim() == 1:
                audio_tensor = audio_tensor.unsqueeze(0)
            audio_tensor = audio_tensor.float()
            
            rprint(f"[cyan]Audio numpy shape: {audio_numpy.shape}, dtype: {audio_numpy.dtype}[/cyan]")
            rprint(f"[cyan]Audio tensor shape: {audio_tensor.shape}, dtype: {audio_tensor.dtype}[/cyan]")
            
            # Verify the audio data
            rprint(f"[cyan]Audio duration: {len(audio_numpy)/16000:.3f}s[/cyan]")
            rprint(f"[cyan]Audio range: [{audio_numpy.min():.3f}, {audio_numpy.max():.3f}][/cyan]")
            
        except Exception as e:
            rprint(f"[red]Error loading audio: {str(e)}[/red]")
            raise
        finally:
            # Clean up temp file
            if os.path.exists(temp_audio_path):
                os.unlink(temp_audio_path)

        rprint("[bold green]note: You will see Progress if working correctly[/bold green]")
        result = model.transcribe(audio_numpy, batch_size=batch_size, print_progress=True)

        # Free GPU resources
        gc.collect()
        torch.cuda.empty_cache()
        del model

        # Save language
        save_language(result['language'])
        if result['language'] == 'zh' and WHISPER_LANGUAGE != 'zh':
            raise ValueError("Please specify the transcription language as zh and try again!")

        # Align whisper output
        # ⚠️ 必须显式传 model_dir（= config 的 model_dir，项目内 _model_cache）：
        #   * 英语走 torchaudio 的 WAV2VEC2_* 管线，它最终调到
        #     torch.hub.load_state_dict_from_url -> 只认 TORCH_HOME；
        #   * 其他语言走 HuggingFace 的 from_pretrained(cache_dir=...) ->
        #     传 None 时落到 transformers 默认缓存。
        # 两者都传项目内路径，才不会下到 C:\Users\<你>\.cache\ 里。
        # （运行期 TORCH_HOME / HF_HOME 已由 runtime_libraries.setup() 指到项目内，
        #   这里再显式传一次，双重保险。）
        #
        # 用 core.align_model 而不是直接调 whisperx：日语/中文/韩语等语种的对齐模型
        # 要联网从 HuggingFace 下，上游遇到网络问题只会抛一句误导性的
        # "could not be found in huggingface"（还把 transformers 吞掉网络错误的
        # "make sure you don't have a local directory with the same name" 一起带上），
        # 而且只试一个端点就放弃。core.align_model 会：本地目录优先 → 官方/hf-mirror
        # 逐个试 → 全失败时给出"去哪下、要哪些文件、放到哪个目录"的明确提示。
        # 见 devdocs/05-guides/05-常见故障排查.md「对齐模型」一节。
        model_a, metadata = align_model_utils.load_align_model(
            language_code=result["language"], device=device, model_dir=MODEL_DIR)
        result = whisperx.align(result["segments"], model_a, metadata, audio_tensor, device, return_char_alignments=False)

        # Free GPU resources again
        gc.collect()
        torch.cuda.empty_cache()
        del model_a

        # Adjust timestamps
        for segment in result['segments']:
            segment['start'] += start
            segment['end'] += start
            for word in segment['words']:
                if 'start' in word:
                    word['start'] += start
                if 'end' in word:
                    word['end'] += start
        return result
    except Exception as e:
        rprint(f"[red]WhisperX processing error:[/red] {e}")
        raise


def transcribe_audio_with_volcano(audio_file: str, start: float, end: float) -> Dict:
    """
    使用火山引擎ASR转录音频

    Args:
        audio_file: 音频文件路径
        start: 开始时间（秒）
        end: 结束时间（秒）

    Returns:
        dict: 转录结果
    """
    if not VOLCANO_ASR_AVAILABLE:
        raise ImportError("火山引擎ASR模块不可用，请确保volcano_asr.py文件存在")

    rprint(f"[cyan]🌋 Starting Volcano Engine ASR for segment {start:.2f}s to {end:.2f}s...[/cyan]")

    try:
        # 创建火山引擎ASR实例
        asr = VolcanoASR()

        # 让 TOS 对象键带上视频名，便于在控制台按视频检索/清理
        try:
            video_file = find_video_files()
            asr.audio_name_hint = os.path.splitext(os.path.basename(video_file))[0]
        except Exception:
            pass  # 拿不到视频名不影响转录，只是对象键退化为时间戳命名

        # 转录音频
        result = asr.transcribe_audio(audio_file, start, end)

        # 记录本次识别语言。火山分支此前从不写 whisper.detected_language，
        # 于是自动检测模式下下游只能读到上一个视频的旧值（经 config_utils
        # .get_source_language() 放大成"提示词与 spaCy 模型用错语种"）。
        # save_language() 已对 None/空串/'auto' 做过滤，拿不到就保持原值。
        save_language(result.get('language'))

        rprint(f"[green]✅ Volcano Engine ASR transcription completed[/green]")
        return result

    except Exception as e:
        rprint(f"[red]火山引擎ASR处理错误:[/red] {e}")
        raise


def transcribe_audio(audio_file: str, start: float, end: float) -> Dict:
    """
    转录音频文件，根据配置选择ASR引擎

    Args:
        audio_file: 音频文件路径
        start: 开始时间（秒）
        end: 结束时间（秒）

    Returns:
        dict: 转录结果
    """
    # 获取ASR引擎配置
    asr_engine = load_key("asr_engine")

    rprint(f"[cyan]🔧 Selected ASR engine: {asr_engine}[/cyan]")

    if asr_engine == "volcano":
        # 检查火山引擎配置
        app_id = load_key("volcano_asr.app_id")
        access_token = load_key("volcano_asr.access_token")

        if not app_id or not access_token:
            rprint("[yellow]⚠️ 火山引擎ASR配置不完整，回退到Whisper引擎[/yellow]")
            rprint("[yellow]请在config.yaml中配置volcano_asr.app_id和volcano_asr.access_token[/yellow]")
            asr_engine = "whisper"

    if asr_engine == "volcano":
        return transcribe_audio_with_volcano(audio_file, start, end)
    else:
        return transcribe_audio_with_whisper(audio_file, start, end)


def enhance_vocals(target_db=-20.0, asr_engine="whisper"):
    """把人声轨归一到合适电平后再送去识别。

    原先这里是固定的 `volume=2.50`：安静素材仍然偏轻、响亮素材直接削顶。
    现在按实测电平算增益（峰值受限），但**编码参数完全不变** ——
    火山侧会用 ffprobe 校验 16kHz/单声道/s16，不能动。

    Args:
        target_db: 目标平均电平（dBFS）
        asr_engine: ASR引擎类型，决定输出格式
    """
    if not load_key("demucs"):
        # 不使用Demucs时，根据ASR引擎返回相应的原始音频文件
        if asr_engine == "volcano":
            return RAW_AUDIO_WAV_FILE
        else:
            return RAW_AUDIO_FILE

    try:
        gain = compute_normalization_gain(VOCAL_AUDIO_FILE, target_db=target_db)
        print(f"[cyan]🎙️ Normalizing vocals by {gain:+.2f}dB (target {target_db}dBFS)[/cyan]")

        if asr_engine == "volcano":
            # 火山引擎需要WAV格式
            enhanced_vocal_wav = "output/audio/enhanced_vocals.wav"
            ffmpeg_cmd = (
                f'ffmpeg -y -i "{VOCAL_AUDIO_FILE}" '
                f'-filter:a "volume={gain}dB" '
                f'-ar 16000 -ac 1 -acodec pcm_s16le -f wav '
                f'"{enhanced_vocal_wav}"'
            )
            output_file = enhanced_vocal_wav
        else:
            # Whisper使用MP3格式
            ffmpeg_cmd = (
                f'ffmpeg -y -i "{VOCAL_AUDIO_FILE}" '
                f'-filter:a "volume={gain}dB" '
                f'"{ENHANCED_VOCAL_PATH}"'
            )
            output_file = ENHANCED_VOCAL_PATH

        subprocess.run(ffmpeg_cmd, shell=True, check=True, capture_output=True)

        return output_file
    except subprocess.CalledProcessError as e:
        print(f"[red]Error enhancing vocals: {str(e)}[/red]")
        # Fallback to original vocals if enhancement fails
        if asr_engine == "volcano":
            # 需要将VOCAL_AUDIO_FILE (MP3) 转换为WAV格式
            try:
                vocal_wav = "output/audio/vocal.wav"
                ffmpeg_cmd = (
                    f'ffmpeg -y -i "{VOCAL_AUDIO_FILE}" '
                    f'-ar 16000 -ac 1 -acodec pcm_s16le -f wav '
                    f'"{vocal_wav}"'
                )
                subprocess.run(ffmpeg_cmd, shell=True, check=True, capture_output=True)
                return vocal_wav
            except:
                return RAW_AUDIO_WAV_FILE  # 最终回退到原始WAV文件
        else:
            return VOCAL_AUDIO_FILE  # Fallback to original vocals if enhancement fails
    
def _asr_cache_settings() -> Dict:
    """构造参与转录缓存身份的 ASR 设置。

    只放"会实质改变识别结果"的东西：引擎、模型、识别语言、是否人声分离，
    以及火山侧那一组会影响输出的参数。不含密钥、文件名与翻译设置。
    """
    whisper = load_key("whisper")
    settings = {
        "asr_engine": load_key("asr_engine"),
        "model": whisper.get("model") if isinstance(whisper, dict) else None,
        "language": whisper.get("language") if isinstance(whisper, dict) else None,
        "demucs": bool(load_key("demucs")),
    }
    if settings["asr_engine"] == "volcano":
        volcano = load_key("volcano_asr") or {}
        if isinstance(volcano, dict):
            settings["volcano"] = {
                key: volcano.get(key) for key in (
                    "resource_id", "language", "enable_punc", "enable_itn",
                    "enable_ddc", "enable_speaker_info", "show_utterances",
                    "enable_channel_split", "vad_segment", "model_version",
                )
            }
    return settings


def transcribe():
    if os.path.exists(CLEANED_CHUNKS_EXCEL_PATH):
        rprint("[yellow]⚠️ Transcription results already exist, skipping transcription step.[/yellow]")
        return

    # step0 准备音频。find_media_file() 同时接受视频与音频输入：
    # 上传音频时不再包成 black_screen.mp4，convert_video_to_audio() 对音频文件
    # 同样适用（ffmpeg 的 -vn 只是丢弃不存在的视频轨）。
    video_file, _media_type = find_media_file()

    # 内容寻址缓存：命中"完整结果"时连 Demucs 人声分离都一起跳过。
    # 这是 dev 相对上游能多省一步的地方 —— dev 没有配音链路消费 vocal.mp3，
    # 所以缓存命中时根本不需要生成它。
    cache_enabled = load_key_or("whisper.cache", True)
    cache_key = None
    if cache_enabled:
        try:
            cache_key = transcription_cache.cache_key(video_file, _asr_cache_settings())
        except OSError as e:
            rprint(f"[yellow]⚠️ 计算转录缓存键失败，本次不启用缓存: {e}[/yellow]")
            cache_key = None

    if cache_key:
        cached = transcription_cache.read_result(cache_key, "complete")
        if cached:
            rprint("[green]♻️ 命中转录缓存：跳过音频分离与识别，直接复用结果[/green]")
            save_language(cached.get('language'))
            df = process_transcription({'segments': cached['result']['segments']})
            save_results(df)
            return

    convert_video_to_audio(video_file)

    # step1 Demucs vocal separation:
    if load_key("demucs"):
        demucs_main()
    
    # step2 根据ASR引擎选择音频处理流程
    asr_engine = load_key("asr_engine")

    if asr_engine == "volcano":
        # 使用火山引擎：直接使用WAV格式的原始音频
        choose_audio = enhance_vocals(asr_engine=asr_engine) if load_key("demucs") else RAW_AUDIO_WAV_FILE
        # 火山引擎使用原始WAV文件，不需要额外转换
        volcano_audio = choose_audio
        # 为split_audio函数准备一个MP3版本（split_audio可能期望MP3）
        # 但split_audio应该能处理WAV文件，所以我们可以直接使用WAV
        audio_for_split = choose_audio
        # 为Whisper分支定义whisper_audio变量（虽然不会使用）
        whisper_audio = None
    else:
        # 使用Whisper：使用MP3格式的原始音频
        choose_audio = enhance_vocals(asr_engine=asr_engine) if load_key("demucs") else RAW_AUDIO_FILE
        # 压缩音频用于Whisper转录
        whisper_audio = compress_audio(choose_audio, WHISPER_FILE)
        audio_for_split = whisper_audio
        volcano_audio = None

    # step3 Extract audio
    segments = split_audio(audio_for_split)

    # step4 Transcribe audio
    all_results = []
    for start, end in segments:
        # 暂停时在此阻塞，停止时抛 StopTask 退出（分段结果已写入缓存，
        # 重新开始时只需补缺失的段）
        eu.check_cancel()

        # 根据ASR引擎选择正确的音频文件
        if asr_engine == "volcano" and volcano_audio:
            audio_file_for_transcription = volcano_audio
        else:
            audio_file_for_transcription = whisper_audio

        # 分段级缓存：长视频中途失败/被中断时，重跑只需补缺失的段
        part = f"{start:.2f}_{end:.2f}"
        if cache_key:
            cached_part = transcription_cache.read_result(cache_key, part)
            if cached_part:
                rprint(f"[cyan]♻️ 复用缓存分段 {part}[/cyan]")
                all_results.append(cached_part['result'])
                continue

        result = transcribe_audio(audio_file_for_transcription, start, end)
        if cache_key:
            transcription_cache.write_result(
                cache_key, part, result, result.get('language')
            )
        all_results.append(result)
    
    # step5 Combine results
    combined_result = {'segments': []}
    for result in all_results:
        combined_result['segments'].extend(result['segments'])

    # 写"完整结果"条目。语言取各分段里第一个非空值。
    if cache_key:
        language = next(
            (r.get('language') for r in all_results if r.get('language')), None
        )
        transcription_cache.write_result(cache_key, "complete", combined_result, language)

    # step6 Process df
    df = process_transcription(combined_result)
    save_results(df)
        
if __name__ == "__main__":
    # Windows 控制台默认是 GBK，本模块会打印 emoji 与中文，直接运行会抛
    # UnicodeEncodeError（实测 `python -m core.step2_whisperX` 会崩）。
    # rich 的终端编码是首次打印时才决定的，因此在入口处补一次即可修复。
    try:
        import easy_util as _eu
        _eu.ensure_utf8_console()
    except Exception:
        pass
    transcribe()