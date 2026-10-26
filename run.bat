@echo off
rem Starts PNB Peer Benchmarking. Keep this window open while the app is in use.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1"
echo.
pause
