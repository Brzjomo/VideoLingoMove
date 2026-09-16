import streamlit as st
import os, sys, time
import easy_util as eu

eu.ensure_utf8_console()

from st_components.imports_and_utils import *
from core.config_utils import load_key
from core.step7_merge_sub_to_vid import STAGE_DONE_MARKER

# SET PATH
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in os.environ['PATH'].split(os.pathsep):
    os.environ['PATH'] += os.pathsep + current_dir
if current_dir not in sys.path:
    sys.path.append(current_dir)

SUB_VIDEO = "output/output_sub.mp4"


def subtitle_stage_finished():
    """字幕阶段是否已完成。

    不能只看 output_sub.mp4：当 resolution=0x0 且输入是真实视频时，我们
    **有意不生成**任何成片（省时间），此时若仍以成片存在为判据，界面会永远
    停在"开始处理字幕"按钮上。因此以 step7 写下的完成标记为准，
    成片存在只是附加条件（用于决定要不要内嵌播放器）。
    """
    return os.path.exists(STAGE_DONE_MARKER)

def text_processing_section():
    # 检查是否只进行转录
    transcription_only = load_key("transcription_only")

    if transcription_only:
        header = "音频转录和生成原语言字幕"
        steps = """
        <p style='font-size: 20px;'>
        此阶段包含以下步骤：
        <p style='font-size: 20px;'>
            1. WhisperX 逐字转录<br>
            2. 使用 NLP 进行句子分割<br>
            3. 生成原语言字幕<br>
            4. 切割和对齐长字幕<br>
            5. 生成时间轴和字幕<br>
            6. 将字幕合并到视频中
        """
    else:
        header = "翻译和生成字幕"
        steps = """
        <p style='font-size: 20px;'>
        此阶段包含以下步骤：
        <p style='font-size: 20px;'>
            1. WhisperX 逐字转录<br>
            2. 使用 NLP 和 LLM 进行句子分割<br>
            3. 总结和多步翻译<br>
            4. 切割和对齐长字幕<br>
            5. 生成时间轴和字幕<br>
            6. 将字幕合并到视频中
        """

    st.header(header)
    with st.container(border=True):
        st.markdown(steps, unsafe_allow_html=True)

        if not subtitle_stage_finished():
            # 两段式流程：pause_before_translate 为真时，先只跑到术语提取，
            # 等用户在页面上确认/编辑 output/log/terminology.json 后再点继续。
            if st.session_state.get("awaiting_terminology_review"):
                st.info("已提取术语，流程暂停中。请编辑 `output/log/terminology.json` 或 "
                        "`custom_terms.xlsx`，确认后点击下方按钮继续翻译。")
                if st.button("✅ 术语已确认，继续翻译", key="resume_translate_button"):
                    st.session_state["awaiting_terminology_review"] = False
                    run_translation_and_subtitles()
                    st.rerun()
                if st.button("↩️ 放弃并重新开始", key="abort_terminology_review"):
                    st.session_state["awaiting_terminology_review"] = False
                    st.rerun()
                return False

            button_text = "开始生成字幕" if transcription_only else "开始处理字幕"
            if st.button(button_text, key="text_processing_button"):
                record_start_time()
                reset_tokens()
                # 返回 True 表示流程在等待人工确认，本次不再继续
                if process_text():
                    st.rerun()
                    return False
                st.rerun()
        else:
            time_duration = read_time_duration()
            success_message = f"原语言字幕生成完成！耗时：{time_duration} " if transcription_only else f"字幕翻译完成！耗时：{time_duration} "
            st.success(success_message)
            # 有真实成片才内嵌播放器；resolution=0x0 时（除纯音频输入外）不产出成片，
            # 这里静默跳过，不再额外提示。
            if os.path.exists(SUB_VIDEO) and load_key("resolution") != "0x0":
                st.video(SUB_VIDEO)
            download_subtitle_zip_button(text="下载所有字幕")

            if st.button("归档到'历史记录'", key="cleanup_in_text_processing"):
                cleanup()
                st.rerun()
            return True

    # 火山引擎二级缓存清理入口：调过火山参数后需要同时清掉
    # output/log/asr_results/，否则会命中旧结果（见 devdocs 已知问题 R5）
    asr_cache_dir = os.path.join("output", "log", "asr_results")
    if os.path.isdir(asr_cache_dir):
        cached = [f for f in os.listdir(asr_cache_dir) if f.endswith('.json')]
        if cached:
            with st.expander(f"🔧 火山 ASR 结果缓存（{len(cached)} 个文件）", expanded=False):
                st.caption(
                    "改变 `volcano_asr` 的参数（模型版本、标点、DDC 等）后，"
                    "这里的历史结果会被优先复用，导致「改了参数没效果」。"
                    "需要重跑时清理本缓存。"
                )
                if st.button("清空 ASR 结果缓存", key="clear_asr_cache_button"):
                    for f in cached:
                        try:
                            os.remove(os.path.join(asr_cache_dir, f))
                        except OSError as e:
                            st.warning(f"删除 {f} 失败: {e}")
                    st.success(f"已清理 {len(cached)} 个缓存文件")
                    st.rerun()

def record_start_time():
    eu.start_time = time.time()

def read_time_duration():
    return eu.convert_seconds(eu.time_duration)

def reset_tokens():
    eu.prompt_tokens = 0
    eu.completion_tokens = 0
    eu.total_tokens = 0

def ensure_terminology_file():
    """确保 output/log/terminology.json 存在（直通模式与暂停点都需要）。"""
    import json
    terminology_file = "output/log/terminology.json"
    os.makedirs(os.path.dirname(terminology_file), exist_ok=True)
    if not os.path.exists(terminology_file):
        with open(terminology_file, 'w', encoding='utf-8') as f:
            json.dump({"topic": "", "terms": []}, f, ensure_ascii=False, indent=4)


def run_translation_and_subtitles():
    """翻译（或直通）→ 字幕切分 → 时间轴 → 压制。"""
    if load_key("transcription_only"):
        with st.spinner("生成原语言字幕中..."):
            step4_2_translate_all.translate_all()
    else:
        with st.spinner("翻译中..."):
            step4_2_translate_all.translate_all()

    with st.spinner("处理和对齐字幕中..."):
        step5_splitforsub.split_for_sub_main()
        step6_generate_final_timeline.align_timestamp_main()
    with st.spinner("将字幕合并到视频中..."):
        step7_merge_sub_to_vid.merge_subtitles_to_video()

    st.success("字幕处理完成！🎉")
    st.balloons()


def process_text():
    """转录 → NLP/LLM 切句 →（可选暂停确认术语）→ 翻译与成片。

    Returns:
        bool: True 表示因 pause_before_translate 停在人工确认点，后续步骤待用户点击继续。
    """
    with st.spinner("使用 Whisper 进行转录中..."):
        step2_whisperX.transcribe()
    with st.spinner("分割长句中..."):
        step3_1_spacy_split.split_by_spacy()
        step3_2_splitbymeaning.split_sentences_by_meaning()

    transcription_only = load_key("transcription_only")

    if transcription_only:
        # 直通模式不需要术语表，但下游会读该文件，这里保证它存在
        ensure_terminology_file()
    else:
        step4_1_summarize.get_summary()
        if load_key("pause_before_translate"):
            # 两段式暂停点：把控制权交回 UI，而不是在脚本线程里 input() 阻塞
            st.session_state["awaiting_terminology_review"] = True
            st.info("术语提取完成，已暂停。请在下方确认后继续翻译。")
            return True

    run_translation_and_subtitles()
    return False

def main():
    st.set_page_config(page_title="VideoLingo", page_icon="docs/logo.svg")
    logo_col, _ = st.columns([1,1])
    with logo_col:
        # 注意：st.image() 在 streamlit 1.38.0 用的是 use_column_width，
        # 不是 use_container_width（后者只适用于 button/dataframe 等控件）。
        # 传错会抛 TypeError: ImageMixin.image() got an unexpected keyword argument。
        # requirements.txt 固定 streamlit==1.38.0；升级到 1.4x 之后再考虑换名。
        st.image("docs/logo.png", use_column_width=True)
    st.markdown(button_style, unsafe_allow_html=True)
    st.markdown("<p style='font-size: 20px; color: #808080;'>你好，欢迎使用 VideoLingo。本项目目前正在建设中。如果遇到任何问题，请随时在 Github 上提问！你也可以访问我们的网站：<a href='https://videolingo.io' target='_blank'>videolingo.io</a></p>", unsafe_allow_html=True)
    # add settings
    with st.sidebar:
        page_setting()
        st.markdown(give_star_button, unsafe_allow_html=True)
    download_video_section()
    text_processing_section()

if __name__ == "__main__":
    main()
