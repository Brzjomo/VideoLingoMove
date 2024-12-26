import streamlit as st
import os
import pandas as pd
import sys
from rich.console import Console
import time
import json

# 添加项目根目录到系统路径
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

from core.config_utils import update_key, load_key
from st_components.imports_and_utils import button_style, ask_gpt
from batch_processor import BatchProcessor

console = Console()

# 定义全局配置文件路径
CONFIG_PATH = os.path.join(root_dir, 'config.yaml')

def check_api():
    """检查API状态"""
    try:
        resp = ask_gpt("This is a test, response 'message':'success' in json format.",
                      response_json=True, log_title='None')
        return resp.get('message') == 'success'
    except Exception:
        return False

def init_session_state():
    if 'processing' not in st.session_state:
        st.session_state.processing = False
    if 'folder_path' not in st.session_state:
        st.session_state.folder_path = None
    if 'process_complete_info' not in st.session_state:
        st.session_state.process_complete_info = None
    if 'current_task_info' not in st.session_state:
        st.session_state.current_task_info = None
    if 'processor' not in st.session_state:
        st.session_state.processor = None

def start_processing():
    st.session_state.processing = True
    st.session_state.process_complete_info = None
    st.session_state.current_task_info = None
    
def reset_processor():
    """重置处理器实例"""
    st.session_state.processor = None
    st.session_state.processing = False
    st.session_state.process_complete_info = None
    st.session_state.current_task_info = None

def display_task_status(tasks_setting_path, status_placeholder, progress_placeholder, table_placeholder):
    try:
        # 读取任务详情表格
        df = pd.read_excel(tasks_setting_path)
        
        # 读取状态文件
        status_file_path = os.path.join(os.path.dirname(tasks_setting_path), 'current_status.json')
        current_task_info = None
        
        if os.path.exists(status_file_path):
            try:
                with open(status_file_path, 'r', encoding='utf-8') as f:
                    current_task_info = json.load(f)
            except json.JSONDecodeError:
                pass
        
        # 使用container来包装状态信息
        with status_placeholder.container():
            # 显示总体进度
            if current_task_info:
                progress = current_task_info['task_number'] / current_task_info['total_tasks']
                st.progress(progress, text=f"总进度: {int(progress * 100)}%")
            
            # 显示当前任务信息
            if current_task_info:
                status = current_task_info.get('status', '')
                current_file = current_task_info.get('current_file', '')
                task_number = current_task_info.get('task_number', 0)
                total_tasks = current_task_info.get('total_tasks', 0)
                
                # 创建两列布局
                col1, col2 = st.columns([3, 1])
                
                with col1:
                    if status == 'processing':
                        st.warning(f"🔄 正在处理: {current_file}")
                    elif status == 'Done':
                        st.success(f"✅ 已完成: {current_file}")
                    elif 'Error' in str(status):
                        st.error(f"❌ 处理出错: {current_file}\n{status}")
                    elif status == 'Skipped':
                        st.info(f"⏭️ 已跳过: {current_file}")
                    else:
                        st.info(f"ℹ️ {status}: {current_file}")
                
                with col2:
                    st.metric("处理进度", f"{task_number}/{total_tasks}")
            
            # 显示完成信息
            if st.session_state.process_complete_info and not st.session_state.processing:
                info = st.session_state.process_complete_info
                st.success(
                    f"✨ 批处理完成\n"
                    f"总耗时: {info['total_time']}\n"
                    f"预计总花费: {info['total_cost']}"
                )
        
        # 显示任务表格
        if not df.empty:
            st.write("### 任务详情:")
            # 格式化状态列
            df['Status'] = df['Status'].apply(lambda x: '✅ 完成' if x == 'Done' 
                                            else '❌ ' + x if isinstance(x, str) and 'Error' in x 
                                            else '⏳ 处理中' if x == 'Processing...'
                                            else '⏭️ 跳过' if x == 'Skipped'
                                            else '⌛ 等待处理' if pd.isna(x)
                                            else x)
            
            # 显示带样式的表格
            styled_df = df.style.apply(lambda x: ['background-color: #e6ffe6' if v == '✅ 完成'
                                                else 'background-color: #ffe6e6' if '❌' in str(v)
                                                else 'background-color: #fff3e6' if '⏳' in str(v)
                                                else 'background-color: #e6f3ff' if '⏭️' in str(v)
                                                else '' for v in x], axis=1)
            
            table_placeholder.dataframe(styled_df, use_container_width=True)
    except Exception as e:
        st.error(f"读取任务状态失败: {str(e)}")

def main():
    st.set_page_config(page_title="视频批量处理", layout="wide")
    
    # 添加自定义样式
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"][aria-expanded="true"]{
            min-width: 450px;
        }
        .stProgress > div > div {
            background-color: #4CAF50 !important;
        }
        .stDataFrame {
            font-size: 14px !important;
        }
        .css-1v0mbdj {
            width: 100% !important;
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
            
            # 导入page_setting函数
            from st_components.sidebar_setting import page_setting
            page_setting()
        except Exception as e:
            st.error(f"加载设置失败: {str(e)}")
            return
    
    st.title("视频批量处理工具")
    
    # 简化API状态检查逻辑
    api_key = load_key('api.key')
    if not api_key:
        st.warning("⚠️ 请在左侧设置面板中配置API密钥")
        return
    
    # 检查API状态
    if not check_api():
        st.error("❌ API连接失败，请检查API设置")
        return
    
    st.success("✅ API状态: 正常")
    
    # 文件夹路径选择
    st.write("### 📁 选择视频文件夹")
    
    # 使用radio来选择路径模式
    path_mode = st.radio(
        "选择路径模式",
        ["使用默认路径", "手动输入路径"],
        horizontal=True,
        key="path_mode",
        on_change=reset_processor
    )
    
    if path_mode == "使用默认路径":
        folder_path = os.path.join(root_dir, 'batch', 'input')
        st.text_input(
            "默认路径",
            value=folder_path,
            disabled=True,
            key="default_path"
        )
    else:
        # 手动输入路径
        folder_path = st.text_input(
            "输入视频文件夹路径",
            value=st.session_state.folder_path if st.session_state.folder_path else "",
            placeholder="请输入视频文件夹的完整路径",
            help="输入包含视频文件的文件夹完整路径",
            key="custom_path",
            on_change=reset_processor
        )
        
        # 添加示例路径
        st.caption("示例路径格式：")
        if os.name == 'nt':  # Windows
            st.code("C:\\Users\\YourName\\Videos")
        else:  # Linux/Mac
            st.code("/home/username/videos")
    
    # 检查文件夹是否存在
    if not folder_path:
        st.warning("⚠️ 请输入视频文件夹路径")
        return
    
    if not os.path.exists(folder_path):
        st.error(f"❌ 目录不存在: {folder_path}")
        return
        
    # 更新session state中的路径
    if st.session_state.folder_path != folder_path:
        st.session_state.folder_path = folder_path
        reset_processor()
    
    # 创建处理器实例
    if st.session_state.processor is None:
        st.session_state.processor = BatchProcessor(folder_path)
    
    # 显示当前选择的路径
    with st.expander("📂 当前文件夹信息", expanded=True):
        st.info(f"当前使用的文件夹: {folder_path}")
        
        # 显示文件夹统计信息
        video_files = [f for f in os.listdir(folder_path) 
                      if f.endswith(('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm'))]
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("📊 视频文件数量", len(video_files))
        with col2:
            if st.button("🗂️ 在资源管理器中打开", use_container_width=True):
                import subprocess
                if os.name == 'nt':  # Windows
                    os.startfile(folder_path)
                else:  # Linux/Mac
                    subprocess.run(['xdg-open', folder_path])
    
    # 显示文件夹中的视频文件
    if video_files:
        with st.expander("🎥 发现以下视频文件", expanded=True):
            for i, video in enumerate(video_files, 1):
                st.text(f"{i}. {video}")
    else:
        st.warning("⚠️ 未在选择的文件夹中找到视频文件")
        return
    
    # 操作按钮
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📝 创建/更新任务配置", 
                    use_container_width=True,
                    disabled=st.session_state.processing):
            with st.spinner("正在更新任务配置文件..."):
                try:
                    df = st.session_state.processor.create_or_update_tasks()
                    st.success("✅ 任务配置已更新!")
                    st.write("### 当前任务配置:")
                    st.dataframe(df, use_container_width=True)
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ {str(e)}")
                
    with col2:
        if st.button("▶️ 开始批量处理", 
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
                
                # 修改工作目录到项目根目录
                os.chdir(root_dir)
                
                # 启动处理
                st.session_state.processor.process_batch()
                    
            except Exception as e:
                st.error(f"❌ 处理过程中出现错误: {str(e)}")
            finally:
                st.session_state.processing = False
    
    # 显示任务状态
    if os.path.exists(st.session_state.processor.tasks_setting_path):
        st.write("### 📊 当前任务状态:")
        
        # 创建固定的占位符
        status_placeholder = st.empty()
        progress_placeholder = st.empty()
        table_placeholder = st.empty()
        
        # 显示任务状态
        display_task_status(st.session_state.processor.tasks_setting_path, 
                          status_placeholder, progress_placeholder, table_placeholder)
        
        # 如果正在处理，自动刷新
        if st.session_state.processing:
            time.sleep(0.2)  # 减少刷新间隔以提高响应速度
            st.experimental_rerun()

if __name__ == "__main__":
    main() 