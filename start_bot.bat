@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python telegram_bot.py
pause
