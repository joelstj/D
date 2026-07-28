@echo off
setlocal

title Build L2ArbBot.exe

echo ============================================================
echo   L2ArbBot -- build the self-installing Windows .exe
echo ============================================================
echo.
echo This compiles launcher\dist\L2ArbBot.exe from source. You only
echo need to run this once (or after pulling new changes). The
echo resulting L2ArbBot.exe is what you run day to day: double-click
echo it and it installs itself the first time, then just launches on
echo every run after that.
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build_windows_exe.ps1"
set "BUILD_RC=%ERRORLEVEL%"

echo.
if "%BUILD_RC%"=="0" (
    echo ============================================================
    echo   Build succeeded: launcher\dist\L2ArbBot.exe
    echo ============================================================
    echo.
    echo Double-click that file to install L2ArbBot ^(first run^) or
    echo launch it ^(every run after^).
) else (
    echo ============================================================
    echo   Build FAILED - exit code %BUILD_RC%
    echo ============================================================
    echo.
    echo See the output above for the error. Common causes: Python is
    echo not installed, or you're offline ^(the first build needs
    echo internet to install PyInstaller^).
)

echo.
pause
exit /b %BUILD_RC%
