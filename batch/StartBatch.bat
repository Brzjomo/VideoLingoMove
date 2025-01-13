@echo off
call "%USERPROFILE%\anaconda3\Scripts\activate.bat" videolingo
cd ..
python -m streamlit run "batch\utils\gui.py"

pause
