@echo off
REM Register a Windows Task Scheduler job that runs the bot once every weekday
REM at 10:00 (your PC's local time). TWS must be open and logged in at that time.
REM   schedule_bot.bat            create/update the task
REM   schedule_bot.bat remove     delete the task
cd /d "%~dp0"
set TASK=TradeMarketBot

if /i "%~1"=="remove" (
    schtasks /Delete /TN %TASK% /F
    exit /b %errorlevel%
)

schtasks /Create /F /TN %TASK% /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:00 ^
    /TR "\"%~dp0run_bot.bat\" --once"
if errorlevel 1 (
    echo Failed to create the task. Try running this file as Administrator.
    pause
    exit /b 1
)
echo.
echo Task "%TASK%" created: runs run_bot.bat --once Mon-Fri at 10:00.
echo Check it in Task Scheduler; output is appended to bot.log in this folder.
echo To run it right now for a test:  schtasks /Run /TN %TASK%
pause
