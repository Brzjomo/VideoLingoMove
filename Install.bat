@echo off
rem ============================================================================
rem  VideoLingo one-click setup for Windows
rem
rem  What it does:
rem    1. Makes sure Python 3.11 is available (3.10-3.13 also work), trying
rem       uv, then winget, then the official installer;
rem    2. Installs uv, used to create a project-local virtual environment so
rem       nothing is written to the C: drive;
rem    3. Creates .venv inside the project and points every cache at it;
rem    4. Installs dependencies. The torch wheels are chosen automatically
rem       (cu126 / cu128 / cu129) from your GPU compute capability;
rem    5. Runs the health check plus the unit tests, then prints how to start.
rem
rem  Usage:
rem    Install.bat                 normal install, safe to re-run
rem    Install.bat --smoke         also import whisperx/torchcodec/pyannote
rem    Install.bat --check         health check only, no install
rem    Install.bat --download-only print big-file download URLs only
rem
rem  Bad network? Run "Install.bat --download-only" first, download the big
rem  files with a browser or download manager into the _downloads\ folder,
rem  then run "Install.bat" again. It will reuse those local files.
rem
rem  NOTE: this file is intentionally ASCII-only. A .bat containing non-ASCII
rem  text is parsed by cmd.exe BEFORE "chcp 65001" can take effect, which
rem  breaks parsing on non-UTF-8 code pages. Chinese output lives in
rem  setup_env.py / installer.py instead.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"

rem The Python side prints UTF-8 (easy_util.ensure_utf8_console reconfigures
rem stdout/stderr), so the console code page must be UTF-8 too, otherwise all
rem the Chinese diagnostics show up as mojibake. Safe here because everything
rem written before this line is pure ASCII.
chcp 65001 >nul

set "PYVER=3.11"
set "PYDIR=%LocalAppData%\Programs\Python\Python311"
set "PYEXE=%PYDIR%\python.exe"
set "PYINSTALLER=%TEMP%\python-3.11.9-amd64.exe"
set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "PY="

echo ============================================================
echo   VideoLingo setup (Windows)
echo   Project: %~dp0
echo ============================================================
echo.

echo [1/5] Checking uv ...
call :ensure_uv
echo.

echo [2/5] Checking Python %PYVER% ...
call :ensure_python
if not defined PY goto :no_python
echo       Using: %PY%
echo.

echo [3/5] Creating the project-local environment and installing deps ...
echo       Note: the torch wheel is 2.7-3.6 GB. On a slow connection run
echo             Install.bat --download-only first, put the big files into
echo             _downloads\, then re-run this script.
echo.
%PY% "%~dp0setup_env.py" --python %PYVER% %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" goto :failed

echo.
echo [4/5] Running unit tests ...
echo       (these tests only check the code; they delete nothing and change
echo        nothing. Output goes to logs\unittest.log, shown only on failure)
if not exist "%~dp0logs" mkdir "%~dp0logs"
if exist "%VENV_PY%" (
    "%VENV_PY%" -m unittest discover -s tests > "%~dp0logs\unittest.log" 2>&1
) else (
    %PY% -m unittest discover -s tests > "%~dp0logs\unittest.log" 2>&1
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo [ERROR] Unit tests failed ^(exit code %RC%^). Full output:
    echo.
    type "%~dp0logs\unittest.log"
    goto :failed
)
echo       ok - all tests passed ^(log: logs\unittest.log^)

echo.
echo [5/5] Done.
echo.
echo   Start the app:    OneKeyStart.bat
echo   Start manually:   .venv\Scripts\python.exe launch.py
echo   Health check:     .venv\Scripts\python.exe installer.py --check --smoke
echo   Big-file URLs:    .venv\Scripts\python.exe installer.py --download-only
echo.
pause
exit /b 0


rem ================================================================ subroutines
:ensure_uv
where uv >nul 2>&1
if not errorlevel 1 (
    echo       uv is ready
    goto :eof
)
where winget >nul 2>&1
if not errorlevel 1 (
    echo       uv not found, installing with winget ...
    winget install --id astral-sh.uv -e --source winget --accept-package-agreements --accept-source-agreements
    where uv >nul 2>&1
    if not errorlevel 1 (
        echo       uv installed
        goto :eof
    )
)
echo       Trying the official uv installer script ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>&1
if not errorlevel 1 (
    echo       uv installed
    goto :eof
)
echo       [WARN] uv is unavailable. Setup continues, but setup_env.py will
echo              fall back to the current interpreter (no Python download).
goto :eof


:ensure_python
rem 1) reuse an existing project-local environment
if exist "%VENV_PY%" (
    set "PY=%VENV_PY%"
    echo       Reusing the existing .venv
    goto :eof
)
rem 2) versions already registered with the py launcher, 3.11 first
call :try_pyver 3.11
if defined PY goto :eof
call :try_pyver 3.10
if defined PY goto :eof
call :try_pyver 3.12
if defined PY goto :eof
call :try_pyver 3.13
if defined PY goto :eof
rem 3) whatever "python" resolves to on PATH
python -c "import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<(3,14) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PY=python"
    echo       Using the python on PATH
    goto :eof
)
rem 4) the usual per-user install location
if exist "%PYEXE%" (
    set "PY=%PYEXE%"
    echo       Using %PYEXE%
    goto :eof
)
rem 5) let uv download a suitable Python
where uv >nul 2>&1
if not errorlevel 1 (
    echo       No suitable Python found, asking uv for %PYVER% ...
    uv python install %PYVER% >nul 2>&1
    for /f "delims=" %%P in ('uv python find %PYVER% 2^>nul') do set "PY=%%P"
    if defined PY (
        echo       uv provided Python %PYVER%
        goto :eof
    )
)
rem 6) winget
where winget >nul 2>&1
if not errorlevel 1 (
    echo       Installing Python %PYVER% with winget ...
    winget install --id Python.Python.3.11 -e --source winget --accept-package-agreements --accept-source-agreements
    if exist "%PYEXE%" (
        set "PY=%PYEXE%"
        goto :eof
    )
)
rem 7) last resort: the official installer
echo       Downloading the official Python %PYVER% installer ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $u='https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe'; Write-Host ('      ' + $u); Invoke-WebRequest -Uri $u -OutFile $env:TEMP\python-3.11.9-amd64.exe"
if not exist "%PYINSTALLER%" (
    echo       [ERROR] download failed
    goto :eof
)
echo       Installing silently into %PYDIR% ...
"%PYINSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 TargetDir="%PYDIR%"
if exist "%PYEXE%" (
    set "PY=%PYEXE%"
    goto :eof
)
echo       [ERROR] %PYEXE% still not found
goto :eof


:try_pyver
rem usage: call :try_pyver 3.11   -- sets PY when that version is registered
py -%1 -c "import sys" >nul 2>&1
if errorlevel 1 goto :eof
set "PY=py -%1"
echo       Using the py launcher: Python %1
goto :eof


:no_python
echo.
echo [ERROR] Could not prepare Python 3.10-3.13.
echo         Please install Python 3.11 manually and tick
echo         "Add python.exe to PATH" during setup:
echo           https://www.python.org/downloads/release/python-3119/
echo         Then run this script again.
echo.
pause
exit /b 1


:failed
echo.
echo [ERROR] Setup failed with exit code %RC%.
echo         What to try:
echo           1) Slow network: run "Install.bat --download-only" first, put
echo              the big files into _downloads\, then run this script again;
echo           2) Full health report:
echo              .venv\Scripts\python.exe installer.py --check --smoke
echo           3) Start over: delete the .venv folder and run this again.
echo.
pause
exit /b %RC%
