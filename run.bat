@echo off
chcp 65001 >nul
echo =========================================================
echo   DiskPulse — Smart Disk Analyzer ^& Cleaner
echo   Dang khoi dong tren moi truong Conda (base)...
echo   Dia chi Web Dashboard: http://127.0.0.1:8765
echo =========================================================
start "" "http://127.0.0.1:8765"
conda run -n base --no-capture-output python "%~dp0main.py"
pause
