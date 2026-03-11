@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "APP_DIR=%~dp0.."
for %%I in ("%APP_DIR%") do set "APP_DIR=%%~fI"

set "PY_DIR=%APP_DIR%\runtime\python"
set "PYTHON_EXE=%PY_DIR%\python.exe"

REM Try embedded Python first, then system Python
if exist "%PYTHON_EXE%" goto :python_ready

where python >nul 2>&1
if errorlevel 1 (
    echo [bootstrap] ERROR: Python not found!
    echo [bootstrap] Please install Python from https://www.python.org/downloads/
    exit /b 1
)
set "PYTHON_EXE=python"

:python_ready
REM Ensure project root is on sys.path for embedded Python
if not "%PYTHON_EXE%"=="python" (
    set "PY_PTH="
    for %%F in ("%PY_DIR%\python*._pth") do (
        if not defined PY_PTH set "PY_PTH=%%~fF"
    )
    if defined PY_PTH (
        findstr /x /c:"..\.." "%PY_PTH%" >nul 2>&1
        if errorlevel 1 echo ..\..">>"%PY_PTH%"
        findstr /x /c:"import site" "%PY_PTH%" >nul 2>&1
        if errorlevel 1 echo import site>>"%PY_PTH%"
    )
)

echo [bootstrap] Starting server...
"%PYTHON_EXE%" "%APP_DIR%\server.py"
exit /b %errorlevel%
