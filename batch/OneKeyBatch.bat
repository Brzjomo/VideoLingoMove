@echo off
chcp 65001 >nul 2>&1
cd /D "%~dp0"
cd ..

call conda activate videolingo

:ask_add_videos
set /p user_input="是否新建tasks_setting.xlsx, 并将input中的视频添加至文件? (输入 y 或 n): "
if /i "%user_input%"=="y" (
    call python batch\utils\add_videos_to_doc.py
) else if /i "%user_input%"=="n" (
    goto ask_batch_processor
) else (
    echo 请输入 y 或 n
    goto ask_add_videos
)

:ask_batch_processor
set /p user_input="是否开始批量翻译? (输入 y 或 n): "
if /i "%user_input%"=="y" (
    call python batch\utils\batch_processor.py
) else if /i "%user_input%"=="n" (
    goto end
) else (
    echo 请输入 y 或 n
    goto ask_batch_processor
)

:end
pause
