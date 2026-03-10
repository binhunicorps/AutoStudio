@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Auto Studio

echo.
echo Auto Studio
echo URL: http://localhost:5000
echo.

call :stop_server

start "" /B "%~dp0scripts\run_server.bat"

echo Dang khoi dong server...
set "ok=0"
for /l %%i in (1,1,300) do (
    if "!ok!"=="0" (
        powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing http://localhost:5000 -TimeoutSec 1; exit 0 } catch { exit 1 }" >nul 2>&1
        if not errorlevel 1 set "ok=1"
        if "!ok!"=="0" timeout /t 1 /nobreak >nul
    )
)
if "%ok%"=="0" (
    echo Khong the khoi dong server. Kiem tra log loi o tren.
    call :stop_server
    exit /b 1
)

echo Server da san sang! Dang mo trinh duyet...
start "" "http://localhost:5000"
echo.
echo Nhan phim bat ky de dung server...
pause >nul

echo Dang dung server...
call :stop_server

echo Server da dung.
exit /b 0

:stop_server
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING 2^>nul') do (
    taskkill /F /PID %%a >nul 2>&1
)
exit /b 0
