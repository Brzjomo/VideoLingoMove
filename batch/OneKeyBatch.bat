@echo off
chcp 65001 >nul 2>&1
cd /D "%~dp0"
cd ..

call conda activate videolingo

:ask_folder
set /p user_input="请输入视频存储文件夹路径 (输入 d 使用默认路径): "
if /i "%user_input%"=="d" (
    set folder_path=%cd%\batch\input
) else (
    if exist "%user_input%" (
        set folder_path=%user_input%
    ) else (
        echo 目录不存在: %user_input%
        exit /b 1
    )
)
@REM echo 当前folder_path设置为: %folder_path%

:ask_add_videos
set /p user_input="是否新建tasks_setting.xlsx, 并将文件夹中的视频添加至文件? (输入 y 或 n): "
if /i "%user_input%"=="y" (
    call python batch\utils\add_videos_to_doc.py "%folder_path%"
) else if /i "%user_input%"=="n" (
    goto ask_batch_processor
) else (
    echo 请输入 y 或 n
    goto ask_add_videos
)

:ask_batch_processor
set /p user_input="是否开始批量翻译? (输入 y 或 n): "
if /i "%user_input%"=="y" (
    call python batch\utils\batch_processor.py "%folder_path%"
) else if /i "%user_input%"=="n" (
    goto end
) else (
    echo 请输入 y 或 n
    goto ask_batch_processor
)

:end
pause
