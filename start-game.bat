@echo off
setlocal
rem Give the supervisor its own console. Ctrl+C is then handled by PowerShell's
rem cleanup path instead of CMD asking "Terminate batch job (Y/N)?" afterwards.
start "SentinelMesh Game" /D "%~dp0" /WAIT powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\start_game.ps1" %*
set "GAME_EXIT=%ERRORLEVEL%"
endlocal & exit /b %GAME_EXIT%
