@echo off
REM ============================================================
REM  Install-Autorun.bat — Optional one-time setup for true autorun.
REM  Right-click and "Run as administrator".
REM
REM  This registers a scheduled task that launches ScreenSense
REM  whenever this specific USB volume is mounted. After running
REM  this once, the app will start automatically on every insert.
REM
REM  To remove: schtasks /Delete /TN "ScreenSense_Autorun" /F
REM ============================================================

setlocal
set "EXEPATH=%~dp0ScreenSense.exe"
set "VOLLABEL=ScreenSense"

if not exist "%EXEPATH%" (
    echo ERROR: ScreenSense.exe not found next to this script.
    pause
    exit /b 1
)

REM Get the volume serial number of this drive so the trigger only fires
REM for THIS specific stick, not every USB drive the user inserts.
for /f "tokens=2 delims=:" %%a in ('vol %~d0 ^| findstr Serial') do set "SERIAL=%%a"
set "SERIAL=%SERIAL: =%"

echo Registering scheduled task for volume serial %SERIAL% ...

schtasks /Create /F /TN "ScreenSense_Autorun" /SC ONEVENT ^
  /EC Microsoft-Windows-Kernel-PnP/Configuration ^
  /MO "*[System[(EventID=410)]]" ^
  /TR "\"%EXEPATH%\""

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Failed to register task. Make sure you ran this as Administrator.
    pause
    exit /b 1
)

echo.
echo Done. ScreenSense will now auto-launch when devices are plugged in.
echo To remove: schtasks /Delete /TN "ScreenSense_Autorun" /F
pause
