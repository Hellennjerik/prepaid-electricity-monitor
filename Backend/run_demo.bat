@echo off
REM Starts the whole backend for a demo. Double-click this file, or run it from the Backend folder.
cd /d "%~dp0"
set DEV_MODE=1
echo Starting the server in a new window...
start "NextHome server" cmd /k python app.py
echo Waiting 6 seconds for it to start...
timeout /t 6 /nobreak >nul
python populate_demo.py
python check_system.py
echo.
echo Admin console: http://localhost:5000/admin  (password: admin123 unless ADMIN_PASSWORD is set)
echo Keep the "NextHome server" window open while you demo. Verification codes appear there.
pause
