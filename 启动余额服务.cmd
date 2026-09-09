@echo off
rem Start the api-quota local data service (feeds the in-app status line).
start "" pythonw "%~dp0scripts\quota-server.py"
