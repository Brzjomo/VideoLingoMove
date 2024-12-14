import os, sys
import pandas as pd
from rich.console import Console
from rich.panel import Panel

console = Console()

def add_all_videos_to_doc(folder_path):
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
    video_storage_folder = folder_path

    # 查看是否存在指定的文件夹，如果不存在则创建
    if not os.path.exists(video_storage_folder):
        os.mkdir(video_storage_folder)
    # 遍历目录下的所有支持的视频文件(不包含子目录)('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm')，并将文件名添加到video_files列表中
    for file in os.listdir(video_storage_folder):
        if file.endswith(('.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm')):
            srt_file = os.path.splitext(os.path.basename(file))[0] + '.srt'
            if srt_file in os.listdir(video_storage_folder):
                console.print(Panel(f"跳过视频文件（已存在字幕）：{file}", title="注意", style="bold yellow"))
                continue
            video_files.append(file)
            console.print(Panel(f"已找到视频文件：{file}", title="提示", style="bold white"))
    # 如果video_files列表为空，则输出提示信息并退出程序
    if len(video_files) == 0:
        return console.print(Panel(f"未找到需要翻译的视频文件，请检查视频文件夹：\n{video_storage_folder}", title="错误", style="bold red"))
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
    if len(sys.argv) > 1:
        folder_path = sys.argv[1]
        # console.print(Panel(f"视频存储文件夹路径：{folder_path}", title="提示", style="bold green"))
        add_all_videos_to_doc(folder_path)
    else:
        console.print(Panel("未提供视频存储文件夹路径", title="错误", style="bold red"))