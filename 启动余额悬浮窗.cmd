@echo off
rem Start the api-quota floating widget (always on top, refreshes every 60s).
start "" pythonw "%~dp0scripts\quota-widget.pyw"
