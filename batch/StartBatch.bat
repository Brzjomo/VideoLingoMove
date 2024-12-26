@echo off
chcp 65001 >nul 2>&1
cd /D "%~dp0"
cd ..

call conda activate videolingo

streamlit run batch/gui.py

pause 