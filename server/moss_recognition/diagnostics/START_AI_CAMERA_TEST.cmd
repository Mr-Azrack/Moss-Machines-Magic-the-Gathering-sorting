@echo off
title MOSS Server AI Camera Test - NO MOTOR CONTROL
powershell.exe -NoLogo -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0START_AI_CAMERA_TEST.ps1"
if errorlevel 1 pause
