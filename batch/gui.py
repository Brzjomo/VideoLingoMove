import streamlit as st
import os
import pandas as pd
import sys
from rich.console import Console

# 添加项目根目录到系统路径
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)  # 获取batch目录的上级目录
sys.path.append(root_dir)

# 修正导入路径，使用绝对导入
from utils.batch_processor import process_batch, check_api
from utils.add_videos_to_doc import add_all_videos_to_doc
from core.config_utils import update_key, load_key
import yaml
from st_components.sidebar_setting import page_setting, check_api
from st_components.imports_and_utils import button_style

console = Console()

# 定义全局配置文件路径
CONFIG_PATH = os.path.join(root_dir, 'config.yaml')

def init_session_state():
    if 'processing' not in st.session_state:
        st.session_state.processing = False
    if 'folder_path' not in st.session_state:
        st.session_state.folder_path = os.path.join(root_dir, 'batch', 'input')
    if 'process_complete_info' not in st.session_state:
        st.session_state.process_complete_info = None

def start_processing():
    st.session_state.processing = True
    st.session_state.process_complete_info = None

def main():
    st.set_page_config(page_title="视频批量处理", layout="wide")
    
    # 添加自定义样式来设置侧边栏宽度
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"][aria-expanded="true"]{
            min-width: 450px;
            # max-width: 450px;
        }
        </style>
        """,
        unsafe_allow_html=True
    )
    
    st.markdown(button_style, unsafe_allow_html=True)
    
    init_session_state()
    
    # 在侧边栏显示设置
    with st.sidebar:
        st.title("设置")
        try:
            # 修改core.config_utils中的CONFIG_PATH
            import core.config_utils
            core.config_utils.CONFIG_PATH = CONFIG_PATH
            page_setting()
        except Exception as e:
            st.error(f"加载设置失败: {str(e)}")
            return
    
    st.title("视频批量处理工具")
    
    # 简化API状态检查逻辑
    api_key = load_key('api.key')
    if not api_key:
        st.warning("请在左侧设置面板中配置API密钥")
        return
    
    # 检查API状态
    if 'api_status' not in st.session_state:
        st.session_state.api_status = check_api()
    
    if not st.session_state.api_status:
        st.error("API连接失败，请检查API设置")
        # 添加重试按钮
        if st.button("重新检查API连接"):
            st.session_state.api_status = check_api()
            st.rerun()
        return
    
    # 每次加载页面都检查一次API状态
    current_api_status = check_api()
    if not current_api_status:
        st.error("API连接失败，请检查API设置")
        st.session_state.api_status = False
        st.rerun()
        return
    
    st.success("API状态: 正常")
    
    # 文件夹路径选择
    st.write("### 选择视频文件夹")
    
    # 使用radio来选择路径模式
    path_mode = st.radio(
        "选择路径模式",
        ["使用默认路径", "手动输入路径"],
        horizontal=True
    )
    
    if path_mode == "使用默认路径":
        folder_path = os.path.join(root_dir, 'batch', 'input')
        st.text_input(
            "默认路径",
            value=folder_path,
            disabled=True
        )
        
        # 检查并创建默认文件夹
        if not os.path.exists(folder_path):
            try:
                os.makedirs(folder_path)
                st.info(f"已创建默认文件夹: {folder_path}")
            except Exception as e:
                st.error(f"创建默认文件夹失败: {str(e)}")
                return
    else:
        # 手动输入路径
        folder_path = st.text_input(
            "输入视频文件夹路径",
            value=st.session_state.folder_path,
            placeholder="请输入视频文件夹的完整路径",
            help="输入包含视频文件的文件夹完整路径"
        )
        
        # 添加示例路径
        st.caption("示例路径格式：")
        if os.name == 'nt':  # Windows
            st.code("C:\\Users\\YourName\\Videos")
        else:  # Linux/Mac
            st.code("/home/username/videos")
    
    # 检查文件夹是否存在
    if not os.path.exists(folder_path):
        st.error(f"目录不存在: {folder_path}")
        return
        
    st.session_state.folder_path = folder_path
    
    # 显示当前选择的路径
    with st.expander("当前文件夹信息", expanded=True):
        st.info(f"当前使用的文件夹: {folder_path}")
        
        # 显示文件夹统计信息
        video_files = [f for f in os.listdir(folder_path) 
                      if f.endswith(('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm'))]
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("视频文件数量", len(video_files))
        with col2:
            if st.button("在资源管理器中打开", use_container_width=True):
                import subprocess
                if os.name == 'nt':  # Windows
                    os.startfile(folder_path)
                else:  # Linux/Mac
                    subprocess.run(['xdg-open', folder_path])
    
    # 显示文件夹中的视频文件
    if video_files:
        with st.expander("发现以下视频文件", expanded=True):
            for video in video_files:
                st.text(f"• {video}")
    else:
        st.warning("未在选择的文件夹中找到视频文件")
        return
    
    # 操作按钮
    col1, col2 = st.columns(2)
    
    # 定义任务配置文件路径
    tasks_setting_path = os.path.join(root_dir, 'batch', 'tasks_setting.xlsx')
    
    with col1:
        if st.button("创建/更新任务配置", 
                    use_container_width=True,
                    disabled=st.session_state.processing):  # 直接使用processing状态
            with st.spinner("正在更新任务配置文件..."):
                # 确保batch目录存在
                batch_dir = os.path.join(root_dir, 'batch')
                os.makedirs(batch_dir, exist_ok=True)
                
                # 确保模板文件存在
                template_path = os.path.join(batch_dir, 'tasks_setting-template.xlsx')
                if not os.path.exists(template_path):
                    # 创建一个新的模板文件
                    df_template = pd.DataFrame(columns=['Video File', 'Source Language', 'Target Language', 'Dubbing'])
                    df_template.to_excel(template_path, index=False)
                
                # 使用完整路径调用函数
                df = add_all_videos_to_doc(folder_path)
                if df is not None:
                    st.success("任务配置已更新!")
                    st.write("### 当前任务配置:")
                    st.dataframe(df)
                    st.rerun()
                
    with col2:
        if st.button("开始批量处理", 
                    disabled=st.session_state.processing, 
                    use_container_width=True,
                    on_click=start_processing):
            try:
                # 清理并重建output目录
                batch_output_dir = os.path.join(root_dir, 'batch', 'output')
                if os.path.exists(batch_output_dir):
                    import shutil
                    shutil.rmtree(batch_output_dir)
                os.makedirs(batch_output_dir)
                
                with st.spinner("正在处理视频..."):
                    # 修改工作目录到项目根目录
                    os.chdir(root_dir)
                    process_batch(folder_path)
                
                st.rerun()
                    
            except Exception as e:
                st.error(f"处理过程中出现错误: {str(e)}")
            finally:
                st.session_state.processing = False

    # 显示当前任务状态（如果文件存在）
    if os.path.exists(tasks_setting_path):
        st.write("### 当前任务状态:")
        
        # 显示处理完成信息（如果存在）
        if st.session_state.process_complete_info:
            info = st.session_state.process_complete_info
            st.success(f"""
            所有任务处理完成!\n
            总耗时: {info['total_time']}\n
            预计总花费: {info['total_cost']}
            """)
        
        # 显示任务详情表格
        df = pd.read_excel(tasks_setting_path)
        with st.expander("任务详情", expanded=True):
            st.dataframe(df)

if __name__ == "__main__":
    main() 