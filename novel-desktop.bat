@echo off
title OpenNovel Desktop

echo ========================================
echo   OpenNovel Desktop - Development Mode
echo ========================================
echo.

pushd "%~dp0" || (
    echo [ERROR] Failed to change to script directory
    pause
    exit /b 1
)
set "PROJECT_ROOT=%CD%"
echo Project Dir: %PROJECT_ROOT%

REM check virtual env
set "PYTHON=%PROJECT_ROOT%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] Virtual environment not found!
    echo   Expected: %PYTHON%
    echo.
    echo   Run these commands first:
    echo     py -3.11 -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -e ".[gui]"
    pause
    popd
    exit /b 1
)

"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not available: %PYTHON%
    pause
    popd
    exit /b 1
)

echo Python  : %PYTHON%
echo Starting OpenNovel Desktop...
echo.

"%PYTHON%" -m opennovel_desktop
set EXIT_CODE=%ERRORLEVEL%

echo.
echo ========================================
if %EXIT_CODE% equ 0 (
    echo Application exited normally
) else (
    echo Application exited with code: %EXIT_CODE%
)
echo ========================================

popd
pause
exit /b %EXIT_CODE%
