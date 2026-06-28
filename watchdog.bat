@echo off
rem Запуск сторожа с авто-перезапуском (если сам сторож вдруг упадёт).
cd /d "%~dp0"
:loop
".venv\Scripts\python.exe" -u watchdog.py
timeout /t 10 /nobreak >nul
goto loop
