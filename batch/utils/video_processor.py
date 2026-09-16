import os, sys
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from st_components.imports_and_utils import *
from core.onekeycleanup import cleanup
from core.config_utils import load_key
import shutil, time
from functools import partial
from rich.panel import Panel
from rich.console import Console
import easy_util as eu
import time

console = Console()

INPUT_DIR = 'batch/input'
OUTPUT_DIR = 'output'
SAVE_DIR = 'batch/output'
ERROR_OUTPUT_DIR = 'batch/output/ERROR'
YTB_RESOLUTION_KEY = "ytb_resolution"

def process_video(video_storage_folder, file, is_retry=False, save_to_video_storage_folder=True, preprocess_only=False, skip_preprocess=False):
    global INPUT_DIR
    INPUT_DIR = video_storage_folder

    # 如果不是重试，总是清理输出目录
    if not is_retry:
        prepare_output_folder(OUTPUT_DIR)
        # 重新创建必要的目录
        os.makedirs('output/audio', exist_ok=True)
        os.makedirs('output/log', exist_ok=True)

    # TOS 状态提示（真正的上传/删除由 core/all_whisper_methods/tos_service.py 完成，
    # 该模块是唯一的 TOS 实现；历史上这里曾有一个未接线的 BatchTOSManager，已合并）
    try:
        from core.all_whisper_methods.tos_service import get_tos_service
        tos_service = get_tos_service()
        if tos_service.is_enabled():
            console.print(f"[cyan]🔧 TOS 已启用（自动清理: {tos_service.auto_cleanup}）: 视频 '{file}' 开始处理[/cyan]")
    except Exception as e:
        console.print(f"[yellow]⚠️ TOS 状态检查失败，继续正常处理: {e}[/yellow]")
    
    # 如果跳过预处理，先尝试恢复预处理文件
    if skip_preprocess:
        try:
            restore_preprocessed_files(file)
        except Exception as e:
            console.print(f"[red]恢复预处理文件失败: {str(e)}[/red]")
            console.print("[yellow]将重新执行完整处理流程[/yellow]")
            skip_preprocess = False
    
    # 定义预处理步骤
    preprocess_steps = [
        ("Recording start", record_start),
        ("🎥 Processing input file", partial(process_input_file, file)),
        ("🎙️ Transcribing with Whisper", partial(step2_whisperX.transcribe)),
    ]
    
    # 定义后续处理步骤
    remaining_steps = [
        ("✂️ Splitting sentences", split_sentences),
        ("📝 Summarizing and translating", summarize_and_translate),
        ("⚡ Processing and aligning subtitles", process_and_align_subtitles),
    ]

    # 是否烧录字幕只取决于 preprocess_only 与 "Burn-in Subtitles" 开关。
    # 该开关在侧边栏以 resolution 表达：开启=具体分辨率，关闭='0x0'。
    # 曾经这里的条件是 `not preprocess_only and not skip_preprocess`，导致勾选
    # "优先进行本地计算"（skip_preprocess=True）时被静默跳过烧录，不产出 output_sub.mp4。
    if not preprocess_only and load_key("resolution") != "0x0":
        remaining_steps.append(("🎬 Merging subtitles to video", step7_merge_sub_to_vid.merge_subtitles_to_video))

    # 选择要执行的步骤
    if preprocess_only:
        steps_to_execute = preprocess_steps
    elif skip_preprocess:
        # 如果跳过预处理，需要先复制视频文件和恢复预处理结果
        steps_to_execute = [
            ("Recording start", record_start),
            ("🎥 Copying input file", partial(copy_input_file, file))
        ] + remaining_steps
    else:
        steps_to_execute = preprocess_steps + remaining_steps

    current_step = ""
    for step_name, step_func in steps_to_execute:
        current_step = step_name
        for attempt in range(4):
            try:
                console.print(Panel(
                    f"[bold green]{step_name}[/]",
                    subtitle=f"Attempt {attempt + 1}/4" if attempt > 0 else None,
                    border_style="blue"
                ))
                if attempt > 0:
                    delay = 5 * (3 ** (attempt - 1))
                    time.sleep(delay)
                result = step_func()
                if result is not None:
                    globals().update(result)
                break
            except Exception as e:
                if attempt == 3:
                    error_panel = Panel(
                        f"[bold red]Error in step '{current_step}':[/]\n{str(e)}",
                        border_style="red"
                    )
                    console.print(error_panel)
                    cleanup(ERROR_OUTPUT_DIR)
                    return False, current_step, str(e)
                console.print(Panel(
                    f"[yellow]Attempt {attempt + 1} failed. Retrying...[/]",
                    border_style="yellow"
                ))
    
    console.print(Panel("[bold green]All steps completed successfully! 🎉[/]", border_style="green"))
    
    if not preprocess_only:
        # 更新总token数
        eu.add_to_total_tokens()
        # 注意：总耗时已由 core/step6_generate_final_timeline.record_summary_info()
        # 累加到 eu.total_time_duration，这里**不能**再调 add_to_total_time()，
        # 否则批量统计里的总耗时约为真值的 2 倍（见 devdocs 已知问题 P3-30）。
        eu.add_to_total_cost()

        # 记录当前视频的消耗
        eu.record_messages()
        
        # 保存字幕和清理
        save_subbtitles(save_to_video_storage_folder)
        cleanup(SAVE_DIR)
    
    return True, "", ""

def prepare_output_folder(output_folder):
    if os.path.exists(output_folder):
        shutil.rmtree(output_folder)
    os.makedirs(output_folder)

def process_input_file(file):
    if file.startswith('http'):
        step1_ytdlp.download_video_ytdlp(file, resolution=load_key(YTB_RESOLUTION_KEY), cutoff_time=None)
        video_file = step1_ytdlp.find_video_files()
        eu.original_name = eu.record_file_name(video_file)
    else:
        input_file = os.path.join(INPUT_DIR, file)
        output_file = os.path.join(OUTPUT_DIR, file)
        shutil.copy(input_file, output_file)
        video_file = output_file
        eu.original_name = eu.record_file_name(video_file)
    return {'video_file': video_file}

def copy_input_file(file):
    """仅复制输入文件到输出目录"""
    input_file = os.path.join(INPUT_DIR, file)
    output_file = os.path.join(OUTPUT_DIR, file)
    shutil.copy(input_file, output_file)
    video_file = output_file
    eu.original_name = eu.record_file_name(video_file)
    return {'video_file': video_file}

def split_sentences():
    step3_1_spacy_split.split_by_spacy()
    # step3_2 内部按 config 的 llm_sentence_split 决定是否调 LLM 做断句优化
    step3_2_splitbymeaning.split_sentences_by_meaning()

def summarize_and_translate():
    step4_1_summarize.get_summary()
    step4_2_translate_all.translate_all()

def process_and_align_subtitles():
    step5_splitforsub.split_for_sub_main()
    step6_generate_final_timeline.align_timestamp_main()

def record_start():
    record_start_time()
    reset_tokens()

def record_start_time():
    eu.start_time = time.time()

def reset_tokens():
    eu.prompt_tokens = 0
    eu.completion_tokens = 0
    eu.total_tokens = 0

def save_subbtitles(save_to_video_storage_folder):
    """保存字幕。

    产出三份：
      1. `batch/output/SavedSubbtitles/<video_name>_subtitles.zip`（打包字幕 + 转录文本）
      2. `output/<video_name>.srt` 与 `output/<video_name>.txt`
         → 随后被 `cleanup(SAVE_DIR)` 整体搬进 `batch/output/<video_name>/`
      3. 复制到**输入视频所在目录**（`INPUT_DIR`），作为"该视频已翻译"的判据，
         批量模式下次运行时会据此跳过

    ⚠️ 顺序很重要：`<video_name>.srt` 必须在**做任何 `os.path.isfile` 判断或打包之前**
    就写到 output/ 下。历史上这一步是靠 zip 循环里的 `copy_as_default_subbtitle()`
    顺带完成的；重构时该调用被移除、但"先判断后写"的顺序被保留，导致：
      - 字幕没有出现在 output/ → 归档目录里也就没有
      - 复制到输入目录的 `os.path.isfile` 判断恒为 False → 输入目录拿不到字幕
    """
    console.print("Saving subtitles...")
    subbtitles_save_dir = "batch/output/SavedSubbtitles"
    os.makedirs(subbtitles_save_dir, exist_ok=True)
    zip_buffer = io.BytesIO()
    output_dir = "output"
    log_dir = os.path.join(output_dir, "log")
    video_name = eu.original_name or "video"

    # ① 先把"默认字幕"落盘，文件名与视频同名。这是后续所有复制动作的来源，
    #    必须先于任何存在性判断执行。
    #    内容来源按模式区分：仅转录 → src.srt（只有原语言）；翻译 → trans_src.srt（双语）。
    default_srt_src, reason = pick_default_subtitle(output_dir)
    default_srt = os.path.join(output_dir, video_name + ".srt")
    if default_srt_src:
        shutil.copy(default_srt_src, default_srt)
        console.print(f"[green]✓ 已生成默认字幕: {default_srt}（{reason}）[/green]")
    else:
        console.print(f"[yellow]⚠️ {reason}，无法生成 {video_name}.srt[/yellow]")

    # 转录文本同样先落到 output/，便于归档与复制
    transcript_path = os.path.join(log_dir, "sentence_splitbymeaning.txt")
    transcript_out = os.path.join(output_dir, video_name + ".txt")
    if os.path.isfile(transcript_path):
        shutil.copy(transcript_path, transcript_out)

    # ② 打包 zip（与 st_components.imports_and_utils.download_subtitle_zip_button 同一套命名与去重逻辑）
    entries = {}
    for file_name in sorted(os.listdir(output_dir)):
        file_path = os.path.join(output_dir, file_name)
        if file_name.endswith(".srt") and os.path.isfile(file_path):
            if file_name == f"{video_name}.srt":
                continue  # 默认字幕，下面单独加入，避免重名条目
            entries[subtitle_zip_name(file_name, video_name)] = file_path

    if os.path.isfile(default_srt):
        entries[f"{video_name}.srt"] = default_srt
    if os.path.isfile(transcript_out):
        entries[f"{video_name}.txt"] = transcript_out

    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
        for arcname, path in entries.items():
            zip_file.write(path, arcname)

    # ③ 复制到输入视频所在目录，形成"已翻译"闭环（下次批量运行会据此跳过该视频）
    if save_to_video_storage_folder:
        copied = []
        for src, name in ((default_srt, video_name + ".srt"), (transcript_out, video_name + ".txt")):
            if os.path.isfile(src):
                try:
                    os.makedirs(INPUT_DIR, exist_ok=True)
                    shutil.copy(src, os.path.join(INPUT_DIR, name))
                    copied.append(name)
                except Exception as e:
                    console.print(f"[red]❌ 复制 {name} 到输入目录失败: {e}[/red]")
        if copied:
            console.print(f"[green]✓ 已复制到输入目录 {INPUT_DIR}: {copied}[/green]")
        else:
            console.print(f"[yellow]⚠️ 没有可复制到输入目录 {INPUT_DIR} 的字幕文件[/yellow]")

    zip_buffer.seek(0)
    zip_path = os.path.join(subbtitles_save_dir, video_name + "_subtitles" + ".zip")
    with open(zip_path, 'wb') as f:
        f.write(zip_buffer.read())
    console.print(f"[green]✓ 已保存字幕包: {zip_path}（{len(entries)} 个条目）[/green]")

def required_preprocess_files():
    """预处理缓存所需的文件清单（按 ASR 引擎区分）。

    `for_whisper.mp3` 只在使用 Whisper 引擎时才生成；火山引擎路径不存在该文件。
    若把它列为必需，`prioritize_local` 的缓存恢复会必然失败（见 devdocs 已知问题 R2）。
    """
    files = ['raw.mp3', 'cleaned_chunks.xlsx']
    try:
        engine = load_key('asr_engine')
    except Exception:
        engine = 'whisper'
    if engine != 'volcano':
        files.insert(1, 'for_whisper.mp3')
    return files


PREPROCESS_FILE_MAP = [
    ('raw.mp3', 'output/audio/raw.mp3'),
    ('for_whisper.mp3', 'output/audio/for_whisper.mp3'),
    ('cleaned_chunks.xlsx', 'output/log/cleaned_chunks.xlsx'),
]


def restore_preprocessed_files(file):
    """从临时目录恢复预处理文件"""
    # 获取视频名（不含扩展名）
    video_name = os.path.splitext(os.path.basename(file))[0]
    temp_dir = os.path.join('batch', 'temp_preprocess', video_name)

    console.print(f"[cyan]Restoring preprocessed files from {temp_dir}[/cyan]")

    # 检查临时目录是否存在
    if not os.path.exists(temp_dir):
        raise Exception(f"临时目录不存在: {temp_dir}")

    # 检查所需文件是否都存在
    required_files = required_preprocess_files()
    missing_files = [f for f in required_files if not os.path.exists(os.path.join(temp_dir, f))]
    if missing_files:
        raise Exception(f"缺少预处理文件: {', '.join(missing_files)}")

    # 确保目标目录存在
    os.makedirs('output/audio', exist_ok=True)
    os.makedirs('output/log', exist_ok=True)

    # 恢复文件（只恢复实际存在的；非必需文件缺失不报错）
    restored = []
    try:
        for src_name, dst_path in PREPROCESS_FILE_MAP:
            src_path = os.path.join(temp_dir, src_name)
            if not os.path.exists(src_path):
                continue
            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
            shutil.copy2(src_path, dst_path)
            restored.append(dst_path)
            console.print(f"[green]✓ Restored {src_name} to {dst_path}[/green]")
    except Exception as e:
        raise Exception(f"恢复文件失败: {str(e)}")

    # 验证必需文件已正确恢复且非空
    for _, dst_path in PREPROCESS_FILE_MAP:
        if dst_path not in restored:
            continue
        if os.path.getsize(dst_path) == 0:
            raise Exception(f"文件恢复失败，目标文件为空: {dst_path}")

    console.print("[bold green]✓ All required preprocessed files restored successfully[/bold green]")
    return None

# 添加新的函数用于生成总结报告
def generate_batch_summary():
    """生成批处理总结报告"""
    tokens = eu.get_total_tokens_summary()
    
    summary = (
        "📊 批量处理总结\n"
        f"总耗时: {eu.convert_seconds(eu.total_time_duration)}\n"
        "\n"
        "Token 消耗统计:\n"
        f"├─ Prompt Tokens: {tokens['prompt']:,}\n"
        f"├─ Completion Tokens: {tokens['completion']:,}\n"
        f"└─ Total Tokens: {tokens['total']:,}\n"
        "\n"
        f"总花费: {eu.get_formatted_total_cost()}"
    )
    
    # 保存总结到文件
    os.makedirs("batch/output", exist_ok=True)
    with open("batch/output/batch_summary.txt", "w", encoding="utf-8") as f:
        f.write(summary)
    
    return summary