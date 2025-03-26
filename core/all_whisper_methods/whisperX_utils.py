import os, sys, subprocess
import pandas as pd
from typing import Dict, List, Tuple
from rich import print
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from core.config_utils import update_key

AUDIO_DIR = "output/audio"
RAW_AUDIO_FILE = "output/audio/raw.mp3"
CLEANED_CHUNKS_EXCEL_PATH = "output/log/cleaned_chunks.xlsx"

def compress_audio(input_file: str, output_file: str):
    """将输入音频文件压缩为低质量音频文件，用于转录"""
    if not os.path.exists(output_file):
        print(f"🗜️ Converting to low quality audio with FFmpeg ......")
        # 16000 Hz, 1 channel, (Whisper default) , 96kbps to keep more details as well as smaller file size
        subprocess.run([
            'ffmpeg', '-y', '-i', input_file, '-vn', '-b:a', '96k',
            '-ar', '16000', '-ac', '1', '-metadata', 'encoding=UTF-8',
            '-f', 'mp3', output_file
        ], check=True, stderr=subprocess.PIPE)
        print(f"🗜️ Converted <{input_file}> to <{output_file}> with FFmpeg")
    return output_file

def convert_video_to_audio(video_file: str):
    os.makedirs(AUDIO_DIR, exist_ok=True)
    if not os.path.exists(RAW_AUDIO_FILE):
        print(f"🎬➡️🎵 Converting to high quality audio with FFmpeg ......")
        subprocess.run([
            'ffmpeg', '-y', '-i', video_file, '-vn',
            '-c:a', 'libmp3lame', '-b:a', '128k',
            '-ar', '32000',
            '-ac', '1', 
            '-metadata', 'encoding=UTF-8', RAW_AUDIO_FILE
        ], check=True, stderr=subprocess.PIPE)
        print(f"🎬➡️🎵 Converted <{video_file}> to <{RAW_AUDIO_FILE}> with FFmpeg\n")

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
            # Check word length
            if len(word["word"]) > 20:
                print(f"⚠️ Warning: Detected word longer than 20 characters, skipping: {word['word']}")
                continue
                
            # ! For French, we need to convert guillemets to empty strings
            word["word"] = word["word"].replace('»', '').replace('«', '')
            
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

def save_language(language: str):
    update_key("whisper.detected_language", language)