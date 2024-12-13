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

console = Console()

INPUT_DIR = 'batch/input'
OUTPUT_DIR = 'output'
SAVE_DIR = 'batch/output'
ERROR_OUTPUT_DIR = 'batch/output/ERROR'
YTB_RESOLUTION_KEY = "ytb_resolution"

def process_video(file, dubbing=False, is_retry=False):
    if not is_retry:
        prepare_output_folder(OUTPUT_DIR)

    text_steps = [
        ("Recording start", record_start),
        ("🎥 Processing input file", partial(process_input_file, file)),
        ("🎙️ Transcribing with Whisper", partial(step2_whisperX.transcribe)),
        ("✂️ Splitting sentences", split_sentences),
        ("📝 Summarizing and translating", summarize_and_translate),
        ("⚡ Processing and aligning subtitles", process_and_align_subtitles),
        ("🎬 Merging subtitles to video", step7_merge_sub_to_vid.merge_subtitles_to_video),
    ]
    
    if dubbing:
        dubbing_steps = [
            ("🔊 Generating audio tasks", gen_audio_tasks),
            ("🎵 Extracting reference audio", step9_extract_refer_audio.extract_refer_audio_main),
            ("🗣️ Generating audio", step10_gen_audio.gen_audio),
            ("🔄 Merging full audio", step11_merge_full_audio.merge_full_audio),
            ("🎞️ Merging dubbing to video", step12_merge_dub_to_vid.merge_video_audio),
        ]
        text_steps.extend(dubbing_steps)
    
    current_step = ""
    for step_name, step_func in text_steps:
        current_step = step_name
        for attempt in range(3):
            try:
                console.print(Panel(
                    f"[bold green]{step_name}[/]",
                    subtitle=f"Attempt {attempt + 1}/3" if attempt > 0 else None,
                    border_style="blue"
                ))
                result = step_func()
                if result is not None:
                    globals().update(result)
                break
            except Exception as e:
                if attempt == 2:
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
    save_subbtitles()
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
        input_file = os.path.join('batch', 'input', file)
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

def save_subbtitles():
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

    zip_buffer.seek(0)
    with open(os.path.join(subbtitles_save_dir, video_name + "_subtitles" + ".zip"), 'wb') as f:
        f.write(zip_buffer.read())

def copy_as_default_subbtitle(folder_path, file_name, file_new_name):
    file_path = os.path.join(folder_path, file_name)
    if os.path.isfile(file_path):
        shutil.copy(file_path, os.path.join(folder_path, file_new_name))
    else:
        print(f"{folder_path} 不存在文件 {file_name}")