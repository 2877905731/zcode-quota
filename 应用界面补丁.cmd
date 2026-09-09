@echo off
chcp 65001 >nul
setlocal
set "HERE=%~dp0"
set "PY=python"
where python >nul 2>nul || set "PY=C:\Python314\python.exe"

"%PY%" "%HERE%scripts\patch-zcode.py" --check
echo.
"%PY%" "%HERE%scripts\patch-zcode.py" --apply
echo.
pause
