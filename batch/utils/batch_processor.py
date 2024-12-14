import os, sys
import gc
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))
from batch.utils.settings_check import check_settings
from batch.utils.video_processor import process_video
from core.config_utils import load_key, update_key
import pandas as pd
from rich.console import Console
from rich.panel import Panel
import time
import shutil
import easy_util as eu

console = Console()

def record_and_update_config(source_language, target_language):
    original_source_lang = load_key('whisper.language')
    original_target_lang = load_key('target_language')
    
    if source_language and not pd.isna(source_language):
        update_key('whisper.language', source_language)
    if target_language and not pd.isna(target_language):
        update_key('target_language', target_language)
    
    return original_source_lang, original_target_lang

def process_batch(folder_path):
    video_storage_folder = folder_path
    if not check_settings(folder_path):
        raise Exception("Settings check failed")

    eu.total_time_duration = 0
    eu.estimated_total_cost = 0

    df = pd.read_excel('batch/tasks_setting.xlsx')
    for index, row in df.iterrows():
        if pd.isna(row['Status']) or 'Error' in str(row['Status']):
            total_tasks = len(df)
            video_file = row['Video File']
            
            if not pd.isna(row['Status']) and 'Error' in str(row['Status']):
                console.print(Panel(f"Retrying failed task: {video_file}\nTask {index + 1}/{total_tasks}", 
                                 title="[bold yellow]Retry Task", expand=False))
                
                # Restore files from batch/output/ERROR to output
                error_folder = os.path.join('batch', 'output', 'ERROR', os.path.splitext(video_file)[0])
                
                if os.path.exists(error_folder):
                    # Ensure the output folder exists
                    os.makedirs('output', exist_ok=True)
                    
                    # Copy all contents from ERROR folder for the specific video to output
                    for item in os.listdir(error_folder):
                        src_path = os.path.join(error_folder, item)
                        dst_path = os.path.join('output', item)
                        
                        if os.path.isdir(src_path):
                            if os.path.exists(dst_path):
                                shutil.rmtree(dst_path)
                            shutil.copytree(src_path, dst_path)
                        else:
                            if os.path.exists(dst_path):
                                os.remove(dst_path)
                            shutil.copy2(src_path, dst_path)
                            
                    console.print(f"[green]Restored files from ERROR folder for {video_file}")
                else:
                    console.print(f"[yellow]Warning: Error folder not found: {error_folder}")
            else:
                console.print(Panel(f"Now processing task: {video_file}\nTask {index + 1}/{total_tasks}", 
                                 title="[bold blue]Current Task", expand=False))
            
            source_language = row['Source Language']
            target_language = row['Target Language']
            
            original_source_lang, original_target_lang = record_and_update_config(source_language, target_language)
            
            try:
                dubbing = 0 if pd.isna(row['Dubbing']) else int(row['Dubbing'])
                is_retry = not pd.isna(row['Status']) and 'Error' in str(row['Status'])
                status, error_step, error_message = process_video(video_storage_folder, video_file, dubbing, is_retry)
                status_msg = "Done" if status else f"Error: {error_step} - {error_message}"
            except Exception as e:
                status_msg = f"Error: Unhandled exception - {str(e)}"
                console.print(f"[bold red]Error processing {video_file}: {status_msg}")
            finally:
                update_key('whisper.language', original_source_lang)
                update_key('target_language', original_target_lang)
                
                df.at[index, 'Status'] = status_msg
                df.to_excel('batch/tasks_setting.xlsx', index=False)
                
                gc.collect()
                
                time.sleep(1)
        else:
            print(f"Skipping task: {row['Video File']} - Status: {row['Status']}")

    console.print(Panel("All tasks processed!\nCheck out in `batch/output`!", 
                       title="[bold green]Batch Processing Complete", expand=False))
    output_total_cost()

def output_total_cost():
    console.print(Panel("[bold green]视频全部处理完成，总耗时：{}\n预计总花费: {}[/bold green]"
                        .format(eu.convert_seconds(eu.total_time_duration), eu.get_formated_total_estimated_cost())))

if __name__ == "__main__":
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]
        # console.print(Panel(f"视频存储文件夹路径：{folder_path}", title="提示", style="bold green"))
        process_batch(folder_path)
    else:
        console.print(Panel("未提供视频存储文件夹路径", title="错误", style="bold red"))