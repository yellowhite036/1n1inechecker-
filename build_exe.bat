@echo off
chcp 65001 >nul
REM ============================================
REM   Python -> EXE quick build script (PyInstaller)
REM   CLI version - keeps console window for input()
REM   Usage:
REM     build_exe.bat              -> auto-detect .py in this folder
REM     build_exe.bat my_script.py -> build a specific file
REM ============================================

setlocal enabledelayedexpansion

set SCRIPT=%~1

if "%SCRIPT%"=="" (
    REM No argument given: auto-detect .py files in current folder
    set COUNT=0
    for %%F in (*.py) do (
        set /a COUNT+=1
        set "FOUND=%%F"
    )

    if !COUNT! equ 0 (
        echo No .py file found in this folder.
        pause
        exit /b 1
    )

    if !COUNT! gtr 1 (
        echo Multiple .py files found in this folder:
        for %%F in (*.py) do echo   - %%F
        echo.
        echo Please specify which one to build:
        echo   build_exe.bat your_script.py
        pause
        exit /b 1
    )

    set "SCRIPT=!FOUND!"
    echo Auto-detected script: !SCRIPT!
)

if not exist "%SCRIPT%" (
    echo Error: file "%SCRIPT%" not found
    pause
    exit /b 1
)

python -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found, installing...
    python -m pip install pyinstaller
)

echo.
echo Building "%SCRIPT%" ...
echo.

REM --onefile: bundle into a single exe
REM (no --noconsole here: this keeps the console window so input()/stdin works)
python -m PyInstaller --onefile "%SCRIPT%"

echo.
echo ============================================
echo Done! The exe is in the "dist" folder.
echo ============================================
pause