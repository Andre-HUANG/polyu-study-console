@echo off
setlocal
cd /d "%~dp0.."
py -3.12 -B WindowsApp\assemble_windows.py --download
exit /b %errorlevel%
