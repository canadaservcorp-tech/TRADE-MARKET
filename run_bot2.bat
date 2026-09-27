@echo off
REM Start the bot 2 on Windows (needs TWS logged in, API enabled, and
REM config.BOT2_ENABLED = True). Run run_bot.bat first once so .venv exists.
REM   run_bot2.bat          keeps looping (Ctrl+C to stop)
REM   run_bot2.bat --once   single pass then exit (used by the scheduled task)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run run_bot.bat once first to create the virtual environment.
    pause
    exit /b 1
)
echo Starting bot 2 (create KILL_SWITCH_BOT2 to liquidate and exit)...
.venv\Scripts\python.exe bot2.py %*
if /i "%~1"=="--once" exit /b %errorlevel%
pause
exit /b 0
