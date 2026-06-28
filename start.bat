@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
title Comfy Telegram Bot

:loop
echo ============================================
echo   Comfy Telegram Bot is running
echo   To STOP the bot - just close this window.
echo ============================================
".venv\Scripts\python.exe" bot.py
if %errorlevel% equ 42 (
  echo.
  echo Bot is already running in another window. You can close this one.
  pause
  exit /b
)
echo.
echo Bot stopped. Restarting in 5 seconds... ^(close window to disable^)
timeout /t 5 >nul
goto loop
