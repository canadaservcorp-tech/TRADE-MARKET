@echo off
REM Register a Windows Task Scheduler job that runs the bot 2 once every weekday
REM at 10:05 (your PC's local time). TWS must be open.
REM   schedule_bot2.bat            create/update the task
REM   schedule_bot2.bat remove     delete the task
cd /d "%~dp0"
set TASK=TradeMarketBot2

if /i "%~1"=="remove" (
    schtasks /Delete /TN %TASK% /F
    exit /b %errorlevel%
)

schtasks /Create /F /TN %TASK% /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:05 ^
    /TR "\"%~dp0run_bot2.bat\" --once"
if errorlevel 1 (
    echo Failed to create the task. Try running this file as Administrator.
    pause
    exit /b 1
)
echo.
echo Task "%TASK%" created: runs run_bot2.bat --once Mon-Fri at 10:05.
echo Output is appended to bot2.log in this folder.
pause
