@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Auto Studio - Release

echo.
echo Auto Studio - Tao Phien Ban
echo.

set "APP_DIR=%~dp0"
for %%I in ("%APP_DIR%.") do set "APP_DIR=%%~fI"
cd /d "%APP_DIR%"

REM Read current version
set /p CURRENT_VER=<VERSION
echo   Phien ban hien tai: v%CURRENT_VER%
echo.

REM Ask for new version
set /p NEW_VER="  Nhap phien ban moi (vd: 1.1.0): "
if "%NEW_VER%"=="" (
    echo   Huy tao phien ban.
    pause
    exit /b 0
)

REM Ask for release notes
set /p NOTES="  Mo ta cap nhat: "
if "%NOTES%"=="" set "NOTES=Release v%NEW_VER%"

echo.
echo   Phien ban: v%CURRENT_VER% -^> v%NEW_VER%
echo   Mo ta: %NOTES%

set /p CONFIRM="  Xac nhan? (y/n): "
if /i not "%CONFIRM%"=="y" (
    echo   Huy tao phien ban.
    pause
    exit /b 0
)

echo.
echo [release] Dang tao phien ban v%NEW_VER%...

REM Update VERSION file
echo %NEW_VER%> VERSION

REM Stage all changes and commit
git add -A
git commit -m "release: v%NEW_VER% - %NOTES%"
if errorlevel 1 (
    echo [release] Khong co thay doi nao de commit.
    echo [release] Tao tag cho commit hien tai...
)

REM Create annotated tag
git tag -a "v%NEW_VER%" -m "%NOTES%"
if errorlevel 1 (
    echo [release] ERROR: Khong the tao tag. Co the tag da ton tai.
    pause
    exit /b 1
)

REM Push commit and tag
git push origin main
git push origin "v%NEW_VER%"

echo.
echo   Da tao phien ban v%NEW_VER%
echo   Tag: v%NEW_VER%
echo.
pause
exit /b 0
