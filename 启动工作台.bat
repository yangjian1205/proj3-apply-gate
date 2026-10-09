@echo off
cd /d "%~dp0"
title proj3 workbench

set "PY=E:\agent-learning\day1-first-llm\venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [ERROR] Python venv not found:
  echo   %PY%
  echo.
  echo Edit this file and point PY to your venv python.exe
  pause
  exit /b 1
)

echo Starting proj3 workbench on http://127.0.0.1:8103
echo The browser will open automatically.
echo Close this window (or Ctrl+C) to stop the server.
echo.

"%PY%" server.py

pause
