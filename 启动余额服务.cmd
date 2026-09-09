@echo off
rem Start the api-quota local data service manually.
rem --standalone keeps it running even if ZCode is closed (normally the
rem service follows ZCode's lifecycle and is started by the SessionStart hook).
where pythonw >nul 2>nul
if errorlevel 1 (
  echo [ERROR] pythonw not found in PATH. Install Python 3.10+ first.
  pause
  exit /b 1
)
start "" pythonw "%~dp0scripts\quota-server.py" --standalone
