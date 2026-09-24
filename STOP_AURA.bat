@echo off
title AURA Core Stopper
cd /d "%~dp0"

".venv\Scripts\python.exe" aura_launcher.py --stop

echo.
pause