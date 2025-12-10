import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import warnings
warnings.filterwarnings("ignore")

import whisperx
import torch
from typing import Dict
import librosa
from rich import print as rprint
import subprocess
import tempfile
import time
import gc
import numpy as np

from core.config_utils import load_key
from core.all_whisper_methods.demucs_vl import demucs_main, RAW_AUDIO_FILE, VOCAL_AUDIO_FILE
from core.all_whisper_methods.whisperX_utils import process_transcription, convert_video_to_audio, split_audio, save_results, save_language, compress_audio, convert_to_volcano_wav, CLEANED_CHUNKS_EXCEL_PATH, RAW_AUDIO_WAV_FILE
from core.step1_ytdlp import find_video_files

# 尝试导入火山引擎ASR
try:
    from core.all_whisper_methods.volcano_asr import VolcanoASR
    VOLCANO_ASR_AVAILABLE = True
except ImportError:
    VOLCANO_ASR_AVAILABLE = False
    rprint("[yellow]⚠️ 火山引擎ASR模块导入失败，确保volcano_asr.py文件存在[/yellow]")

MODEL_DIR = load_key("model_dir")
WHISPER_FILE = "output/audio/for_whisper.mp3"
VOLCANO_FILE = "output/audio/for_volcano.wav"
ENHANCED_VOCAL_PATH = "output/audio/enhanced_vocals.mp3"

def check_hf_mirror() -> str:
    """Check and return the fastest HF mirror"""
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
    return fastest_url

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
        if WHISPER_LANGUAGE == 'zh':
            model_name = "Huan69/Belle-whisper-large-v3-zh-punct-fasterwhisper"
            local_model = os.path.join(MODEL_DIR, "Belle-whisper-large-v3-zh-punct-fasterwhisper")
        else:
            model_name = load_key("whisper.model")
            local_model = os.path.join(MODEL_DIR, model_name)
            
        if os.path.exists(local_model):
            rprint(f"[green]📥 Loading local WHISPER model:[/green] {local_model} ...")
            model_name = local_model
        else:
            rprint(f"[green]📥 Using WHISPER model from HuggingFace:[/green] {model_name} ...")

        vad_options = {"vad_onset": 0.500,"vad_offset": 0.363}
        asr_options = {"temperatures": [0],"initial_prompt": "",}
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
            # Try loading with whisperx first
            audio_numpy = whisperx.load_audio(temp_audio_path)
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
        model_a, metadata = whisperx.load_align_model(language_code=result["language"], device=device)
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

        # 转录音频
        result = asr.transcribe_audio(audio_file, start, end)

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


def enhance_vocals(vocals_ratio=2.50, asr_engine="whisper"):
    """Enhance vocals audio volume

    Args:
        vocals_ratio: 音量增强比例
        asr_engine: ASR引擎类型，决定输出格式
    """
    if not load_key("demucs"):
        # 不使用Demucs时，根据ASR引擎返回相应的原始音频文件
        if asr_engine == "volcano":
            return RAW_AUDIO_WAV_FILE
        else:
            return RAW_AUDIO_FILE

    try:
        print(f"[cyan]🎙️ Enhancing vocals with volume ratio: {vocals_ratio}[/cyan]")

        if asr_engine == "volcano":
            # 火山引擎需要WAV格式
            enhanced_vocal_wav = "output/audio/enhanced_vocals.wav"
            ffmpeg_cmd = (
                f'ffmpeg -y -i "{VOCAL_AUDIO_FILE}" '
                f'-filter:a "volume={vocals_ratio}" '
                f'-ar 16000 -ac 1 -acodec pcm_s16le -f wav '
                f'"{enhanced_vocal_wav}"'
            )
            output_file = enhanced_vocal_wav
        else:
            # Whisper使用MP3格式
            ffmpeg_cmd = (
                f'ffmpeg -y -i "{VOCAL_AUDIO_FILE}" '
                f'-filter:a "volume={vocals_ratio}" '
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
    
def transcribe():
    if os.path.exists(CLEANED_CHUNKS_EXCEL_PATH):
        rprint("[yellow]⚠️ Transcription results already exist, skipping transcription step.[/yellow]")
        return
    
    # step0 Convert video to audio
    video_file = find_video_files()
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
        # 根据ASR引擎选择正确的音频文件
        if asr_engine == "volcano" and volcano_audio:
            audio_file_for_transcription = volcano_audio
        else:
            audio_file_for_transcription = whisper_audio

        result = transcribe_audio(audio_file_for_transcription, start, end)
        all_results.append(result)
    
    # step5 Combine results
    combined_result = {'segments': []}
    for result in all_results:
        combined_result['segments'].extend(result['segments'])
    
    # step6 Process df
    df = process_transcription(combined_result)
    save_results(df)
        
if __name__ == "__main__":
    transcribe()