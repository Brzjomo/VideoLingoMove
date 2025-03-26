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

def process_video(video_storage_folder, file, dubbing=False, is_retry=False, save_to_video_storage_folder=True, preprocess_only=False, skip_preprocess=False):
    global INPUT_DIR
    INPUT_DIR = video_storage_folder

    # 如果不是重试，总是清理输出目录
    if not is_retry:
        prepare_output_folder(OUTPUT_DIR)
        # 重新创建必要的目录
        os.makedirs('output/audio', exist_ok=True)
        os.makedirs('output/log', exist_ok=True)
    
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

    # 如果不是预处理模式，检查是否需要添加字幕烧录步骤
    if not preprocess_only and not skip_preprocess:
        try:
            # 检查分辨率是否为"0x0"来判断是否启用字幕烧录
            if load_key("resolution") != "0x0":
                remaining_steps.append(("🎬 Merging subtitles to video", step7_merge_sub_to_vid.merge_subtitles_to_video))
        except Exception as e:
            console.print(f"[yellow]Warning: {str(e)}. Skipping subtitle burning.[/yellow]")
    
    if dubbing:
        dubbing_steps = [
            ("🔊 Generating audio tasks", gen_audio_tasks),
            ("🎵 Extracting reference audio", step9_extract_refer_audio.extract_refer_audio_main),
            ("🗣️ Generating audio", step10_gen_audio.gen_audio),
            ("🔄 Merging full audio", step11_merge_full_audio.merge_full_audio),
            ("🎞️ Merging dubbing to video", step12_merge_dub_to_vid.merge_video_audio),
        ]
        remaining_steps.extend(dubbing_steps)

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
    step3_2_splitbymeaning.split_sentences_by_meaning()

def summarize_and_translate():
    step4_1_summarize.get_summary()
    step4_2_translate_all.translate_all()

def process_and_align_subtitles():
    step5_splitforsub.split_for_sub_main()
    step6_generate_final_timeline.align_timestamp_main()

def gen_audio_tasks():
    step8_1_gen_audio_task.gen_audio_task_main()
    step8_2_gen_dub_chunks.gen_dub_chunks()

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
    console.print("Saving subtitles...")
    subbtitles_save_dir = "batch/output/SavedSubbtitles"
    os.makedirs(subbtitles_save_dir, exist_ok=True)
    zip_buffer = io.BytesIO()
    output_dir = "output"
    log_dir = os.path.join(output_dir, "log")
    video_name = eu.original_name

    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
        for file_name in os.listdir(output_dir):
            file_path = os.path.join(output_dir, file_name)
            if file_name.endswith(".srt") and os.path.isfile(file_path):
                if "src_trans" in file_name:
                    new_name = video_name + "_src_trans.srt"
                elif "trans_src" in file_name:
                    new_name = video_name + "_trans_src.srt"
                    # 复制一份默认字幕
                    copy_as_default_subbtitle(output_dir, file_name, video_name + ".srt")
                    zip_file.write(file_path, video_name + ".srt")
                elif "src" in file_name:
                    new_name = video_name + "_src.srt"
                elif "trans" in file_name:
                    new_name = video_name + "_trans.srt"
                else:
                    new_name = file_name
                
                zip_file.write(file_path, new_name)

        # 添加log文件夹下的转录文件，用于后续AI总结
        specific_txt_file = "sentence_splitbymeaning.txt"
        specific_txt_path = os.path.join(log_dir, specific_txt_file)
        new_txt_name = video_name + '.txt'
        if os.path.isfile(specific_txt_path):
            zip_file.write(specific_txt_path, new_txt_name)
            shutil.copy(specific_txt_path, os.path.join(output_dir, new_txt_name))

    if save_to_video_storage_folder:
        # 将与video_name同名的srt文件和txt文件复制到视频存储文件夹INPUT_DIR
        if os.path.isfile(os.path.join(output_dir, video_name + ".srt")):
            shutil.copy(os.path.join(output_dir, video_name + ".srt"), os.path.join(INPUT_DIR, video_name + ".srt"))
        if os.path.isfile(os.path.join(output_dir, video_name + '.txt')):
            shutil.copy(os.path.join(output_dir, video_name + '.txt'), os.path.join(INPUT_DIR, video_name + '.txt'))

    zip_buffer.seek(0)
    with open(os.path.join(subbtitles_save_dir, video_name + "_subtitles" + ".zip"), 'wb') as f:
        f.write(zip_buffer.read())

def copy_as_default_subbtitle(folder_path, file_name, file_new_name):
    file_path = os.path.join(folder_path, file_name)
    if os.path.isfile(file_path):
        shutil.copy(file_path, os.path.join(folder_path, file_new_name))
    else:
        print(f"{folder_path} 不存在文件 {file_name}")

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
    required_files = ['raw.mp3', 'for_whisper.mp3', 'cleaned_chunks.xlsx']
    missing_files = [f for f in required_files if not os.path.exists(os.path.join(temp_dir, f))]
    if missing_files:
        raise Exception(f"缺少预处理文件: {', '.join(missing_files)}")
    
    # 确保目标目录存在
    os.makedirs('output/audio', exist_ok=True)
    os.makedirs('output/log', exist_ok=True)
    
    # 恢复文件
    try:
        for src_name, dst_path in [
            ('raw.mp3', 'output/audio/raw.mp3'),
            ('for_whisper.mp3', 'output/audio/for_whisper.mp3'),
            ('cleaned_chunks.xlsx', 'output/log/cleaned_chunks.xlsx')
        ]:
            src_path = os.path.join(temp_dir, src_name)
            dst_dir = os.path.dirname(dst_path)
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src_path, dst_path)
            console.print(f"[green]✓ Restored {src_name} to {dst_path}[/green]")
    except Exception as e:
        raise Exception(f"恢复文件失败: {str(e)}")
    
    # 验证文件是否已正确恢复
    for _, dst_path in [
        ('raw.mp3', 'output/audio/raw.mp3'),
        ('for_whisper.mp3', 'output/audio/for_whisper.mp3'),
        ('cleaned_chunks.xlsx', 'output/log/cleaned_chunks.xlsx')
    ]:
        if not os.path.exists(dst_path):
            raise Exception(f"文件恢复失败，目标文件不存在: {dst_path}")
        if os.path.getsize(dst_path) == 0:
            raise Exception(f"文件恢复失败，目标文件为空: {dst_path}")
    
    console.print("[bold green]✓ All preprocessed files restored successfully[/bold green]")
    return None