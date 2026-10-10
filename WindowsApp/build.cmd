@echo off
setlocal
cd /d "%~dp0.."
py -3.12 -c "import sys,struct;assert sys.platform=='win32' and struct.calcsize('P')==8, 'Windows x64 Python 3.12 required'"
if errorlevel 1 exit /b 1
if not exist ".build\windows-venv\Scripts\python.exe" py -3.12 -m venv ".build\windows-venv"
if errorlevel 1 exit /b 1
".build\windows-venv\Scripts\python.exe" -m pip install --only-binary=:all: setuptools==84.0.0 wheel==0.45.1
if errorlevel 1 exit /b 1
".build\windows-venv\Scripts\python.exe" -m pip install --only-binary=:all: --no-binary=proxy_tools --no-build-isolation -r "WindowsApp\requirements.txt"
if errorlevel 1 exit /b 1
".build\windows-venv\Scripts\python.exe" -m pip check
if errorlevel 1 exit /b 1
".build\windows-venv\Scripts\python.exe" -B build.py --windows
exit /b %errorlevel%
