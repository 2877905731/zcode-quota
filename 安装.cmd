@echo off
chcp 65001 >nul
setlocal

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH.
  echo         Install Python 3.10+ from https://www.python.org/downloads/
  echo         and make sure "Add python.exe to PATH" is checked.
  echo.
  pause
  exit /b 1
)

python "%~dp0scripts\install.py"
echo.
pause
