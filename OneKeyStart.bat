@echo off
chcp 65001 >nul

rem 切到脚本所在目录：直接双击或从别处调用时，否则会因为工作目录不对
rem 找不到 st.py / config.example.yaml（原脚本缺这一行）。
cd /d "%~dp0"

rem ---- 解释器选择：项目内 .venv 优先，其次 conda ---------------------------------
rem 环境大升级后推荐用 setup_env.py 建的项目内 .venv（不占 C 盘、可搬移）。
rem conda 分支保留是为了兼容旧环境，不做硬切。
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "CONDA_ENV=%USERPROFILE%\anaconda3\envs\videolingo"

if exist "%VENV_PY%" (
    set "PY=%VENV_PY%"
    echo [环境] 使用项目内虚拟环境: %VENV_PY%
    goto :run
)

if exist "%CONDA_ENV%\python.exe" (
    call "%USERPROFILE%\anaconda3\Scripts\activate.bat" videolingo
    set "PY=python"
    echo [环境] 使用 conda 环境: %CONDA_ENV%
    goto :run
)

echo [错误] 未找到可用的 Python 环境。
echo         推荐:  python setup_env.py --python 3.11
echo         旧方式: conda create -n videolingo python=3.11  ^&^&  python installer.py
pause
exit /b 1

:run
rem 启动前先体检；有问题就提示修复方式，而不是直接崩在 Streamlit 里。
rem 用 launch.py 启动可以顺带写入 logs\videolingo_<时间戳>.log。
"%PY%" installer.py --check --quiet
if errorlevel 1 (
    echo.
    echo [提示] 体检发现问题，尝试修复: python setup_env.py
    echo.
)

"%PY%" launch.py

pause
