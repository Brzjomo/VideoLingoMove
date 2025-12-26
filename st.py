import streamlit as st
import os, sys, time
import easy_util as eu
from st_components.imports_and_utils import *
from core.config_utils import load_key

# SET PATH
current_dir = os.path.dirname(os.path.abspath(__file__))
os.environ['PATH'] += os.pathsep + current_dir
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

SUB_VIDEO = "output/output_sub.mp4"
DUB_VIDEO = "output/output_dub.mp4"

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

        if not os.path.exists(SUB_VIDEO):
            button_text = "开始生成字幕" if transcription_only else "开始处理字幕"
            if st.button(button_text, key="text_processing_button"):
                record_start_time()
                reset_tokens()
                process_text()
                st.rerun()
        else:
            time_duration = read_time_duration()
            success_message = f"原语言字幕生成完成！耗时：{time_duration} " if transcription_only else f"字幕翻译完成！耗时：{time_duration} "
            st.success(success_message)
            if load_key("resolution") != "0x0":
                st.video(SUB_VIDEO)
            download_subtitle_zip_button(text="下载所有字幕")

            if st.button("归档到'历史记录'", key="cleanup_in_text_processing"):
                cleanup()
                st.rerun()
            return True

def record_start_time():
    eu.start_time = time.time()

def read_time_duration():
    return eu.convert_seconds(eu.time_duration)

def reset_tokens():
    eu.prompt_tokens = 0
    eu.completion_tokens = 0
    eu.total_tokens = 0

def process_text():
    with st.spinner("使用 Whisper 进行转录中..."):
        step2_whisperX.transcribe()
    with st.spinner("分割长句中..."):
        step3_1_spacy_split.split_by_spacy()
        step3_2_splitbymeaning.split_sentences_by_meaning()

    # 检查是否只进行转录（不翻译）
    transcription_only = load_key("transcription_only")

    if transcription_only:
        with st.spinner("生成原语言字幕中..."):
            # 确保术语文件存在（创建空的）
            import json
            terminology_file = "output/log/terminology.json"
            import os
            os.makedirs(os.path.dirname(terminology_file), exist_ok=True)
            if not os.path.exists(terminology_file):
                with open(terminology_file, 'w', encoding='utf-8') as f:
                    json.dump({"theme": "", "terms": []}, f, ensure_ascii=False, indent=4)

            # 跳过总结和翻译，直接生成原语言字幕文件
            from core.step4_2_translate_all import translate_all
            translate_all()  # 这个函数需要修改以支持直通模式
    else:
        with st.spinner("总结和翻译中..."):
            step4_1_summarize.get_summary()
            if load_key("pause_before_translate"):
                input("⚠️ 翻译前暂停。请前往 `output/log/terminology.json` 编辑术语。完成后按回车继续...")
            step4_2_translate_all.translate_all()

    with st.spinner("处理和对齐字幕中..."):
        step5_splitforsub.split_for_sub_main()
        step6_generate_final_timeline.align_timestamp_main()
    with st.spinner("将字幕合并到视频中..."):
        step7_merge_sub_to_vid.merge_subtitles_to_video()

    st.success("字幕处理完成！🎉")
    st.balloons()

def audio_processing_section():
    st.header("配音")
    with st.container(border=True):
        st.markdown("""
        <p style='font-size: 20px;'>
        此阶段包含以下步骤：
        <p style='font-size: 20px;'>
            1. 生成音频任务和分段<br>
            2. 提取参考音频<br>
            3. 生成和合并音频文件<br>
            4. 将最终音频合并到视频中
        """, unsafe_allow_html=True)
        if not os.path.exists(DUB_VIDEO):
            if st.button("开始处理音频", key="audio_processing_button"):
                process_audio()
                st.rerun()
        else:
            st.success("音频处理完成！你可以在 `output` 文件夹中查看音频文件。")
            if load_key("resolution") != "0x0": 
                st.video(DUB_VIDEO)
            if st.button("删除配音文件", key="delete_dubbing_files"):
                delete_dubbing_files()
                st.rerun()
            if st.button("归档到'历史记录'", key="cleanup_in_audio_processing"):
                cleanup()
                st.rerun()

def process_audio():
    with st.spinner("生成音频任务中"):
        step8_1_gen_audio_task.gen_audio_task_main()
        step8_2_gen_dub_chunks.gen_dub_chunks()
    with st.spinner("提取参考音频中"):
        step9_extract_refer_audio.extract_refer_audio_main()
    with st.spinner("生成所有音频中"):
        step10_gen_audio.gen_audio()
    with st.spinner("合并完整音频中"):
        step11_merge_full_audio.merge_full_audio()
    with st.spinner("将配音合并到视频中"):
        step12_merge_dub_to_vid.merge_video_audio()
    
    st.success("音频处理完成！🎇")
    st.balloons()

def main():
    st.set_page_config(page_title="VideoLingo", page_icon="docs/logo.svg")
    logo_col, _ = st.columns([1,1])
    with logo_col:
        st.image("docs/logo.png", use_column_width=True)
    st.markdown(button_style, unsafe_allow_html=True)
    st.markdown("<p style='font-size: 20px; color: #808080;'>你好，欢迎使用 VideoLingo。本项目目前正在建设中。如果遇到任何问题，请随时在 Github 上提问！你也可以访问我们的网站：<a href='https://videolingo.io' target='_blank'>videolingo.io</a></p>", unsafe_allow_html=True)
    # add settings
    with st.sidebar:
        page_setting()
        st.markdown(give_star_button, unsafe_allow_html=True)
    download_video_section()
    text_processing_section()
    audio_processing_section()

if __name__ == "__main__":
    main()
