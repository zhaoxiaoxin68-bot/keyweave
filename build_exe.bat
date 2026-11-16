@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto install
py -3 -m venv .venv
if errorlevel 1 goto failed
:install
".venv\Scripts\python.exe" -m pip install ".[build]"
if errorlevel 1 goto failed
".venv\Scripts\python.exe" tools\build_exe.py
if errorlevel 1 goto failed
echo.
echo Build complete: artifacts\KeyWeave.exe
pause
exit /b 0
:failed
echo.
echo Build failed. Check the error above. Windows x64 and Python 3.11+ are required.
pause
exit /b 1
