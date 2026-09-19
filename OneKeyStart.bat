@echo off
chcp 65001 >nul

rem 切到脚本所在目录：直接双击或从别处调用时，否则会因为工作目录不对
rem 找不到 st.py / config.example.yaml（原脚本缺这一行）。
cd /d "%~dp0"

rem 指定的 conda 环境不存在时，直接给出明确提示，而不是让 activate 静默失败后
rem 用一个错误的 Python 去跑（那样会报一堆看不懂的缺包错误）。
set "CONDA_ENV=%USERPROFILE%\anaconda3\envs\videolingo"
if not exist "%CONDA_ENV%\python.exe" (
    echo [错误] 未找到 conda 环境: %CONDA_ENV%
    echo         请先执行: conda create -n videolingo python=3.10
    echo         然后执行: python install.py
    pause
    exit /b 1
)

call "%USERPROFILE%\anaconda3\Scripts\activate.bat" videolingo

rem 启动前先体检；有问题就提示修复方式，而不是直接崩在 Streamlit 里。
rem 用 launch.py 启动可以顺带写入 logs\videolingo_<时间戳>.log。
python installer.py --check --quiet
if errorlevel 1 (
    echo.
    echo [提示] 体检发现问题，尝试修复: python install.py
    echo.
)

python launch.py

pause
