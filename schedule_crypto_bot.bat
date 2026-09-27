@echo off
REM Register a Windows Task Scheduler job that runs the crypto bot once a day
REM at 10:05 (every day incl. weekends — crypto trades 24/7). TWS must be open.
REM   schedule_crypto_bot.bat            create/update the task
REM   schedule_crypto_bot.bat remove     delete the task
cd /d "%~dp0"
set TASK=TradeMarketCryptoBot

if /i "%~1"=="remove" (
    schtasks /Delete /TN %TASK% /F
    exit /b %errorlevel%
)

schtasks /Create /F /TN %TASK% /SC DAILY /ST 10:05 ^
    /TR "\"%~dp0run_crypto_bot.bat\" --once"
if errorlevel 1 (
    echo Failed to create the task. Try running this file as Administrator.
    pause
    exit /b 1
)
echo.
echo Task "%TASK%" created: runs run_crypto_bot.bat --once daily at 10:05.
echo Output is appended to crypto_bot.log in this folder.
pause
