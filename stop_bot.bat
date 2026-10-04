@echo off
echo Stopping Telegram Bot background process...
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *telegram_bot*" 2>nul
taskkill /F /FI "COMMANDLINE eq *telegram_bot.py*" 2>nul
wmic process where "CommandLine like '%%telegram_bot.py%%'" call terminate 2>nul
echo Done!
pause
