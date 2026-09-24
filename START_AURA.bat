@echo off
title AURA Core Launcher
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Virtual environment .venv not found in D:\AURA!
    pause
    exit /b 1
)

".venv\Scripts\python.exe" aura_launcher.py %*

if %errorlevel% neq 0 (
    echo.
    echo [INFO] Launcher exited with code %errorlevel%.
    pause
)