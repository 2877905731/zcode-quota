@echo off
rem Start the api-quota local data service (feeds the in-app status line).
where pythonw >nul 2>nul
if errorlevel 1 (
  echo [ERROR] pythonw not found in PATH. Install Python 3.10+ first.
  pause
  exit /b 1
)
start "" pythonw "%~dp0scripts\quota-server.py"
