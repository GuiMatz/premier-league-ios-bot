@echo off
cd /d "%~dp0"
for /f "delims=" %%i in ('py -c "import sys; print(sys.executable.replace('python.exe','pythonw.exe'))"') do set "PYW=%%i"
if not exist "%PYW%" set "PYW=pythonw"
start "" "%PYW%" "%~dp0cartola_tray.py"
