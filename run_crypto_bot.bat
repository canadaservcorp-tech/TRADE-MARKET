@echo off
REM Start the crypto bot on Windows (needs TWS logged in, API enabled, and
REM config.CRYPTO_ENABLED = True). Run run_bot.bat first once so .venv exists.
REM   run_crypto_bot.bat          keeps looping (Ctrl+C to stop)
REM   run_crypto_bot.bat --once   single pass then exit (used by the scheduled task)
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Run run_bot.bat once first to create the virtual environment.
    pause
    exit /b 1
)
echo Starting crypto bot (create KILL_SWITCH_CRYPTO to liquidate and exit)...
.venv\Scripts\python.exe crypto_bot.py %*
if /i "%~1"=="--once" exit /b %errorlevel%
pause
exit /b 0
