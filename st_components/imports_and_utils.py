import os, sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core import (
    # Download & Transcribe 📥
    step1_ytdlp,
    step2_whisperX,
    
    # Text Processing & Analysis 📝
    step3_1_spacy_split,
    step3_2_splitbymeaning,
    step4_1_summarize,
    step4_2_translate_all,
    step5_splitforsub,
    
    # Subtitle Timeline & Merging 🎬
    step6_generate_final_timeline,
    step7_merge_sub_to_vid,
)
from core.onekeycleanup import cleanup  
from core.ask_gpt import ask_gpt
from core.config_utils import load_key
import streamlit as st
import io, zipfile
import easy_util as eu
from st_components.download_video_section import download_video_section
from st_components.sidebar_setting import page_setting

import os
import io
import shutil
import zipfile
import streamlit as st

def subtitle_zip_name(file_name: str, video_name: str):
    """把 output/ 下的字幕文件名映射为打包后的名字。

    用**完整文件名匹配**而不是子串包含（`"src" in file_name`），
    避免 src_trans / trans_src 这类同时含两个关键字的文件名依赖判断顺序
    （见 devdocs 已知问题 P3-1）。

    该函数被 st_components 与 batch/utils/video_processor 共用，
    所以命名**不带下划线前缀**——`from st_components.imports_and_utils import *`
    不会导入以下划线开头的名字（batch/utils/video_processor.py:3 就是这么用的）。
    """
    stem = os.path.splitext(file_name)[0]
    mapping = {
        'src_trans': f"{video_name}_src_trans.srt",
        'trans_src': f"{video_name}_trans_src.srt",
        'src': f"{video_name}_src.srt",
        'trans': f"{video_name}_trans.srt",
    }
    return mapping.get(stem, file_name)


# 向后兼容别名（旧名字带下划线，仅供显式 import 使用）
_subtitle_zip_name = subtitle_zip_name


def pick_default_subtitle(output_dir: str = "output"):
    """选出「默认字幕」的源文件，返回 (路径, 说明) 或 (None, 原因)。

    选择规则：
      - 仅转录模式（transcription_only）：只有原语言，用 **src.srt**
      - 翻译模式                        ：双语，用 **trans_src.srt**（译文在上、原文在下）

    历史上这里固定用 trans_src.srt，导致"只生成原语言字幕"时默认字幕反而取自
    译文文件（在直通模式下虽然内容相同，但语义错误；若目录里残留了旧的双语字幕，
    还会给出错误的内容）。
    """
    if load_key("transcription_only"):
        p = os.path.join(output_dir, "src.srt")
        if os.path.isfile(p):
            return p, "仅转录模式：使用原语言字幕 src.srt"
        # 兜底：src.srt 缺失时（例如目录里是翻译模式遗留的产物）退回双语字幕
        for name in ("src_trans.srt", "trans_src.srt"):
            p = os.path.join(output_dir, name)
            if os.path.isfile(p):
                return p, f"仅转录模式但缺 src.srt：退回双语字幕 {name}"
        return None, "仅转录模式但未找到 src.srt（也无双语字幕可退回）"
    for name in ("trans_src.srt", "trans.srt"):
        p = os.path.join(output_dir, name)
        if os.path.isfile(p):
            return p, f"翻译模式：使用双语字幕 {name}"
    return None, "未找到 trans_src.srt / trans.srt"


def download_subtitle_zip_button(text: str):
    zip_buffer = io.BytesIO()
    output_dir = "output"
    log_dir = os.path.join(output_dir, "log")
    video_name = eu.original_name or "video"

    # ① 生成"默认字幕" `output/<video_name>.srt`
    default_srt_src, reason = pick_default_subtitle(output_dir)
    default_srt = os.path.join(output_dir, video_name + ".srt")
    if default_srt_src:
        shutil.copy(default_srt_src, default_srt)
    else:
        print(f"{reason}，无法生成 {video_name}.srt")

    # 转录文本也留一份到 output/，方便直接取用
    transcript_path = os.path.join(log_dir, "sentence_splitbymeaning.txt")
    transcript_out = os.path.join(output_dir, video_name + ".txt")
    if os.path.isfile(transcript_path):
        shutil.copy(transcript_path, transcript_out)

    # ② 收集打包清单再去重后写入（避免 zip 出现重名条目，见 P3-38）
    entries = {}   # arcname -> 真实路径
    for file_name in sorted(os.listdir(output_dir)):
        file_path = os.path.join(output_dir, file_name)
        if file_name.endswith(".srt") and os.path.isfile(file_path):
            if file_name == f"{video_name}.srt":
                # 这是默认字幕的副本，下面单独加入，避免与命名映射冲突
                continue
            entries[subtitle_zip_name(file_name, video_name)] = file_path

    if os.path.isfile(default_srt):
        entries[f"{video_name}.srt"] = default_srt
    if os.path.isfile(transcript_out):
        entries[f"{video_name}.txt"] = transcript_out

    with zipfile.ZipFile(zip_buffer, "w") as zip_file:
        for arcname, path in entries.items():
            zip_file.write(path, arcname)

    zip_buffer.seek(0)

    st.download_button(
        label=text,
        data=zip_buffer,
        file_name=video_name + "_subtitles.zip",
        mime="application/zip"
    )


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