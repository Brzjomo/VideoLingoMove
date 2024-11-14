import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import step1_ytdlp, step2_whisperX, step3_1_spacy_split, step3_2_splitbymeaning, step9_extract_refer_audio
from core import step4_1_summarize, step4_2_translate_all, step5_splitforsub, step6_generate_final_timeline 
from core import step7_merge_sub_to_vid, step8_gen_audio_task, step10_gen_audio, step11_merge_audio_to_vid
from core.onekeycleanup import cleanup  
from core.delete_retry_dubbing import delete_dubbing_files
from core.ask_gpt import ask_gpt
import streamlit as st
import io, zipfile
from st_components.download_video_section import download_video_section
from st_components.sidebar_setting import page_setting

import os
import io
import shutil
import zipfile
import streamlit as st

def download_subtitle_zip_button(text: str):
    zip_buffer = io.BytesIO()
    output_dir = "output"
    log_dir = os.path.join(output_dir, "log")
    video_file_name = get_video_file_without_with_subs(output_dir)
    video_name = replace_underscore_with_space(video_file_name[0].split('.', 1)[0])

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
                    # zip_file.write(file_path, video_name + ".srt")
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
    
    st.download_button(
        label=text,
        data=zip_buffer,
        file_name= video_name + "_subtitles" + ".zip",
        mime="application/zip"
    )

def copy_as_default_subbtitle(folder_path, file_name, file_new_name):
    file_path = os.path.join(folder_path, file_name)
    if os.path.isfile(file_path):
        shutil.copy(file_path, os.path.join(folder_path, file_new_name))
    else:
        print(f"{folder_path} 不存在文件 {file_name}")

def get_video_file_without_with_subs(output_dir):
    video_files = []
    
    # 遍历output文件夹
    for file_name in os.listdir(output_dir):
        file_path = os.path.join(output_dir, file_name)
        
        # 检查文件是否为视频文件且不包含"with_subs"
        if os.path.isfile(file_path) and not "with_subs" in file_name and file_name.endswith(('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm')):
            video_files.append(file_name)
    
    return video_files

def replace_underscore_with_space(input_string):
    return input_string.replace("_", " ")

# st.markdown
give_star_button = """
<style>
    .github-button {
        display: block;
        width: 100%;
        padding: 0.5em 1em;
        color: #144070;
        background-color: #d0e0f2;
        border-radius: 6px;
        text-decoration: none;
        font-weight: bold;
        text-align: center;
        transition: background-color 0.3s ease, color 0.3s ease;
        box-sizing: border-box;
    }
    .github-button:hover {
        background-color: #ffffff;
        color: #144070;
    }
</style>
<a href="https://github.com/Huanshere/VideoLingo" target="_blank" style="text-decoration: none;">
    <div class="github-button">
        Star on GitHub 🌟
    </div>
</a>
"""

button_style = """
<style>
div.stButton > button:first-child {
    display: block;
    padding: 0.5em 1em;
    color: #144070;
    background-color: transparent;
    text-decoration: none;
    font-weight: bold;
    text-align: center;
    transition: all 0.3s ease;
    box-sizing: border-box;
    border: 2px solid #D0DFF2;
    font-size: 1.2em;
}
div.stButton > button:hover {
    background-color: transparent;
    color: #144070;
    border-color: #144070;
}
div.stButton > button:active, div.stButton > button:focus {
    background-color: transparent !important;
    color: #144070 !important;
    border-color: #144070 !important;
    box-shadow: none !important;
}
div.stButton > button:active:hover, div.stButton > button:focus:hover {
    background-color: transparent !important;
    color: #144070 !important;
    border-color: #144070 !important;
    box-shadow: none !important;
}
div.stDownloadButton > button:first-child {
    display: block;
    padding: 0.5em 1em;
    color: #144070;
    background-color: transparent;
    text-decoration: none;
    font-weight: bold;
    text-align: center;
    transition: all 0.3s ease;
    box-sizing: border-box;
    border: 2px solid #D0DFF2;
    font-size: 1.2em;
}
div.stDownloadButton > button:hover {
    background-color: transparent;
    color: #144070;
    border-color: #144070;
}
div.stDownloadButton > button:active, div.stDownloadButton > button:focus {
    background-color: transparent !important;
    color: #144070 !important;
    border-color: #144070 !important;
    box-shadow: none !important;
}
div.stDownloadButton > button:active:hover, div.stDownloadButton > button:focus:hover {
    background-color: transparent !important;
    color: #144070 !important;
    border-color: #144070 !important;
    box-shadow: none !important;
}
</style>
"""