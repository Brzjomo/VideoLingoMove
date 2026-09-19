import streamlit as st
import os, sys, shutil
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config_utils import load_key
from core.step1_ytdlp import download_video_ytdlp, find_video_files
from time import sleep
import re
import subprocess
import easy_util as eu

# 已处理过的上传标识（文件名:大小），用于避免重复导入同一个文件
UPLOAD_ID_KEY = "_processed_upload_id"


def download_video_section():
    st.header("下载或上传视频")
    with st.container(border=True):
        # 只有"确实还没有素材"（FileNotFoundError）才算正常状态。
        # 旧实现用裸 except 吞掉一切：目录里有多个视频、权限不足、output 不可写
        # 等情况都会静默显示成"还没上传"，用户完全看不出真正的原因。
        try:
            video_file = find_video_files()
        except FileNotFoundError:
            video_file = None
        except Exception as e:
            st.error(f"检测已有素材时出错：{type(e).__name__}: {e}")
            if st.button("清空 output 并重新选择", key="clear_output_on_error"):
                shutil.rmtree("output", ignore_errors=True)
                st.session_state.pop(UPLOAD_ID_KEY, None)
                sleep(1)
                st.rerun()
            return False

        if video_file:
            st.video(video_file)
            eu.original_name = eu.record_file_name(video_file)
            if st.button("删除并重新选择", key="delete_video_button"):
                os.remove(video_file)
                st.session_state.pop(UPLOAD_ID_KEY, None)
                if os.path.exists("output"):
                    shutil.rmtree("output")
                sleep(1)
                st.rerun()
            return True

        col1, col2 = st.columns([3, 1])
        with col1:
            url = st.text_input("输入YouTube链接:")
        with col2:
            res_dict = {
                "360p": "360",
                "1080p": "1080",
                "最佳": "best"
            }
            target_res = load_key("ytb_resolution")
            res_options = list(res_dict.keys())
            default_idx = list(res_dict.values()).index(target_res) if target_res in res_dict.values() else 0
            res_display = st.selectbox("分辨率", options=res_options, index=default_idx)
            res = res_dict[res_display]
        if st.button("下载视频", key="download_button", use_container_width=True):
            if url:
                with st.spinner("正在下载视频..."):
                    download_video_ytdlp(url, resolution=res)
                st.rerun()

        uploaded_file = st.file_uploader("或上传视频", type=load_key("allowed_video_formats") + load_key("allowed_audio_formats"))
        if uploaded_file:
            # 幂等保护：Streamlit 每次 rerun 都会重新给出同一个 uploader 值，
            # 不做去重就会反复清空 output/ 并重写文件（旧的"音频重复上传/重复转录"
            # 问题正是由此而来）。
            upload_id = f"{uploaded_file.name}:{uploaded_file.size}"
            if st.session_state.get(UPLOAD_ID_KEY) == upload_id:
                st.info("该文件已导入过。如需重新导入，请先在上方删除，或换一个文件。")
                return False

            #删除output文件夹中的文件
            if os.path.exists("output"):
                shutil.rmtree("output")
            os.makedirs("output", exist_ok=True)

            raw_name = uploaded_file.name.replace(' ', '_')
            name, ext = os.path.splitext(raw_name)
            clean_name = re.sub(r'[^\w\-_\.]', '', name) + ext.lower()

            target_path = os.path.join("output", clean_name)
            with open(target_path, "wb") as f:
                f.write(uploaded_file.getbuffer())

            # 如果是音频文件则转换为视频
            if clean_name.split('.')[-1] in load_key("allowed_audio_formats"):
                convert_audio_to_video(target_path)
            # 只有真正落盘成功才记录标识
            st.session_state[UPLOAD_ID_KEY] = upload_id
            st.rerun()
        else:
            return False

def convert_audio_to_video(audio_file: str) -> str:
    output_video = 'output/black_screen.mp4'
    if not os.path.exists(output_video):
        print(f"🎵➡️🎬 正在使用FFmpeg将音频转换为视频......")
        ffmpeg_cmd = ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=640x360', '-i', audio_file, '-shortest', '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', output_video]
        subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True, encoding='utf-8')
        print(f"🎵➡️🎬 已将 <{audio_file}> 转换为 <{output_video}>\n")
        # delete audio file
        os.remove(audio_file)
    return output_video
