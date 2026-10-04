@echo off
echo Stopping Telegram Bot background process...
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*telegram_bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('Stopped process ID: ' + $_.ProcessId) }"
echo Done!
pause
