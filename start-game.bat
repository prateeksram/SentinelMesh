@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start_game.ps1" %*
set "GAME_EXIT=%ERRORLEVEL%"
endlocal & exit /b %GAME_EXIT%
