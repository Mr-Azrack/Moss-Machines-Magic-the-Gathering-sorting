@echo off
setlocal
title Moss Machines Card Sorter

set "REPO_DIR=D:\Sorter\Moss-Machines-Magic-the-Gathering-sorting"
set "APP_DIR=%REPO_DIR%\Current version"
set "START_FILE=%APP_DIR%\sorter_gui_main.py"

if not exist "%REPO_DIR%\.git" (
    echo.
    echo ERROR: Git repository not found at:
    echo %REPO_DIR%
    echo.
    pause
    exit /b 1
)

cd /d "%REPO_DIR%"

echo Checking for sorter updates...
git pull origin main
if errorlevel 1 (
    echo.
    echo ERROR: Git update failed.
    echo Fix the Git error above before starting the sorter.
    echo.
    pause
    exit /b 1
)

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
    py "sorter_gui_main.py"
) else (
    where python >nul 2>&1
    if %errorlevel%==0 (
        python "sorter_gui_main.py"
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
