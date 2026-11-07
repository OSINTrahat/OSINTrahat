@echo off
REM Luna Binance Manager - Windows launcher
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -c "import tkinter" 2>nul || (echo Python with tkinter is required. Reinstall Python from python.org and tick "tcl/tk and IDLE". & pause & exit /b 1)
  py -3 main.py
) else (
  python -c "import tkinter" 2>nul || (echo Python with tkinter is required. Reinstall Python from python.org and tick "tcl/tk and IDLE". & pause & exit /b 1)
  python main.py
)
if %errorlevel% neq 0 pause
