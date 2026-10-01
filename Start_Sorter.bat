@echo off
setlocal
title Moss Machines Card Sorter

set "APP_DIR=D:\Sorter\Moss-Machines-Magic-the-Gathering-sorting\Current version"
set "START_FILE=%APP_DIR%\gui_interface_enhanced.py"

if not exist "%START_FILE%" (
    echo.
    echo ERROR: Sorter GUI not found at:
    echo %START_FILE%
    echo.
    pause
    exit /b 1
)

cd /d "%APP_DIR%"

where py >nul 2>&1
if %errorlevel%==0 (
    py "gui_interface_enhanced.py"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        python "gui_interface_enhanced.py"
    ) else (
        echo.
        echo ERROR: Python was not found in PATH.
        echo.
        pause
        exit /b 1
    )
)

if not %errorlevel%==0 (
    echo.
    echo The sorter GUI exited with an error.
    echo.
    pause
)

endlocal
