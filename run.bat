@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
    python scripts\launch.py %*
) else (
    py -3 scripts\launch.py %*
)
set "espresso_exit=%errorlevel%"
if not "%espresso_exit%"=="0" (
    echo.
    echo Espresso could not start. Review the message above.
    pause
)
exit /b %espresso_exit%
