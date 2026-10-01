@echo off
setlocal
title Moss Machines Card Sorter

REM Preferred repo locations on the Desktop
set "APP_DIR=%USERPROFILE%\Desktop\Moss-Machines-Magic-the-Gathering-sorting\Current version"

if not exist "%APP_DIR%\gui_interface_enhanced.py" (
    set "APP_DIR=%USERPROFILE%\Desktop\Sorter-Development\Current version"
)

if not exist "%APP_DIR%\gui_interface_enhanced.py" (
    set "APP_DIR=%USERPROFILE%\OneDrive\Desktop\Moss-Machines-Magic-the-Gathering-sorting\Current version"
)

if not exist "%APP_DIR%\gui_interface_enhanced.py" (
    set "APP_DIR=%USERPROFILE%\OneDrive\Desktop\Sorter-Development\Current version"
)

REM Fallback: search Desktop for the GUI file
if not exist "%APP_DIR%\gui_interface_enhanced.py" (
    for /r "%USERPROFILE%\Desktop" %%F in (gui_interface_enhanced.py) do (
        set "APP_DIR=%%~dpF"
        goto :FOUND
    )
)

REM Fallback: search OneDrive Desktop if present
if not exist "%APP_DIR%\gui_interface_enhanced.py" (
    if exist "%USERPROFILE%\OneDrive\Desktop" (
        for /r "%USERPROFILE%\OneDrive\Desktop" %%F in (gui_interface_enhanced.py) do (
            set "APP_DIR=%%~dpF"
            goto :FOUND
        )
    )
)

:FOUND
if not exist "%APP_DIR%\gui_interface_enhanced.py" (
    echo.
    echo ERROR: Could not find gui_interface_enhanced.py on the Desktop.
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
