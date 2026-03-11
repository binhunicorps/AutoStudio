@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "APP_DIR=%~dp0.."
for %%I in ("%APP_DIR%") do set "APP_DIR=%%~fI"

set "PY_DIR=%APP_DIR%\runtime\python"
set "PYTHON_EXE=%PY_DIR%\python.exe"
set "REQ_FILE=%APP_DIR%\requirements.txt"

REM ── Try embedded Python first, then fall back to system Python ──
if exist "%PYTHON_EXE%" goto :python_ready

echo [bootstrap] Embedded Python not found, trying system Python...
where python >nul 2>&1
if errorlevel 1 (
    echo [bootstrap] ERROR: Python not found!
    echo [bootstrap] Please install Python from https://www.python.org/downloads/
    echo [bootstrap] Or copy runtime\python\ from another device.
    exit /b 1
)
set "PYTHON_EXE=python"

:python_ready
REM Ensure project root is on sys.path for embedded Python (_pth mode).
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

if not exist "%REQ_FILE%" (
    echo [bootstrap] ERROR: requirements.txt not found at "%REQ_FILE%"
    exit /b 1
)

REM Ensure required packages exist.
REM Use marker file to skip slow import check on subsequent starts.
set "DEPS_OK=%PY_DIR%\.deps_ok"

REM Invalidate marker if requirements.txt is newer
if exist "%DEPS_OK%" (
    for %%R in ("%REQ_FILE%") do for %%D in ("%DEPS_OK%") do (
        if "%%~tR" GTR "%%~tD" del "%DEPS_OK%" 2>nul
    )
)

if exist "%DEPS_OK%" goto :deps_ready

"%PYTHON_EXE%" -c "import flask, requests" >nul 2>&1
if errorlevel 1 (
    echo [bootstrap] Installing missing dependencies...
    "%PYTHON_EXE%" -m pip install --disable-pip-version-check -q -r "%REQ_FILE%"
    if errorlevel 1 (
        echo [bootstrap] ERROR: Failed to install dependencies.
        exit /b 1
    )
)
echo.>"%DEPS_OK%"
:deps_ready

echo [bootstrap] Starting server...
"%PYTHON_EXE%" "%APP_DIR%\server.py"
exit /b %errorlevel%
