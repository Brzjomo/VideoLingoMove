@echo off
rem ============================================================================
rem  VideoLingo cleanup -- frees space on the C: drive
rem
rem  Removes caches and auto-downloaded files created while running VideoLingo:
rem    1. the old conda environment named "videolingo" (if it still exists);
rem    2. pip / uv download caches (pure caches, safe);
rem    3. HuggingFace / torch model caches (these may be SHARED with other
rem       projects, so they are opt-in);
rem    4. the project's own big files: _downloads\ (torch wheels, ~7 GB) and
rem       ffmpeg\ (the bundled FFmpeg).
rem
rem  Run it with no arguments first: it only SCANS and reports, it never
rem  deletes anything. You then choose what to clean.
rem
rem  Usage:
rem    Cleanup.bat                scan and report only (default, safe)
rem    Cleanup.bat --clean        clean the safe caches (pip / uv)
rem    Cleanup.bat --clean --models   also HuggingFace / torch model caches
rem    Cleanup.bat --clean --models --all   also _downloads\ and ffmpeg\
rem    Cleanup.bat --clean --yes  no confirmation prompt
rem    Cleanup.bat --help         full option list
rem
rem  Two things this script will NEVER do:
rem    * it will not uninstall Anaconda/Miniconda itself (11 GB, other projects
rem      live in there);
rem    * it will not touch other conda environments. On this machine there are
rem      "aisummary" and "novelmanager" -- they are reported, never deleted.
rem
rem  NOTE: ASCII only on purpose. cmd.exe parses the whole .bat before "chcp"
rem  can take effect, so non-ASCII bytes break parsing on non-UTF-8 code pages.
rem  All Chinese output comes from cleanup.py.
rem ============================================================================
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "PY="

rem Prefer the project interpreter (it has the caches' metadata), then any
rem suitable Python on the machine. cleanup.py only uses the standard library.
if exist "%VENV_PY%" set "PY=%VENV_PY%"
if not defined PY (
    python -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,8) else 1)" >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3"
)

if not defined PY (
    echo [ERROR] No Python found. VideoLingo's setup installs one:
    echo         run Install.bat first, or install Python 3.11 manually.
    echo.
    pause
    exit /b 1
)

%PY% "%~dp0cleanup.py" %*
set "RC=%ERRORLEVEL%"

echo.
pause
exit /b %RC%
