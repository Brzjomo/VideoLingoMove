#!/usr/bin/env python3
"""
直接从转录JSON文件生成字幕的脚本
跳过转录步骤，使用已有的ASR结果
"""

import os
import sys
import json
import argparse
from typing import Dict, List, Tuple, Optional

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 尝试导入rich，失败时使用简单回退
try:
    from rich.console import Console as RichConsole
    from rich.panel import Panel
    rich_available = True
    console = RichConsole()
except ImportError:
    rich_available = False
    # 简单的控制台输出
    class SimpleConsole:
        def print(self, message):
            # 移除rich标记
            import re
            message = re.sub(r'\[[^]]*\]', '', message)
            print(message)
    console = SimpleConsole()

def print_panel(title, style=""):
    if rich_available:
        console.print(Panel(title, style=style or "bold blue"))
    else:
        print("\n" + "=" * 60)
        print(f"  {title}")
        print("=" * 60 + "\n")

def convert_to_srt_format(start_time: float, end_time: float) -> str:
    """Convert time (in seconds) to the format: hours:minutes:seconds,milliseconds"""
    def seconds_to_hmsm(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = seconds % 60
        milliseconds = int(seconds * 1000) % 1000
        return f"{hours:02d}:{minutes:02d}:{int(seconds):02d},{milliseconds:03d}"

    start_srt = seconds_to_hmsm(start_time)
    end_srt = seconds_to_hmsm(end_time)
    return f"{start_srt} --> {end_srt}"

def load_json_file(json_path: str) -> Dict:
    """加载JSON文件"""
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        console.print(f"[green]✅ 成功加载JSON文件: {json_path}[/green]")
        return data
    except Exception as e:
        console.print(f"[red]❌ 加载JSON文件失败: {str(e)}[/red]")
        raise

def extract_segments(data: Dict) -> List[Dict]:
    """
    从JSON数据中提取segments

    支持多种格式：
    1. 火山引擎ASR简化版: {"segments": [...]}
    2. 火山引擎ASR完整版: {"converted_result": {"segments": [...]}}
    3. Whisper格式: {"segments": [...]}
    """
    segments = []

    # 检查简化版格式
    if 'segments' in data:
        segments = data['segments']
    # 检查完整版格式
    elif 'converted_result' in data and 'segments' in data['converted_result']:
        segments = data['converted_result']['segments']
    # 检查原始火山引擎格式
    elif 'original_result' in data and 'result' in data['original_result']:
        # 需要转换火山引擎格式
        from core.all_whisper_methods.volcano_asr import VolcanoASR
        asr = VolcanoASR()
        whisper_result = asr._convert_to_whisper_format(data['original_result'])
        segments = whisper_result['segments']
    else:
        raise ValueError("无法识别JSON格式：未找到segments或converted_result字段")

    console.print(f"[green]✅ 提取到 {len(segments)} 个segments[/green]")
    return segments

def generate_srt_from_segments(segments: List[Dict], output_path: str) -> None:
    """从segments生成SRT字幕文件"""
    srt_lines = []

    for i, segment in enumerate(segments, 1):
        start = segment.get('start', 0)
        end = segment.get('end', 0)
        text = segment.get('text', '').strip()

        # 跳过空文本segment
        if not text:
            continue

        # SRT格式：序号
        srt_lines.append(str(i))

        # 时间轴
        srt_lines.append(convert_to_srt_format(start, end))

        # 文本
        srt_lines.append(text)

        # 空行分隔
        srt_lines.append('')

    # 写入文件
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(srt_lines))

    console.print(f"[green]✅ SRT字幕文件已生成: {output_path}[/green]")
    console.print(f"[cyan]包含 {len(srt_lines)//4} 个字幕条目[/cyan]")

def generate_excel_from_segments(segments: List[Dict], output_path: str) -> None:
    """从segments生成Excel文件（兼容现有流程）"""
    import pandas as pd

    # 提取所有单词
    all_words = []
    for segment in segments:
        words = segment.get('words', [])
        if words:
            for word in words:
                word_text = word.get('word', '').strip()
                if word_text:
                    all_words.append({
                        'text': word_text,
                        'start': word.get('start', 0),
                        'end': word.get('end', 0)
                    })
        else:
            # 如果没有单词级时间戳，使用segment文本
            text = segment.get('text', '').strip()
            if text:
                all_words.append({
                    'text': text,
                    'start': segment.get('start', 0),
                    'end': segment.get('end', 0)
                })

    # 创建DataFrame
    df = pd.DataFrame(all_words)
    df['text'] = df['text'].apply(lambda x: f'"{x}"')

    # 保存Excel
    df.to_excel(output_path, index=False)
    console.print(f"[green]✅ Excel文件已生成: {output_path}[/green]")
    console.print(f"[cyan]包含 {len(df)} 个单词[/cyan]")

def main():
    parser = argparse.ArgumentParser(description='直接从转录JSON文件生成字幕')
    parser.add_argument('json_file', help='转录JSON文件路径')
    parser.add_argument('--output-srt', '-s', default='output/subtitles.srt',
                       help='SRT输出文件路径 (默认: output/subtitles.srt)')
    parser.add_argument('--output-excel', '-e', default='output/log/cleaned_chunks.xlsx',
                       help='Excel输出文件路径 (默认: output/log/cleaned_chunks.xlsx)')
    parser.add_argument('--only-srt', action='store_true',
                       help='只生成SRT文件，不生成Excel文件')
    parser.add_argument('--only-excel', action='store_true',
                       help='只生成Excel文件，不生成SRT文件')

    args = parser.parse_args()

    print_panel("📝 JSON转字幕工具")

    # 检查输入文件
    if not os.path.exists(args.json_file):
        console.print(f"[red]❌ JSON文件不存在: {args.json_file}[/red]")
        sys.exit(1)

    # 加载JSON数据
    data = load_json_file(args.json_file)

    # 提取segments
    segments = extract_segments(data)

    if not segments:
        console.print("[red]❌ 未找到有效的segments[/red]")
        sys.exit(1)

    # 创建输出目录
    os.makedirs(os.path.dirname(args.output_srt) if os.path.dirname(args.output_srt) else '.', exist_ok=True)
    if not args.only_srt:
        os.makedirs(os.path.dirname(args.output_excel) if os.path.dirname(args.output_excel) else '.', exist_ok=True)

    # 生成SRT文件
    if not args.only_excel:
        generate_srt_from_segments(segments, args.output_srt)

    # 生成Excel文件（用于后续处理）
    if not args.only_srt:
        generate_excel_from_segments(segments, args.output_excel)

    print_panel("✅ 处理完成！", "bold green")
    console.print(f"[cyan]输入JSON: {args.json_file}[/cyan]")
    if not args.only_excel:
        console.print(f"[cyan]输出SRT: {args.output_srt}[/cyan]")
    if not args.only_srt:
        console.print(f"[cyan]输出Excel: {args.output_excel}[/cyan]")

if __name__ == '__main__':
    main()