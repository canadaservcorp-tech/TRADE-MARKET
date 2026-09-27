@echo off
REM Start the trading bot on Windows. Run this next to TWS (logged in, API enabled).
REM   run_bot.bat          keeps looping (Ctrl+C to stop)
REM   run_bot.bat --once   single pass then exit (used by the scheduled task)
REM First run creates the venv and installs dependencies.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv .venv || goto :fail
    .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt || goto :fail
)

echo Starting bot (create a file named KILL_SWITCH to liquidate and exit)...
.venv\Scripts\python.exe bot.py %*
if /i "%~1"=="--once" exit /b %errorlevel%
pause
exit /b 0

:fail
echo Setup failed. Make sure Python 3 is installed and on PATH (https://www.python.org/downloads/).
if /i not "%~1"=="--once" pause
exit /b 1
