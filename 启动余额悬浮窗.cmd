@echo off
rem Start the api-quota floating widget (always on top, refreshes every 60s).
where pythonw >nul 2>nul
if errorlevel 1 (
  echo [ERROR] pythonw not found in PATH. Install Python 3.10+ first.
  pause
  exit /b 1
)
start "" pythonw "%~dp0scripts\quota-widget.pyw"
