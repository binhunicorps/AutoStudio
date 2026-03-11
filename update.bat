@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Auto Studio - Update

echo.
echo Auto Studio - Cap Nhat
echo.

set "APP_DIR=%~dp0"
for %%I in ("%APP_DIR%.") do set "APP_DIR=%%~fI"

REM Check if git is available
where git >nul 2>&1
if errorlevel 1 (
    echo [update] Git chua duoc cai dat.
    echo [update] Vui long cai dat Git: https://git-scm.com/download/win
    pause
    exit /b 1
)

REM Check if this is a git repo
if not exist "%APP_DIR%\.git" (
    echo [update] Thu muc nay chua phai la Git repo.
    echo [update] Hay chay: git init ^&^& git remote add origin ^<URL_REPO^>
    pause
    exit /b 1
)

echo [update] Dang kiem tra cap nhat...
cd /d "%APP_DIR%"

git fetch origin 2>nul
if errorlevel 1 (
    echo [update] Khong the ket noi den server. Kiem tra mang.
    pause
    exit /b 1
)

REM Check if there are updates
for /f %%i in ('git rev-parse HEAD') do set "LOCAL=%%i"
for /f %%i in ('git rev-parse @{u}') do set "REMOTE=%%i"

if "%LOCAL%"=="%REMOTE%" (
    echo [update] Da la phien ban moi nhat!
    echo.
    pause
    exit /b 0
)

echo [update] Co phien ban moi! Dang cap nhat...
echo.

REM Pull updates
git pull --ff-only origin main 2>&1
if errorlevel 1 (
    echo.
    echo [update] Loi cap nhat. Co the ban da chinh sua file app.
    echo [update] Thu chay: git stash ^&^& git pull ^&^& git stash pop
    pause
    exit /b 1
)

echo.
echo Cap nhat thanh cong!
echo Hay khoi dong lai Auto Studio.
echo.
pause
exit /b 0
