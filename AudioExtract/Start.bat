@echo off
rem ============================================================================
rem  VideoLingo audio extractor (Windows) -- launches AudioExtract\gui.py
rem
rem  Interpreter order: project .venv  ->  conda env videolingo  ->  python on PATH
rem
rem  Fixes over the old version:
rem    * the old script had no "cd", so invoking it from anywhere but this folder
rem      made python fail to find gui.py; it now resolves its own directory;
rem    * the interpreter is probed instead of assumed, so a broken environment
rem      gives a clear message instead of dumping you into a python REPL.
rem
rem  ASCII-only on purpose: cmd.exe parses the whole .bat before "chcp 65001"
rem  takes effect, so non-ASCII bytes break parsing on non-UTF-8 code pages.
rem  Chinese output comes from Python instead.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

set "VENV_PY=%~dp0..\.venv\Scripts\python.exe"
set "CONDA_ENV=%USERPROFILE%\anaconda3\envs\videolingo"
set "PY="

if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<(3,14)and __import__('streamlit')and __import__('rich')else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PY=%VENV_PY%"
        echo [env] using project .venv
        goto :run
    )
    echo [warn] .venv exists but its python is unusable -- falling back
)

if exist "%CONDA_ENV%\python.exe" (
    "%CONDA_ENV%\python.exe" -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<(3,14)and __import__('streamlit')and __import__('rich')else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PY=%CONDA_ENV%\python.exe"
        echo [env] using conda env videolingo
        goto :run
    )
)

python -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<(3,14)and __import__('streamlit')and __import__('rich')else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    echo [env] using python on PATH
    goto :run
)

echo.
echo [ERROR] No usable Python 3.10-3.13 found.
echo         Recommended:  run Install.bat in the project root.
echo.
pause
exit /b 1

:run
%PY% -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<(3,14)and __import__('streamlit')and __import__('rich')else 1)" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Interpreter is not usable: %PY%
    pause
    exit /b 1
)

echo starting audio extractor ...
%PY% -m streamlit run gui.py

pause
exit /b 0
