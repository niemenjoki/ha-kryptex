@echo off
rem Kryptex bridge installer: double-click this file.
rem It only starts install.ps1 from this same folder. Read install.ps1 to see every step it takes.
rem Options for install.ps1 can be given after the name, e.g.  install.cmd -Port 5501

rem Step 1: the firewall rule needs administrator rights. fltmc works only as administrator, so it tells whether
rem this window has them. If not, ask Windows (the usual "Do you want to allow this app to make changes" prompt)
rem and run this file again as administrator.
fltmc >nul 2>&1
if not errorlevel 1 goto install
if "%~1"=="" goto elevate_plain
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
if errorlevel 1 goto denied
exit /b
:elevate_plain
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
if errorlevel 1 goto denied
exit /b

:install
rem Step 2: run the installer. "-ExecutionPolicy Bypass" lets Windows run this one unsigned script this once;
rem it does not change any Windows setting.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*

rem Step 3: keep the window open so you can copy the API key.
echo.
pause
exit /b

:denied
echo Administrator rights were not given, so nothing was installed.
pause
