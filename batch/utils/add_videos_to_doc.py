import os, sys
import pandas as pd
from rich.console import Console
from rich.panel import Panel

console = Console()

def add_all_videos_to_doc():
    # 切换目录
    os.chdir('batch')
    # 显示当前目录
    # console.print(Panel(f"当前目录：{os.getcwd()}", title="提示", style="bold green"))

    # 检查是否存在tasks_setting.xlsx文件，存在则删除
    if os.path.exists('tasks_setting.xlsx'):
        os.remove('tasks_setting.xlsx')
    # 如果存在tasks_setting-template.xlsx文件，复制一份并重命名为 tasks_setting.xlsx
    if os.path.exists('tasks_setting-template.xlsx'):
        os.system('copy tasks_setting-template.xlsx tasks_setting.xlsx')
    else:
        return console.print(Panel(f"未找到tasks_setting-template.xlsx文件，请先创建该文件", title="错误", style="bold red"))

    # 读取tasks_setting.xlsx文件
    df = pd.read_excel('tasks_setting.xlsx')
    video_files = []
    # 查看是否存在input文件夹，如果不存在则创建
    if not os.path.exists('input'):
        os.mkdir('input')
    # 遍历input下的所有支持的视频文件('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm')，并将文件名添加到video_files列表中
    for file in os.listdir('input'):
        if file.endswith(('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm')):
            video_files.append(file)
    # 将video_files列表中的文件添加到tasks_setting.xlsx文件
    for video in video_files:
        df = add_videos_to_doc(df, video, 'en', '简体中文', 0)

    console.print(Panel(f"已将所有视频文件添加到tasks_setting.xlsx文件中", title="提示", style="bold green"))



def add_videos_to_doc(df, video, source_language, target_language, dubbing):
    # video文件和配置添加到tasks_setting.xlsx文件中相应列的新行
    # 创建一个新的 DataFrame 行
    new_row = pd.DataFrame({
        'Video File': [video],
        'Source Language': [source_language],
        'Target Language': [target_language],
        'Dubbing': [dubbing]
    })
    # 使用 pd.concat 连接 DataFrame
    df = pd.concat([df, new_row], ignore_index=True)
    # 写入 Excel 文件
    df.to_excel('tasks_setting.xlsx', index=False)

    return df

if __name__ == "__main__":
    add_all_videos_to_doc()