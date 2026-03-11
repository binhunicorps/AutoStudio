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
:wait_loop
set "attempts=0"
:wait_check
if %attempts% GEQ 300 goto :wait_fail
set /a attempts+=1
timeout /t 1 /nobreak >nul
powershell -NoProfile -Command "try { $null = Invoke-WebRequest -UseBasicParsing http://localhost:5000 -TimeoutSec 1; exit 0 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 goto :wait_check

echo Server da san sang! Dang mo trinh duyet...
start "" "http://localhost:5000"
echo.
echo Nhan phim bat ky de dung server...
pause >nul

echo Dang dung server...
call :stop_server
echo Server da dung.
exit /b 0

:wait_fail
echo Khong the khoi dong server. Kiem tra log loi o tren.
call :stop_server
exit /b 1

:stop_server
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING 2^>nul') do (
    taskkill /F /PID %%a >nul 2>&1
)
exit /b 0
