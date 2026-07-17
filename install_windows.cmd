@echo off
setlocal enabledelayedexpansion

rem Installs the Kitsu Maya Plugin into Maya's user modules folder.
rem
rem Usage: install_windows.cmd [custom modules folder]
rem   Run with no arguments to install to the standard location
rem   (%%USERPROFILE%%\Documents\maya\modules), which every Maya version
rem   scans at startup. If your Documents folder is redirected or you use
rem   a custom MAYA_MODULE_PATH, pass that modules folder as an argument
rem   to this script instead.

set "SCRIPT_DIR=%~dp0"
set "SOURCE=%SCRIPT_DIR%modules"

if "%~1"=="" (
    set "TARGET=%USERPROFILE%\Documents\maya\modules"
) else (
    set "TARGET=%~1"
)

echo Installing Kitsu Maya Plugin
echo   From: %SOURCE%
echo   To:   %TARGET%
echo.

if not exist "%SOURCE%\kitsu_maya_plugin.mod" (
    echo ERROR: %SOURCE%\kitsu_maya_plugin.mod not found.
    echo Run this script from the repository root, or check your checkout.
    exit /b 1
)

if not exist "%TARGET%" (
    mkdir "%TARGET%"
    if errorlevel 1 (
        echo ERROR: Could not create %TARGET%
        exit /b 1
    )
)

rem Remove any previous install of this plugin's own folder first, for a
rem clean, idempotent install that never leaves stale files from an older
rem version behind.
if exist "%TARGET%\kitsu_maya_plugin" (
    rmdir /s /q "%TARGET%\kitsu_maya_plugin"
)

copy /y "%SOURCE%\kitsu_maya_plugin.mod" "%TARGET%\kitsu_maya_plugin.mod" >nul
if errorlevel 1 (
    echo ERROR: Could not copy the .mod file.
    exit /b 1
)

robocopy "%SOURCE%\kitsu_maya_plugin" "%TARGET%\kitsu_maya_plugin" /E /NFL /NDL /NJH /NJS >nul
if errorlevel 8 (
    echo ERROR: Could not copy the plugin folder.
    exit /b 1
)

echo.
echo Done. Restart Maya, then enable "kitsuMayaPlugin.py" in
echo Windows ^> Settings/Preferences ^> Plug-in Manager (check both
echo "Loaded" and "Auto load").
pause
endlocal
