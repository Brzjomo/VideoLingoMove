import os, sys, subprocess, math
import pandas as pd
from typing import Dict, List, Tuple
from rich import print
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.config_utils import update_key

AUDIO_DIR = "output/audio"
RAW_AUDIO_FILE = "output/audio/raw.mp3"  # 向后兼容
RAW_AUDIO_WAV_FILE = "output/audio/raw.wav"  # 新的原始WAV文件
CLEANED_CHUNKS_EXCEL_PATH = "output/log/cleaned_chunks.xlsx"

def _ffmpeg_has_encoder(encoder_name: str) -> bool:
    """探测当前 ffmpeg 是否带某个编码器。

    conda-forge / 精简构建的 ffmpeg 常常没有 libmp3lame，此前会直接抛
    CalledProcessError 让整条流程失败。这里探测一次，缺失时回退到 PCM。
    """
    try:
        result = subprocess.run(['ffmpeg', '-hide_banner', '-encoders'],
                                capture_output=True, text=True, check=False)
    except OSError:
        return False
    return encoder_name in (result.stdout or '')


def compress_audio(input_file: str, output_file: str):
    """将输入音频文件压缩为低质量音频文件，用于转录"""
    if not os.path.exists(output_file):
        print(f"🗜️ Converting to low quality audio with FFmpeg ......")
        # 16000 Hz, 1 channel, (Whisper default) , 96kbps to keep more details as well as smaller file size
        if _ffmpeg_has_encoder('libmp3lame'):
            cmd = [
                'ffmpeg', '-y', '-i', input_file, '-vn', '-b:a', '96k',
                '-ar', '16000', '-ac', '1', '-metadata', 'encoding=UTF-8',
                '-f', 'mp3', output_file
            ]
        else:
            # 回退：无 libmp3lame 时输出 PCM/WAV。下游（whisperX 的 load_audio、
            # pydub）都按文件头识别格式，不依赖扩展名。
            print("[yellow]⚠️ ffmpeg 缺少 libmp3lame，回退为 WAV(PCM) 编码[/yellow]")
            cmd = [
                'ffmpeg', '-y', '-i', input_file, '-vn',
                '-c:a', 'pcm_s16le', '-ar', '16000', '-ac', '1',
                '-f', 'wav', output_file
            ]
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE)
        print(f"🗜️ Converted <{input_file}> to <{output_file}> with FFmpeg")
    return output_file


def convert_video_to_audio(video_file: str):
    os.makedirs(AUDIO_DIR, exist_ok=True)

    # --- 1) raw.wav：火山引擎 ASR 专用 ---
    # ⚠️ 16kHz / 单声道 / 16-bit PCM 这三个参数是火山侧的硬约定：
    #    volcano_asr._convert_audio_for_volcano() 会用 ffprobe 校验 sample_rate=16000、
    #    channels=1、bits_per_sample=16，改动任意一项都会让火山分支直接失败。
    #
    # `aresample=async=1:first_pts=0` 用于把"解码出的采样点"与"容器呈现时间轴"
    # 重新对齐：压缩帧可能解码出比容器时长更多的采样点，逐点拼接会把识别时钟
    # 越推越后，导致字幕整体逐渐偏移（且下游无法修复，因为成片用的是同一条时钟）。
    if not os.path.exists(RAW_AUDIO_WAV_FILE):
        print(f"🎬➡️🎵 Converting video to high quality WAV audio (Volcano ASR format) ......")
        subprocess.run([
            'ffmpeg', '-y', '-i', video_file, '-vn',
            '-af', 'aresample=async=1:first_pts=0',
            '-ar', '16000',          # 采样率 16kHz
            '-ac', '1',              # 单声道
            '-acodec', 'pcm_s16le',  # 16-bit PCM
            '-metadata', 'encoding=UTF-8',
            '-f', 'wav',             # WAV格式
            RAW_AUDIO_WAV_FILE
        ], check=True, stderr=subprocess.PIPE)
        print(f"🎬➡️🎵 Converted <{video_file}> to Volcano ASR format: <{RAW_AUDIO_WAV_FILE}>\n")

    # --- 2) raw.mp3：Whisper 转录 + Demucs 人声分离的输入 ---
    # 必须直接从**源视频**编码。旧实现是从上面那个 16kHz 的 raw.wav 转码出来的，
    # 虽然写着 `-ar 32000`，实际带宽已被 raw.wav 限制在 8kHz 以内 —— 参数是假的，
    # Demucs 与 Whisper 拿到的都是窄带音频。
    if not os.path.exists(RAW_AUDIO_FILE):
        print(f"🎬➡️🎵 Generating MP3 for Whisper/Demucs ......")
        common = ['ffmpeg', '-y', '-i', video_file, '-vn',
                  '-af', 'aresample=async=1:first_pts=0']
        if _ffmpeg_has_encoder('libmp3lame'):
            cmd = common + ['-c:a', 'libmp3lame', '-b:a', '128k',
                            '-ar', '32000', '-ac', '1',
                            '-metadata', 'encoding=UTF-8', RAW_AUDIO_FILE]
        else:
            print("[yellow]⚠️ ffmpeg 缺少 libmp3lame，回退为 WAV(PCM) 编码[/yellow]")
            cmd = common + ['-c:a', 'pcm_s16le', '-ar', '32000', '-ac', '1',
                            '-f', 'wav', RAW_AUDIO_FILE]
        subprocess.run(cmd, check=True, stderr=subprocess.PIPE)
        print(f"🎬➡️🎵 Generated MP3 for Whisper: <{RAW_AUDIO_FILE}>\n")

def _detect_silence(audio_file: str, start: float, end: float) -> List[float]:
    """Detect silence points in the given audio segment"""
    cmd = ['ffmpeg', '-y', '-i', audio_file, 
           '-ss', str(start), '-to', str(end),
           '-af', 'silencedetect=n=-30dB:d=0.5', 
           '-f', 'null', '-']
    
    output = subprocess.run(cmd, capture_output=True, text=True, 
                          encoding='utf-8').stderr
    
    return [float(line.split('silence_end: ')[1].split(' ')[0])
            for line in output.split('\n')
            if 'silence_end' in line]

def get_audio_duration(audio_file: str) -> float:
    """Get the duration of an audio file using ffmpeg."""
    if not os.path.exists(audio_file):
        print(f"[red]Error: Audio file does not exist: {audio_file}[/red]")
        raise FileNotFoundError(f"Audio file not found: {audio_file}")
        
    cmd = ['ffmpeg', '-i', audio_file]
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    output = stderr.decode('utf-8', errors='ignore')
    
    # print(f"[cyan]FFmpeg output for duration check:[/cyan]")
    # print(output)
    
    try:
        duration_lines = [line for line in output.split('\n') if 'Duration' in line]
        if not duration_lines:
            print("[red]Error: Could not find duration information in FFmpeg output[/red]")
            raise ValueError("No duration information found")
            
        duration_str = duration_lines[0]
        duration_parts = duration_str.split('Duration: ')[1].split(',')[0].split(':')
        duration = float(duration_parts[0])*3600 + float(duration_parts[1])*60 + float(duration_parts[2])
        
        if duration <= 0:
            print(f"[red]Error: Invalid duration: {duration}s[/red]")
            raise ValueError(f"Invalid duration: {duration}s")
            
        print(f"[green]Successfully got audio duration: {duration:.2f}s ({duration/60:.2f} minutes)[/green]")
        return duration
    except Exception as e:
        print(f"[red]Error: Failed to get audio duration: {str(e)}[/red]")
        print(f"[red]FFmpeg output: {output}[/red]")
        raise

def split_audio(audio_file: str, target_len: int = 30*60, win: int = 60, min_segment_len: float = 0.5) -> List[Tuple[float, float]]:
    # 30 min 16000 Hz 96kbps ~ 22MB < 25MB required by whisper
    print("[bold blue]🔪 Starting audio segmentation...[/]")
    
    duration = get_audio_duration(audio_file)
    print(f"[cyan]Total audio duration: {duration:.2f}s ({duration/60:.2f} minutes)[/cyan]")
    
    if duration == 0:
        print("[red]Error: Could not get audio duration, please check the audio file.[/red]")
        raise ValueError("Invalid audio duration")
    
    # 确保目标长度不超过音频总长度
    target_len = min(target_len, duration)
    print(f"[cyan]Target segment length: {target_len}s[/cyan]")
    
    segments = []
    pos = 0
    while pos < duration:
        remaining = duration - pos
        print(f"[cyan]Processing position {pos:.2f}s, remaining {remaining:.2f}s[/cyan]")
        
        if remaining < min_segment_len:
            # If remaining duration is too short, merge with previous segment
            if segments:
                last_start, _ = segments[-1]
                segments[-1] = (last_start, duration)
                print(f"[yellow]Merging short remaining segment with previous: {last_start:.2f}s -> {duration:.2f}s[/yellow]")
            break
        elif remaining < target_len:
            segments.append((pos, duration))
            print(f"[green]Adding final segment: {pos:.2f}s -> {duration:.2f}s[/green]")
            break
            
        win_start = pos + target_len - win
        win_end = min(win_start + 2 * win, duration)
        print(f"[cyan]Searching for silence between {win_start:.2f}s and {win_end:.2f}s[/cyan]")
        
        silences = _detect_silence(audio_file, win_start, win_end)
        if silences:
            print(f"[green]Found {len(silences)} silence points: {', '.join(f'{t:.2f}s' for t in silences)}[/green]")
            target_pos = target_len - (win_start - pos)
            # Find a silence point that results in segments longer than min_segment_len
            valid_splits = [t for t in silences if t - win_start > target_pos and t - pos >= min_segment_len]
            split_at = next(iter(valid_splits), None) if valid_splits else None
            
            if split_at:
                segments.append((pos, split_at))
                print(f"[green]Adding segment at silence: {pos:.2f}s -> {split_at:.2f}s[/green]")
                pos = split_at
                continue
        else:
            print("[yellow]No silence points found in window[/yellow]")
                
        # If no valid silence point found, use target_len
        next_pos = pos + target_len
        if duration - next_pos < min_segment_len:
            # If remaining would be too short, extend current segment to end
            segments.append((pos, duration))
            print(f"[green]Adding final segment (no silence): {pos:.2f}s -> {duration:.2f}s[/green]")
            break
        else:
            segments.append((pos, next_pos))
            print(f"[green]Adding regular segment: {pos:.2f}s -> {next_pos:.2f}s[/green]")
            pos = next_pos
    
    print(f"\n[bold blue]🔪 Audio split into {len(segments)} segments:[/bold blue]")
    total_duration = 0
    for i, (start, end) in enumerate(segments):
        segment_duration = end - start
        total_duration += segment_duration
        print(f"  Segment {i+1}: {start:.2f}s -> {end:.2f}s (duration: {segment_duration:.2f}s)")
    print(f"[bold blue]Total segments duration: {total_duration:.2f}s[/bold blue]")
    
    if abs(total_duration - duration) > 1.0:  # 允许1秒的误差
        print(f"[red]Warning: Total segments duration ({total_duration:.2f}s) differs from audio duration ({duration:.2f}s)[/red]")
    
    return segments

def process_transcription(result: Dict) -> pd.DataFrame:
    all_words = []
    for segment in result['segments']:
        for word in segment['words']:
            # 跳过空格单词和空文本
            word_text = word.get("word", "")
            if word_text and word_text.strip() == "":
                continue

            # Check word length
            if len(word_text) > 20:
                print(f"⚠️ Warning: Detected word longer than 20 characters, skipping: {word_text}")
                continue

            # ! For French, we need to convert guillemets to empty strings
            word_text = word_text.replace('»', '').replace('«', '')
            word["word"] = word_text
            
            if 'start' not in word and 'end' not in word:
                if all_words:
                    # Assign the end time of the previous word as the start and end time of the current word
                    word_dict = {
                        'text': word["word"],
                        'start': all_words[-1]['end'],
                        'end': all_words[-1]['end'],
                    }
                    all_words.append(word_dict)
                else:
                    # If it's the first word, look next for a timestamp then assign it to the current word
                    next_word = next((w for w in segment['words'] if 'start' in w and 'end' in w), None)
                    if next_word:
                        word_dict = {
                            'text': word["word"],
                            'start': next_word["start"],
                            'end': next_word["end"],
                        }
                        all_words.append(word_dict)
                    else:
                        raise Exception(f"No next word with timestamp found for the current word : {word}")
            else:
                # Normal case, with start and end times
                word_dict = {
                    'text': f'{word["word"]}',
                    'start': word.get('start', all_words[-1]['end'] if all_words else 0),
                    'end': word['end'],
                }
                
                all_words.append(word_dict)
    
    return pd.DataFrame(all_words)

def save_results(df: pd.DataFrame):
    os.makedirs('output/log', exist_ok=True)

    # Remove rows where 'text' is empty
    initial_rows = len(df)
    df = df[df['text'].str.len() > 0]
    removed_rows = initial_rows - len(df)
    if removed_rows > 0:
        print(f"ℹ️ Removed {removed_rows} row(s) with empty text.")
    
    # Check for and remove words longer than 20 characters
    long_words = df[df['text'].str.len() > 20]
    if not long_words.empty:
        print(f"⚠️ Warning: Detected {len(long_words)} word(s) longer than 20 characters. These will be removed.")
        df = df[df['text'].str.len() <= 20]
    
    df['text'] = df['text'].apply(lambda x: f'"{x}"')
    df.to_excel(CLEANED_CHUNKS_EXCEL_PATH, index=False)
    print(f"📊 Excel file saved to {CLEANED_CHUNKS_EXCEL_PATH}")

def compute_normalization_gain(audio_path: str, target_db: float = -20.0,
                              peak_ceiling_db: float = -1.0):
    """算出把音频整体归一到 target_db 所需的增益（dB），并保证峰值不超上限。

    移植上游 c5f8fe7 的思路，但**只算增益、不改编码**：dev 现有的 ffmpeg
    命令负责容器/采样率/位深（火山侧还会用 ffprobe 校验 16k/单声道/s16），
    这里把结果作为 `volume={gain}dB` 传给同一条命令，既替换掉原先拍脑袋的
    固定 ×2.50，又不触碰任何已验证的编码参数。

    为什么要限制增益：pydub 在整数样本上加增益，一段"语音 + 长静音"的素材
    平均电平很低但峰值很高，直接按平均值补足会削顶。这里让峰值停在
    peak_ceiling_db 以下；**衰减从不限制**。

    Returns:
        float: 增益（dB）。无法计算时返回 0.0（即不加增益）。
    """
    try:
        from pydub import AudioSegment
    except ImportError:
        print("[yellow]⚠️ 未安装 pydub，跳过音量归一化[/yellow]")
        return 0.0
    try:
        audio = AudioSegment.from_file(audio_path)
        change_in_dBFS = target_db - audio.dBFS
        headroom = peak_ceiling_db - audio.max_dBFS
    except Exception as e:
        print(f"[yellow]⚠️ 无法分析音频电平（{e}），跳过音量归一化[/yellow]")
        return 0.0

    if not math.isfinite(change_in_dBFS) or not math.isfinite(headroom):
        return 0.0
    if change_in_dBFS > headroom:
        print(f"[yellow]⚠️ 增益受限为 {headroom:+.1f}dB（原需 {change_in_dBFS:+.1f}dB），"
              f"以保证峰值不超过 {peak_ceiling_db:.1f}dBFS[/yellow]")
        return headroom
    return change_in_dBFS


def save_language(language: str):
    """记录本次识别实际使用的语言。

    只在拿到确定值时才写入：None / 空串 / 'auto' 一律跳过。
    伪造一个语言（例如自动检测失败时兜底 'en'）比不写更糟——
    core.config_utils.get_source_language() 会把它当成真实源语言，
    让提示词与 spaCy 模型全部用错语种。
    """
    if not isinstance(language, str):
        return
    language = language.strip()
    if not language or language == 'auto':
        return
    update_key("whisper.detected_language", language)