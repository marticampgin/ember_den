@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 -m den.launcher
goto finished
:use_python
python -m den.launcher
:finished
if errorlevel 1 pause
