@echo off
rem ============================================================================
rem  VideoLingo launcher (Windows)
rem
rem  Picks a working interpreter and starts the app:
rem    1. the project-local .venv  (preferred -- created by Install.bat)
rem    2. the conda environment "videolingo" (legacy layout, still supported)
rem    3. any python 3.10-3.13 on PATH
rem
rem  It runs a health check first and, if that fails, tells you how to fix it
rem  instead of dying inside Streamlit. The app is started through launch.py so
rem  the run is also written to logs\videolingo_<timestamp>.log.
rem
rem  NOTE: this file is ASCII-only on purpose. cmd.exe parses the WHOLE .bat
rem  before "chcp 65001" can take effect, so non-ASCII bytes get read in the
rem  console code page and break parsing (symptom: a stray line like
rem  "'-----------------' is not recognized as an internal or external
rem  command", followed by a bare python REPL). Chinese output comes from
rem  installer.py / launch.py instead.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "CONDA_ENV=%USERPROFILE%\anaconda3\envs\videolingo"
set "PY="

rem ---- 1) project-local .venv ------------------------------------------------
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>&1
    if not errorlevel 1 (
        set "PY=%VENV_PY%"
        echo [env] using project .venv
        goto :run
    )
    echo [warn] .venv exists but its python is unusable -- falling back
)

rem ---- 2) legacy conda env --------------------------------------------------
if exist "%CONDA_ENV%\python.exe" (
    "%CONDA_ENV%\python.exe" -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PY=%CONDA_ENV%\python.exe"
        echo [env] using conda env videolingo
        goto :run
    )
)

rem ---- 3) python on PATH -----------------------------------------------------
python -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    echo [env] using python on PATH
    goto :run
)

echo.
echo [ERROR] No usable Python 3.10-3.13 found.
echo         Checked: .venv, conda env videolingo, and PATH.
echo.
echo         Recommended:  run Install.bat
echo         Manual:       python setup_env.py --python 3.11
echo.
pause
exit /b 1

:run
rem Guard: if PY somehow still points at a broken interpreter, python would
rem drop into an interactive REPL and the window would look "hung". Verify
rem once more and abort cleanly instead.
%PY% -c "import sys" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Interpreter is not usable: %PY%
    pause
    exit /b 1
)

echo [1/2] health check ...
%PY% installer.py --check --quiet
if errorlevel 1 (
    echo.
    echo [hint] Health check reported problems.
    echo        Fix automatically:  python setup_env.py
    echo        Full report:        .venv\Scripts\python.exe installer.py --check --smoke
    echo.
)

echo [2/2] starting VideoLingo ...
%PY% launch.py

pause
exit /b 0
