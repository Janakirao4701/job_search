@echo off
title JobStaffer Scraper & Application Tracker
cd /d "%~dp0"

echo ==============================================
echo        Starting JobStaffer Node Server
echo ==============================================

:: Check if virtual environment exists
if not exist ".venv" (
    echo [INFO] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Python not found or failed to create venv. Please ensure Python is installed and added to PATH.
        pause
        exit /b 1
    )
)

:: Activate virtual environment and install/update dependencies
echo [INFO] Verifying/Installing dependencies...
.venv\Scripts\pip.exe install -r requirements.txt
if errorlevel 1 (
    echo [WARNING] Failed to verify or install dependencies automatically. Trying to start anyway...
)

:: Launch the browser with the local port
echo [INFO] Launching dashboard in default browser...
start http://localhost:5000

:: Start the Flask app
echo [INFO] Starting Flask Server...
.venv\Scripts\python.exe app.py
if errorlevel 1 (
    echo [ERROR] Flask server stopped unexpectedly or failed to start.
    pause
)
