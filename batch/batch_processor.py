import os, sys
import gc
import pandas as pd
from rich.console import Console
from rich.panel import Panel
import time
import shutil
import json
from threading import Lock
import streamlit as st

# 添加项目根目录到系统路径
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.dirname(current_dir)
sys.path.append(root_dir)

from core.config_utils import load_key, update_key
from st_components.imports_and_utils import ask_gpt
from utils.video_processor import process_video
import easy_util as eu

console = Console()
status_lock = Lock()

class BatchProcessor:
    def __init__(self, folder_path):
        # 规范化路径
        self.folder_path = os.path.abspath(folder_path)
        
        # 获取项目根目录
        self.root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.batch_dir = os.path.join(self.root_dir, 'batch')
        
        # 设置配置文件路径
        self.tasks_setting_path = os.path.join(self.batch_dir, 'tasks_setting.xlsx')
        self.status_file_path = os.path.join(self.batch_dir, 'current_status.json')
        self.template_path = os.path.join(self.batch_dir, 'tasks_setting-template.xlsx')
        
        # 初始化状态
        self.total_tasks = 0
        self.completed_tasks = 0
        eu.total_time_duration = 0
        eu.estimated_total_cost = 0
        
        # 确保目录存在
        os.makedirs(self.folder_path, exist_ok=True)
        os.makedirs(self.batch_dir, exist_ok=True)
    
    def update_status(self, status_info):
        """更新处理状态"""
        with status_lock:
            with open(self.status_file_path, 'w', encoding='utf-8') as f:
                json.dump(status_info, f, ensure_ascii=False, indent=2)
            st.session_state.current_task_info = status_info
    
    def check_settings(self):
        """检查设置和环境"""
        try:
            # 检查视频文件
            video_files = []
            for file in os.listdir(self.folder_path):
                if file.endswith(('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm')):
                    srt_file = os.path.splitext(os.path.basename(file))[0] + '.srt'
                    if srt_file not in os.listdir(self.folder_path):
                        video_files.append(file)
            
            if not video_files:
                raise Exception(f"未找到需要处理的视频文件，请检查文件夹：{self.folder_path}")
            
            return video_files
        except Exception as e:
            console.print(Panel(f"设置检查失败: {str(e)}", title="错误", style="bold red"))
            raise
    
    def create_or_update_tasks(self):
        """创建或更新任务配置"""
        try:
            # 检查并创建模板文件
            if not os.path.exists(self.template_path):
                df_template = pd.DataFrame(columns=['Video File', 'Source Language', 'Target Language', 'Dubbing', 'Status'])
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
                    'Dubbing': [0],
                    'Status': [None]
                })
                df = pd.concat([df, new_row], ignore_index=True)
            
            # 保存配置
            df.to_excel(self.tasks_setting_path, index=False)
            return df
            
        except Exception as e:
            console.print(Panel(f"创建任务配置失败: {str(e)}", title="错误", style="bold red"))
            raise
    
    def process_single_video(self, video_file, source_lang, target_lang, dubbing, is_retry=False):
        """处理单个视频"""
        # 保存原始语言设置
        original_source_lang = load_key('whisper.language')
        original_target_lang = load_key('target_language')
        
        try:
            # 更新语言设置
            if source_lang and not pd.isna(source_lang):
                update_key('whisper.language', source_lang)
            if target_lang and not pd.isna(target_lang):
                update_key('target_language', target_lang)
            
            # 处理视频
            status, error_step, error_message = process_video(
                self.folder_path, video_file, dubbing, is_retry)
            return "Done" if status else f"Error: {error_step} - {error_message}"
        except Exception as e:
            return f"Error: Unhandled exception - {str(e)}"
        finally:
            # 恢复原始语言设置
            update_key('whisper.language', original_source_lang)
            update_key('target_language', original_target_lang)
    
    def process_batch(self):
        """批量处理视频"""
        try:
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
            
            # 初始化状态
            self.update_status({
                'current_file': '',
                'task_number': 0,
                'total_tasks': self.total_tasks,
                'status': 'Starting...'
            })
            
            # 处理每个视频
            for index, row in df.iterrows():
                if pd.isna(row['Status']) or 'Error' in str(row['Status']):
                    video_file = row['Video File']
                    
                    # 更新状态为处理中
                    self.update_status({
                        'current_file': video_file,
                        'task_number': self.completed_tasks,
                        'total_tasks': self.total_tasks,
                        'status': 'processing'
                    })
                    
                    # 更新Excel状态
                    df.at[index, 'Status'] = 'Processing...'
                    df.to_excel(self.tasks_setting_path, index=False)
                    
                    # 处理视频
                    status_msg = self.process_single_video(
                        video_file,
                        row['Source Language'],
                        row['Target Language'],
                        0 if pd.isna(row['Dubbing']) else int(row['Dubbing']),
                        not pd.isna(row['Status']) and 'Error' in str(row['Status'])
                    )
                    
                    # 更新完成状态
                    self.completed_tasks += 1
                    df.at[index, 'Status'] = status_msg
                    df.to_excel(self.tasks_setting_path, index=False)
                    
                    # 更新处理状态
                    self.update_status({
                        'current_file': video_file,
                        'task_number': self.completed_tasks,
                        'total_tasks': self.total_tasks,
                        'status': status_msg
                    })
                    
                    gc.collect()
                else:
                    self.completed_tasks += 1
                    print(f"Skipping task: {row['Video File']} - Status: {row['Status']}")
                    
                    # 更新跳过状态
                    self.update_status({
                        'current_file': row['Video File'],
                        'task_number': self.completed_tasks,
                        'total_tasks': self.total_tasks,
                        'status': 'Skipped'
                    })
            
            # 更新完成信息
            st.session_state.process_complete_info = {
                'total_time': eu.convert_seconds(eu.total_time_duration),
                'total_cost': eu.get_formated_total_estimated_cost()
            }
            
            return True
        except Exception as e:
            console.print(f"[bold red]Batch processing error: {str(e)}")
            return False
        finally:
            # 清理状态
            if os.path.exists(self.status_file_path):
                os.remove(self.status_file_path)
            st.session_state.current_task_info = None
            st.session_state.processing = False

def check_api():
    """检查API状态"""
    try:
        resp = ask_gpt("This is a test, response 'message':'success' in json format.",
                      response_json=True, log_title='None')
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