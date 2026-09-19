@echo off
chcp 65001 >nul

rem 原脚本没有 cd，从别处调用或双击时工作目录不对会找不到 gui.py。
cd /d "%~dp0"

rem ---- 解释器选择：项目内 .venv 优先，其次 conda ----
set "VENV_PY=%~dp0..\.venv\Scripts\python.exe"
set "CONDA_ENV=%USERPROFILE%\anaconda3\envs\videolingo"

if exist "%VENV_PY%" (
    set "PY=%VENV_PY%"
    goto :run
)

if exist "%CONDA_ENV%\python.exe" (
    call "%USERPROFILE%\anaconda3\Scripts\activate.bat" videolingo
    set "PY=python"
    goto :run
)

echo [错误] 未找到可用的 Python 环境。
echo         推荐:  python setup_env.py --python 3.11
pause
exit /b 1

:run
"%PY%" -m streamlit run gui.py

pause
