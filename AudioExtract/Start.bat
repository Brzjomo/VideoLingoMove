@echo off
call "%USERPROFILE%\anaconda3\Scripts\activate.bat" videolingo
python -m streamlit run gui.py

pause
