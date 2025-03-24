import os
import sys
import streamlit as st
from audio_extractor import AudioExtractor
import subprocess

# 添加项目根目录到系统路径
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

def init_session_state():
    """初始化session state"""
    if 'processing' not in st.session_state:
        st.session_state.processing = False
    if 'extractor' not in st.session_state:
        st.session_state.extractor = None

def reset_extractor():
    """重置提取器实例"""
    st.session_state.extractor = None
    st.session_state.processing = False

def main():
    st.set_page_config(page_title="音频提取工具", layout="wide")
    
    st.title("🎵 音频提取工具")
    
    init_session_state()
    
    # 输入输出目录设置
    st.write("### 📁 选择目录")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # 输入目录
        input_dir = st.text_input(
            "输入目录",
            placeholder="请输入视频文件夹的完整路径",
            help="包含视频文件的文件夹路径",
            key="input_dir",
            on_change=reset_extractor
        )
        
        # 添加示例路径
        st.caption("示例路径格式：")
        if os.name == 'nt':  # Windows
            st.code("C:\\Users\\YourName\\Videos")
        else:  # Linux/Mac
            st.code("/home/username/videos")
    
    with col2:
        # 输出目录
        output_dir = st.text_input(
            "输出目录",
            placeholder="请输入音频输出文件夹的完整路径",
            help="音频文件将保存在此目录中",
            key="output_dir",
            on_change=reset_extractor
        )
    
    # 子目录处理选项
    process_subdirs = st.checkbox(
        "处理子目录",
        help="启用后将递归处理所选文件夹中的所有子目录",
        key="process_subdirs",
        on_change=reset_extractor
    )
    
    # 检查目录
    if not input_dir:
        st.warning("⚠️ 请输入视频文件夹路径")
        return
    
    if not output_dir:
        st.warning("⚠️ 请输入音频输出文件夹路径")
        return
    
    if not os.path.exists(input_dir):
        st.error(f"❌ 输入目录不存在: {input_dir}")
        return
    
    # 创建提取器实例
    if st.session_state.extractor is None:
        st.session_state.extractor = AudioExtractor(input_dir, output_dir)
        st.session_state.extractor.process_subdirs = process_subdirs
    
    # 显示目录信息
    with st.expander("📂 目录信息", expanded=True):
        # 获取视频文件列表
        video_files = []
        if st.session_state.extractor:
            video_files = st.session_state.extractor.get_video_files(input_dir)
            if process_subdirs:
                for root, _, _ in os.walk(input_dir):
                    if root != input_dir:
                        video_files.extend(st.session_state.extractor.get_video_files(root))
        
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.metric("📊 视频文件数量", len(video_files))
        
        with col2:
            if st.button("📂 打开输入目录", use_container_width=True):
                if os.name == 'nt':  # Windows
                    os.startfile(input_dir)
                else:  # Linux/Mac
                    subprocess.run(['xdg-open', input_dir])
        
        with col3:
            if st.button("📂 打开输出目录", use_container_width=True):
                os.makedirs(output_dir, exist_ok=True)
                if os.name == 'nt':  # Windows
                    os.startfile(output_dir)
                else:  # Linux/Mac
                    subprocess.run(['xdg-open', output_dir])
    
    # 显示文件列表
    if video_files:
        with st.expander("🎥 待处理的视频文件", expanded=True):
            for i, (_, rel_path) in enumerate(video_files, 1):
                st.text(f"{i}. {rel_path}")
    else:
        st.warning("⚠️ 未找到支持的视频文件")
        return
    
    # 开始处理按钮
    if st.button("▶️ 开始提取", 
                disabled=st.session_state.processing,
                use_container_width=True):
        st.session_state.processing = True
        
        try:
            with st.spinner("正在提取音频..."):
                success, failed = st.session_state.extractor.process_directory()
            
            if failed == 0:
                st.success(f"✅ 处理完成！成功提取 {success} 个文件的音频")
            else:
                st.warning(f"⚠️ 处理完成，但有部分失败\n"
                          f"成功: {success} 个文件\n"
                          f"失败: {failed} 个文件")
        except Exception as e:
            st.error(f"❌ 处理出错: {str(e)}")
        finally:
            st.session_state.processing = False

if __name__ == "__main__":
    main() 