import os
import sys
import streamlit as st
import pandas as pd
from rich.console import Console
import time
import json
import shutil

# 添加项目根目录到系统路径
current_dir = os.path.dirname(os.path.abspath(__file__))  # utils目录
batch_dir = os.path.dirname(current_dir)  # batch目录
root_dir = os.path.dirname(batch_dir)  # 项目根目录
sys.path.append(root_dir)

from core.config_utils import update_key, load_key
from st_components.imports_and_utils import button_style, ask_gpt
from batch_processor import BatchProcessor
import batch_paths
import easy_util as eu

console = Console()

# 定义全局配置文件路径
CONFIG_PATH = os.path.join(root_dir, 'config.yaml')

# Windows 控制台默认 GBK，emoji 输出会崩，这里统一成 UTF-8
import easy_util as _eu
_eu.ensure_utf8_console()

def check_api():
    """检查 API 连通性（绕过缓存，见 devdocs 已知问题 P1-8）"""
    try:
        resp = ask_gpt("This is a test, response 'message':'success' in json format.",
                      response_json=True, log_title=None, use_cache=False)
        return resp.get('message') == 'success'
    except Exception:
        return False


def default_input_dir():
    """批量模式的默认输入目录：<项目根>/batch/input。

    实现放在 `batch_paths.py`（零依赖，便于单测）；这里保留同名包装是为了
    向后兼容，避免外部代码/文档引用失效。
    """
    return batch_paths.default_input_dir(root_dir)


def ensure_default_input_dir():
    """确保默认输入目录存在，返回 (路径, 是否新建)。

    这个目录在 `.gitignore` 里（`batch/input/`），所以刚克隆/刚搬迁的仓库里
    根本没有它。旧实现只在下游 `video_processor.py` 里 `os.makedirs`，但 GUI 在
    更早的地方就先判断「目录不存在」并 return 了 —— 于是默认路径必然报
    「目录不存在: <项目根>/batch/input」，永远走不到创建那一步。
    界面自己负责把它建出来。
    """
    folder, created, error = batch_paths.ensure_default_input_dir(root_dir)
    if error:
        console.print(f"[yellow]无法创建默认输入目录 {folder}: {error}[/yellow]")
    return folder, created


def archive_previous_batch_output(batch_output_dir):
    """把上一批的 batch/output 移到带时间戳的归档目录，而不是直接删除。

    这样重复点击「开始批量处理」不会丢掉上一次的结果。
    归档目录名形如 `output_archive_20260206_153012`，位于 batch/ 下。
    """
    if not os.path.isdir(batch_output_dir):
        return
    if not os.listdir(batch_output_dir):
        return  # 空目录无需归档
    import time as _time
    stamp = _time.strftime('%Y%m%d_%H%M%S')
    archive_dir = os.path.join(root_dir, 'batch', f'output_archive_{stamp}')
    try:
        os.rename(batch_output_dir, archive_dir)
        print(f"[batch] 上一批产物已归档到 {archive_dir}")
    except OSError:
        # 归档失败（如被占用）时退回删除，保证流程能继续
        shutil.rmtree(batch_output_dir, ignore_errors=True)

def init_session_state():
    if 'processing' not in st.session_state:
        st.session_state.processing = False
    if 'current_progress' not in st.session_state:
        st.session_state.current_progress = 0
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
        
        # 计算进度
        progress = 0
        if eu.is_processing():
            progress = eu.get_progress()
        elif df is not None:
            completed = len([x for x in df['Status'] if x == 'Done' or x == 'Skipped'])
            total = len(df)
            progress = completed / total if total > 0 else 0
        
        # 分别显示进度文本和进度条
        col1, col2 = progress_placeholder.columns([1, 4])
        with col1:
            st.text(f"总进度: {int(progress * 100)}%")
        with col2:
            st.progress(progress)
        
        # 显示任务表格
        if not df.empty:
            # 格式化状态列，使用换行符
            def format_status(x):
                if x == 'Done':
                    return '✅ 完成'
                elif isinstance(x, str) and 'Error' in x:
                    # 使用 split 和 join 来处理换行
                    parts = x.split(' - ')
                    return f'❌ {" ".join(parts)}'
                elif x == 'Processing...':
                    return '⏳ 处理中'
                elif x == 'Skipped':
                    return '⏭️ 跳过'
                elif pd.isna(x):
                    return '🕐 等待处理'
                return x
            
            df['Status'] = df['Status'].apply(format_status)
            
            # 显示带样式的表格，但不设置背景色
            styled_df = df.style.apply(lambda x: ['' for v in x], axis=1)
            
            table_placeholder.dataframe(styled_df, width="stretch")
            
            # 显示完成信息
            if st.session_state.process_complete_info and not st.session_state.processing:
                info = st.session_state.process_complete_info
                st.success(
                    f"✨ 批处理完成  \n"
                    f"总耗时: {info['total_time']}  \n"
                    f"预计总花费: {info['total_cost']}"
                )
                
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
    
    col1, col2 = st.columns([2, 1])
    with col1:
        # 使用radio来选择路径模式
        path_mode = st.radio(
            "选择路径模式",
            ["使用默认路径", "手动输入路径"],
            horizontal=True,
            key="path_mode",
            on_change=reset_processor
        )
    
    with col2:
        # 添加子目录处理选项
        process_subdirs = st.checkbox(
            "处理子目录",
            help="启用后将递归处理所选文件夹中的所有子目录",
            key="process_subdirs",
            on_change=reset_processor
        )
    
    if path_mode == "使用默认路径":
        # 默认目录由界面自己保证存在（见 ensure_default_input_dir 的说明）
        folder_path, created = ensure_default_input_dir()
        st.text_input(
            "默认路径",
            value=folder_path,
            disabled=True,
            key="default_path"
        )
        if created:
            st.info(f"📁 已自动创建默认目录：{folder_path}\n\n"
                    f"把要批量处理的视频/音频放进这个文件夹即可。")
        elif not os.path.isdir(folder_path):
            st.error(f"❌ 无法创建默认目录：{folder_path}\n\n"
                     f"请检查项目目录的写入权限。")
            return
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
    
    if not os.path.isdir(folder_path):
        # 手动输入的路径可能是打错了，不擅自创建；但给一个一键创建的出口
        st.error(f"❌ 目录不存在: {folder_path}")
        if path_mode != "使用默认路径":
            if st.button("📁 创建这个目录", key="create_custom_dir"):
                try:
                    os.makedirs(folder_path, exist_ok=True)
                    st.success(f"已创建：{folder_path}")
                    st.rerun()
                except OSError as e:
                    st.error(f"创建失败：{e}")
        return
        
    # 更新session state中的路径
    if st.session_state.folder_path != folder_path:
        st.session_state.folder_path = folder_path
        reset_processor()
    
    # 在文件夹选择之后，添加处理选项
    st.write("### ⚙️ 处理选项")
    
    col1, col2 = st.columns(2)
    
    with col1:
        # 添加优先本地计算选项
        prioritize_local = st.checkbox(
            "优先进行本地计算",
            help="启用后会先完成所有视频的预处理（文件处理和语音识别），再进行后续处理",
            key="prioritize_local",
            on_change=reset_processor
        )
    
    with col2:
        # 添加时间限制开关，单独一行
        time_limit_enabled = st.checkbox(
            "启用时间限制",
            help="启用后只在指定时间段内处理需要联网的步骤",
            key="time_limit_enabled",
            on_change=reset_processor
        )
    
    # 时间输入框放在一行
    if time_limit_enabled:
        col1, col2 = st.columns(2)
        
        with col1:
            # 开始时间选择
            start_time = st.text_input(
                "开始时间",
                value="00:30",
                help="格式: HH:MM (24小时制)",
                key="start_time",
                on_change=reset_processor
            )
        
        with col2:
            # 结束时间选择
            end_time = st.text_input(
                "结束时间",
                value="08:30",
                help="格式: HH:MM (24小时制)",
                key="end_time",
                on_change=reset_processor
            )
        
        # 验证时间格式
        try:
            # 验证时间格式
            for time_str in [start_time, end_time]:
                hour, minute = map(int, time_str.split(':'))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
        except ValueError:
            st.error("❌ 时间格式错误！请使用24小时制格式（HH:MM），例如：08:30")
            return
    else:
        # 当时间限制未启用时，使用默认值
        start_time = "00:30"
        end_time = "08:30"
    
    # 创建处理器实例
    if st.session_state.processor is None:
        st.session_state.processor = BatchProcessor(folder_path)
        st.session_state.processor.process_subdirs = process_subdirs
        st.session_state.processor.prioritize_local = prioritize_local
        st.session_state.processor.time_limit_enabled = time_limit_enabled
        st.session_state.processor.start_time = start_time
        st.session_state.processor.end_time = end_time
    
    # 显示当前选择的路径
    with st.expander("📂 当前文件夹信息", expanded=True):
        st.info(f"当前使用的文件夹: {folder_path}")
        
        # 显示文件夹统计信息
        video_files = []
        if st.session_state.processor:
            video_files = st.session_state.processor.check_settings()
        
        col1, col2 = st.columns(2)
        with col1:
            st.metric("📊 视频文件数量", len(video_files))
        with col2:
            if st.button("🗂️ 在资源管理器中打开", width="stretch"):
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
        st.warning(
            f"⚠️ 未在选择的文件夹中找到视频文件：{folder_path}\n\n"
            f"把要处理的视频（.mp4/.mkv/.mov/.avi/.flv/.wmv/.webm）或音频"
            f"（.mp3/.wav/.m4a/.flac）放进这个目录即可；已存在同名 `.srt` 的"
            f"文件会被跳过（视为已处理）。\n\n"
            f"可以用上面的「🗂️ 在资源管理器中打开」按钮直接打开该目录。"
        )
        return
    
    # 显示任务状态
    if os.path.exists(st.session_state.processor.tasks_setting_path):
        st.write("### 📊 当前任务状态:")
        
        # 创建固定的占位符
        status_placeholder = st.empty()
        progress_placeholder = st.empty()
        table_placeholder = st.empty()
        
        # 保存占位符和显示函数到session state
        st.session_state.status_text = status_placeholder.empty()
        st.session_state.progress_placeholder = progress_placeholder
        st.session_state.table_placeholder = table_placeholder
        st.session_state.display_task_status_func = display_task_status
        
        # 显示任务状态
        display_task_status(st.session_state.processor.tasks_setting_path, 
                          status_placeholder, progress_placeholder, table_placeholder)
    
    # 操作按钮
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("📝 创建/更新任务配置", 
                    width="stretch",
                    disabled=st.session_state.processing):
            with st.spinner("正在更新任务配置文件..."):
                try:
                    # 更新任务配置
                    st.session_state.processor.create_or_update_tasks()
                    st.success("✅ 任务配置已更新!")
                    # 直接重新运行，不显示临时的表格
                    st.rerun()
                except Exception as e:
                    st.error(f"❌ {str(e)}")
                
    with col2:
        # ⚠️ 这里**不能**写 `on_click=start_processing`（2026-09-20 实测报障）。
        # Streamlit 的 widget 回调在**脚本重跑之前**执行：回调先把 processing 置 True，
        # 于是本次渲染出来的按钮是 `disabled=True` —— 而**禁用按钮的返回值恒为 False**，
        # 下面这个 `if st.button(...)` 分支（唯一调用 process_batch() 的地方）永远进不来。
        # 结果：界面每 0.5 秒 rerun 一次（"一直闪烁 / 反复开始结束"），
        # process_batch() 从未执行，控制台一行输出都没有。
        # 状态改在点击分支内部设置，语义等价且不会自锁。
        if st.button("▶️ 开始批量处理", 
                    disabled=st.session_state.processing, 
                    width="stretch"):
            start_processing()
            try:
                # 把上一批的产物归档而不是直接删除（此前每次点击都会 rmtree，
                # 导致历史结果丢失，见 devdocs 已知问题 P3-34）
                batch_output_dir = os.path.join(root_dir, 'batch', 'output')
                archive_previous_batch_output(batch_output_dir)
                os.makedirs(batch_output_dir, exist_ok=True)
                
                # 修改工作目录到项目根目录
                os.chdir(root_dir)
                
                # 启动处理
                st.session_state.processor.process_batch()
                    
            except Exception as e:
                st.error(f"❌ 处理过程中出现错误: {str(e)}")
            finally:
                st.session_state.processing = False
                st.rerun()
    
    # 如果正在处理，自动刷新
    if st.session_state.processing:
        time.sleep(0.5)
        st.rerun()

if __name__ == "__main__":
    main() 