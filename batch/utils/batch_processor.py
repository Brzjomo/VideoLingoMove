import os
import sys
import gc
import pandas as pd
from rich.console import Console
from rich.panel import Panel
import time
import shutil
import json
from threading import Lock
import streamlit as st
import subprocess
import datetime

# 添加项目根目录到系统路径
current_dir = os.path.dirname(os.path.abspath(__file__))  # utils目录
batch_dir = os.path.dirname(current_dir)  # batch目录
root_dir = os.path.dirname(batch_dir)  # 项目根目录
sys.path.append(root_dir)
# 同目录模块（video_processor）需要 utils 目录本身在 path 上。
# 此前依赖"streamlit run """batch\\utils\\gui.py""" 会把脚本目录加入 path"这一隐式行为，
# 导致 `import batch.utils.batch_processor` 直接失败（ModuleNotFoundError: video_processor）。
sys.path.append(current_dir)

from core.config_utils import load_key, update_key
from st_components.imports_and_utils import ask_gpt
try:
    from batch.utils.video_processor import process_video, generate_batch_summary
except ImportError:
    # 以脚本方式加载（gui.py 直接运行）时的回退
    from video_processor import process_video, generate_batch_summary
import easy_util as eu

console = Console()
status_lock = Lock()

class BatchProcessor:
    def __init__(self, folder_path):
        # 规范化路径
        self.folder_path = os.path.abspath(folder_path)
        
        # 获取项目根目录
        self.root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.batch_dir = os.path.join(self.root_dir, 'batch')
        
        # 设置配置文件路径
        self.tasks_setting_path = os.path.join(self.batch_dir, 'tasks_setting.xlsx')
        self.template_path = os.path.join(self.batch_dir, 'tasks_setting-template.xlsx')
        
        # 初始化状态
        self.total_tasks = 0
        self.completed_tasks = 0
        eu.total_time_duration = 0
        eu.estimated_total_cost = 0
        
        # 确保目录存在
        os.makedirs(self.folder_path, exist_ok=True)
        os.makedirs(self.batch_dir, exist_ok=True)
        
        # 添加子目录处理标志
        self.process_subdirs = False
        
        # 添加时间限制相关设置
        self.time_limit_enabled = False
        self.start_time = "00:30"
        self.end_time = "08:30"
        
        # 添加优先本地计算标志
        self.prioritize_local = False
        
        # 添加预处理状态跟踪
        self.preprocessed_files = set()
        
        # 添加临时存储目录
        self.temp_dir = os.path.join(self.batch_dir, 'temp_preprocess')
        os.makedirs(self.temp_dir, exist_ok=True)

    def convert_audio_to_video(self, input_audio: str, output_video: str):
        if not os.path.exists(output_video):
            print(f"🎵➡️🎬 正在使用FFmpeg将音频转换为视频......")
            ffmpeg_cmd = ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=640x360', '-i', input_audio, '-shortest', '-c:v', 'libx264', '-c:a', 'aac', '-pix_fmt', 'yuv420p', output_video]
            subprocess.run(ffmpeg_cmd, check=True, capture_output=True, text=True, encoding='utf-8')
            print(f"🎵➡️🎬 已将 <{input_audio}> 转换为 <{output_video}>\n")
            # delete input_audio file
            os.remove(input_audio)
    
    def get_video_files(self, directory):
        """获取指定目录下的视频文件"""
        video_files = []
        for file in os.listdir(directory):
            full_path = os.path.join(directory, file)
            if os.path.isfile(full_path) and file.endswith(('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm')):
                srt_file = os.path.splitext(os.path.basename(file))[0] + '.srt'
                if srt_file not in os.listdir(directory):
                    # 返回相对于主文件夹的路径
                    rel_path = os.path.relpath(full_path, self.folder_path)
                    video_files.append(rel_path)
            elif  os.path.isfile(full_path) and file.endswith(('.wav', '.mp3', '.flac', '.m4a')):
                base_name = os.path.splitext(os.path.basename(file))[0]
                srt_file = base_name + '.srt'
                if srt_file not in os.listdir(directory):
                    input_file = f"{full_path}"
                    output_video = f"{base_name}.mp4"
                    full_path = os.path.join(directory, output_video)
                    self.convert_audio_to_video(input_file, full_path)
                    # 返回相对于主文件夹的路径
                    rel_path = os.path.relpath(full_path, self.folder_path)
                    video_files.append(rel_path)
        return video_files

    def check_settings(self):
        """检查设置和环境"""
        try:
            video_files = []
            
            # 处理主目录
            video_files.extend(self.get_video_files(self.folder_path))
            
            # 如果启用了子目录处理，递归处理子目录
            if self.process_subdirs:
                for root, dirs, _ in os.walk(self.folder_path):
                    if root != self.folder_path:  # 跳过主目录，因为已经处理过了
                        video_files.extend(self.get_video_files(root))
            
            return video_files
        except Exception as e:
            console.print(Panel(f"设置检查失败: {str(e)}", title="错误", style="bold red"))
            return []
    
    def create_or_update_tasks(self):
        """创建或更新任务配置"""
        try:
            # 检查并创建模板文件
            if not os.path.exists(self.template_path):
                df_template = pd.DataFrame(columns=['Video File', 'Source Language', 'Target Language', 'Status'])
                df_template.to_excel(self.template_path, index=False)
            
            # 删除旧的任务文件
            if os.path.exists(self.tasks_setting_path):
                os.remove(self.tasks_setting_path)
            
            # 复制模板
            shutil.copy2(self.template_path, self.tasks_setting_path)
            
            # 读取任务配置
            df = pd.read_excel(self.tasks_setting_path)
            
            # 获取视频文件列表
            video_files = self.check_settings()
            
            # 添加任务
            for video in video_files:
                source_language = load_key("whisper.language")
                target_language = load_key("target_language")
                new_row = pd.DataFrame({
                    'Video File': [video],
                    'Source Language': [source_language],
                    'Target Language': [target_language],
                    'Status': [None]
                })
                df = pd.concat([df, new_row], ignore_index=True)
            
            # 保存配置
            df.to_excel(self.tasks_setting_path, index=False)
            return df
            
        except Exception as e:
            console.print(Panel(f"创建任务配置失败: {str(e)}", title="错误", style="bold red"))
            raise
    
    def is_time_in_range(self):
        """检查当前时间是否在指定时间段内"""
        if not self.time_limit_enabled:
            return True
            
        now = datetime.datetime.now()
        start_hour, start_minute = map(int, self.start_time.split(':'))
        end_hour, end_minute = map(int, self.end_time.split(':'))
        
        start_time = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
        end_time = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)
        
        # 处理跨天的情况
        if end_time < start_time:
            end_time = end_time + datetime.timedelta(days=1)
            if now < start_time:
                now = now + datetime.timedelta(days=1)
        
        return start_time <= now <= end_time

    def wait_until_time_in_range(self):
        """等待直到当前时间进入指定时间段"""
        while not self.is_time_in_range():
            print(f"Waiting... Current time is outside the allowed range ({self.start_time}-{self.end_time})")
            time.sleep(60)  # 每分钟检查一次

    def get_temp_dir_for_video(self, video_file: str) -> str:
        """获取视频文件的临时目录"""
        # 使用视频文件名（不含扩展名）作为临时目录名
        video_name = os.path.splitext(os.path.basename(video_file))[0]
        temp_dir = os.path.join(self.temp_dir, video_name)
        os.makedirs(temp_dir, exist_ok=True)
        return temp_dir
    
    def get_required_preprocess_files(self) -> list:
        """预处理缓存恢复所需的文件（按 ASR 引擎区分）。

        关键点：`output/audio/for_whisper.mp3` **只在使用 Whisper 引擎时才会生成**
        （只有 core/step2_whisperX.py 的 whisper 分支会调 compress_audio）。
        当 `asr_engine` 为 `volcano` 时该文件不存在，如果仍把它列进必需清单，
        「优先进行本地计算」(prioritize_local) 的缓存恢复会**必然失败**并白跑一遍预处理
        （见 devdocs 已知问题 R2）。
        """
        files = ['raw.mp3', 'cleaned_chunks.xlsx']
        try:
            engine = load_key('asr_engine')
        except Exception:
            engine = 'whisper'
        if engine != 'volcano':
            files.insert(1, 'for_whisper.mp3')
        return files

    def save_preprocess_results(self, video_file: str) -> None:
        """保存预处理结果到临时目录"""
        temp_dir = self.get_temp_dir_for_video(video_file)

        # 需要保存的文件列表（for_whisper.mp3 可能存在也可能不存在，存在就一起存）
        files_to_save = [
            ('output/audio/raw.mp3', 'raw.mp3'),
            ('output/audio/for_whisper.mp3', 'for_whisper.mp3'),
            ('output/log/cleaned_chunks.xlsx', 'cleaned_chunks.xlsx')
        ]

        # 复制文件到临时目录
        for src_path, dst_name in files_to_save:
            if os.path.exists(src_path):
                shutil.copy2(src_path, os.path.join(temp_dir, dst_name))

    def restore_preprocess_results(self, video_file: str) -> bool:
        """从临时目录恢复预处理结果"""
        temp_dir = self.get_temp_dir_for_video(video_file)

        # 检查所需文件是否都存在（按当前 ASR 引擎决定是否要求 for_whisper.mp3）
        required_files = self.get_required_preprocess_files()
        missing = [f for f in required_files if not os.path.exists(os.path.join(temp_dir, f))]
        if missing:
            console.print(f"[yellow]预处理缓存缺少文件 {missing}，将重新执行预处理[/yellow]")
            return False

        # 确保目标目录存在
        os.makedirs('output/audio', exist_ok=True)
        os.makedirs('output/log', exist_ok=True)

        # 恢复文件（只恢复实际存在的，不做强制要求）
        try:
            for src_name, dst_path in [
                ('raw.mp3', 'output/audio/raw.mp3'),
                ('for_whisper.mp3', 'output/audio/for_whisper.mp3'),
                ('cleaned_chunks.xlsx', 'output/log/cleaned_chunks.xlsx')
            ]:
                src_path = os.path.join(temp_dir, src_name)
                if os.path.exists(src_path):
                    shutil.copy2(src_path, dst_path)
            return True
        except Exception as e:
            console.print(f"[red]恢复预处理文件失败: {str(e)}[/red]")
            return False
    
    def cleanup_temp_files(self, video_file: str = None):
        """清理临时文件"""
        if video_file:
            # 清理特定视频的临时文件
            temp_dir = self.get_temp_dir_for_video(video_file)
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
        else:
            # 清理所有临时文件
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
            os.makedirs(self.temp_dir, exist_ok=True)
    
    def process_single_video(self, video_file, source_lang, target_lang, is_retry=False, skip_preprocess=False):
        """处理单个视频"""
        if self.time_limit_enabled and not self.is_time_in_range():
            print(f"Paused: Current time is outside the allowed range ({self.start_time}-{self.end_time})")
            self.wait_until_time_in_range()
        
        # 保存原始语言设置
        original_source_lang = load_key('whisper.language')
        original_target_lang = load_key('target_language')
        
        try:
            # 更新语言设置
            if source_lang and not pd.isna(source_lang):
                update_key('whisper.language', source_lang)
            if target_lang and not pd.isna(target_lang):
                update_key('target_language', target_lang)
            
            # 获取视频文件的完整路径
            video_full_path = os.path.join(self.folder_path, video_file)
            video_dir = os.path.dirname(video_full_path)
            video_filename = os.path.basename(video_full_path)
            
            # 如果跳过预处理，先恢复预处理结果
            if skip_preprocess:
                if not self.restore_preprocess_results(video_file):
                    console.print(f"[red]无法恢复预处理结果，将重新执行预处理步骤[/red]")
                    skip_preprocess = False
            
            # 处理视频
            status, error_step, error_message = process_video(
                video_dir, video_filename, is_retry,
                save_to_video_storage_folder=True,
                skip_preprocess=skip_preprocess
            )
            
            # 清理临时文件
            if status:
                self.cleanup_temp_files(video_file)
            
            return "Done" if status else f"Error: {error_step} - {error_message}"
        except Exception as e:
            return f"Error: Unhandled exception - {str(e)}"
        finally:
            # 恢复原始语言设置
            update_key('whisper.language', original_source_lang)
            update_key('target_language', original_target_lang)
    
    def preprocess_video(self, video_file: str) -> bool:
        """预处理单个视频（仅执行本地计算部分）"""
        try:
            # 获取视频文件的完整路径
            video_full_path = os.path.join(self.folder_path, video_file)
            video_dir = os.path.dirname(video_full_path)
            video_filename = os.path.basename(video_full_path)
            
            # 执行预处理步骤
            status, error_step, error_message = process_video(
                video_dir, 
                video_filename, 
                is_retry=False,
                preprocess_only=True
            )
            
            if status:
                # 保存预处理结果
                self.save_preprocess_results(video_file)
                self.preprocessed_files.add(video_file)
                print(f"preprocessed_files: {video_file}");
                return True
            return False
            
        except Exception as e:
            console.print(f"[red]预处理出错: {str(e)}[/red]")
            return False
    
    def process_batch(self):
        """批量处理视频"""
        # 提前绑定，避免在赋值前抛异常时 except 分支引用它导致 NameError
        # （覆盖真实错误信息，见 devdocs 已知问题 R3）
        status_text = None
        try:
            # 重置总计数据
            eu.reset_total_statistics()
            
            # 设置处理状态
            eu.set_processing(True)
            
            # 检查API
            if not check_api():
                console.print(Panel("API Key is not set. Please set API Key First.", 
                                  title="[bold red]Error", expand=True))
                return False
            
            # 检查设置
            self.check_settings()
            
            # 读取任务配置
            df = pd.read_excel(self.tasks_setting_path)
            self.total_tasks = len(df)
            self.completed_tasks = 0
            
            # 获取状态占位符和显示函数
            status_text = st.session_state.get('status_text')
            progress_placeholder = st.session_state.get('progress_placeholder')
            table_placeholder = st.session_state.get('table_placeholder')
            display_task_status_func = st.session_state.get('display_task_status_func')
            
            if not all([status_text, progress_placeholder, table_placeholder, display_task_status_func]):
                return False
            
            # 清理所有临时文件
            self.cleanup_temp_files()
            
            # 如果启用了优先本地计算，先进行预处理
            if self.prioritize_local:
                console.print(Panel("开始预处理阶段...", style="bold cyan"))
                for index, row in df.iterrows():
                    if pd.isna(row['Status']) or 'Error' in str(row['Status']):
                        video_file = row['Video File']
                        
                        # 更新状态文本
                        status_text.text(f"🔄 正在预处理: {video_file}")
                        
                        # 预处理视频
                        if self.preprocess_video(video_file):
                            df.at[index, 'Status'] = 'Preprocessed'
                        else:
                            df.at[index, 'Status'] = 'Preprocess Failed'
                        
                        # 更新Excel和显示
                        df.to_excel(self.tasks_setting_path, index=False)
                        display_task_status_func(self.tasks_setting_path, 
                                              status_text, 
                                              progress_placeholder, 
                                              table_placeholder)
            
            # 处理每个视频
            for index, row in df.iterrows():
                # 更新进度
                eu.set_progress(self.completed_tasks / self.total_tasks)
                
                if pd.isna(row['Status']) or 'Error' in str(row['Status']) or row['Status'] == 'Preprocessed':
                    video_file = row['Video File']
                    
                    # 如果启用了时间限制且不在允许时间范围内
                    if self.time_limit_enabled and not self.is_time_in_range():
                        status_text.warning(f"⏸️ 等待工作时间: {video_file}")
                        self.wait_until_time_in_range()
                    
                    # 更新状态文本
                    status_text.text(f"🔄 正在处理: {video_file}")
                    
                    # 更新Excel状态
                    df.at[index, 'Status'] = 'Processing...'
                    df.to_excel(self.tasks_setting_path, index=False)
                    display_task_status_func(self.tasks_setting_path, 
                                          status_text, 
                                          progress_placeholder, 
                                          table_placeholder)
                    
                    # 处理视频
                    status_msg = self.process_single_video(
                        video_file,
                        row['Source Language'],
                        row['Target Language'],
                        not pd.isna(row['Status']) and 'Error' in str(row['Status']),
                        skip_preprocess=self.prioritize_local and video_file in self.preprocessed_files
                    )
                    
                    # 更新完成状态
                    self.completed_tasks += 1
                    df.at[index, 'Status'] = status_msg
                    df.to_excel(self.tasks_setting_path, index=False)
                    
                    # 更新状态文本
                    if 'Error' in status_msg:
                        status_text.markdown(f"❌ 处理出错:  \n{video_file}  \n{status_msg}")
                    else:
                        status_text.success(f"✅ 已完成: {video_file}")
                    
                    # 更新进度显示
                    display_task_status_func(self.tasks_setting_path, 
                                          status_text, 
                                          progress_placeholder, 
                                          table_placeholder)
                    
                else:
                    self.completed_tasks += 1
                    status_text.info(f"⏭️ 已跳过: {row['Video File']}")
                    
                    # 更新进度显示
                    display_task_status_func(self.tasks_setting_path, 
                                          status_text, 
                                          progress_placeholder, 
                                          table_placeholder)
                
                time.sleep(0.1)
            
            # 只更新完成信息，不显示
            st.session_state.process_complete_info = {
                'total_time': eu.convert_seconds(eu.total_time_duration),
                'total_cost': eu.get_formated_total_estimated_cost()
            }
            
            # 在处理完成后生成总结报告
            if self.completed_tasks > 0:
                summary = generate_batch_summary()
                console.print(Panel(summary, title="批处理总结", border_style="green"))
            
            return True
        except Exception as e:
            console.print(f"[bold red]Batch processing error: {str(e)}")
            # status_text 已在函数开头绑定为 None，这里做存在性判断
            if status_text is not None:
                try:
                    status_text.error(f"❌ 处理出错: {str(e)}")
                except Exception:
                    pass
            return False
        finally:
            # 清理所有临时文件
            self.cleanup_temp_files()
            eu.set_processing(False)

def check_api():
    """检查 API 连通性（绕过缓存，见 devdocs 已知问题 P1-8）"""
    try:
        resp = ask_gpt("This is a test, response 'message':'success' in json format.",
                      response_json=True, log_title=None, use_cache=False)
        return resp.get('message') == 'success'
    except Exception:
        return False

def output_total_cost():
    """输出总花费信息"""
    console.print(Panel("[bold green]视频全部处理完成，总耗时：{}\n预计总花费: {}[/bold green]"
                        .format(eu.convert_seconds(eu.total_time_duration), 
                               eu.get_formated_total_estimated_cost())))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]
        processor = BatchProcessor(folder_path)
        processor.process_batch()
    else:
        console.print(Panel("未提供视频存储文件夹路径", title="错误", style="bold red")) 