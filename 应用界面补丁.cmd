@echo off
chcp 65001 >nul
setlocal

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python not found in PATH. Install Python 3.10+ first.
  pause
  exit /b 1
)

python "%~dp0scripts\patch-zcode.py" --check
echo.
python "%~dp0scripts\patch-zcode.py" --apply
echo.
pause
