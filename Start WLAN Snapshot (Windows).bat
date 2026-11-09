@echo off
setlocal
title Cisco Meraki WLAN Snapshot
rem Double-click to start Cisco Meraki WLAN Snapshot. Close this window to stop it.
cd /d "%~dp0"

if not exist "server.py" (
  echo server.py wasn't found next to this launcher.
  echo.
  echo If you opened this from inside the .zip file: close it, right-click the .zip,
  echo choose "Extract All...", then run this launcher from the extracted folder.
  goto :done
)

call :findpy
if defined PY goto :run

echo Python 3 is needed to run the app, and it isn't installed on this computer.
echo It's free, and can be installed just for your Windows account (no admin rights needed).
echo.
choice /c YN /m "Install Python 3 now"
if errorlevel 2 goto :declined
where winget >nul 2>nul || goto :manual

echo.
echo Installing Python 3 with Windows Package Manager (winget). This takes a minute or two...
echo By installing, you agree to the Python license (https://docs.python.org/3/license.html).
echo.
winget install --id Python.Python.3.13 -e --scope user --accept-package-agreements --accept-source-agreements
call :findpy
if defined PY (
  echo.
  echo Python is installed.
  echo.
  goto :run
)
echo.
echo If Python installed, this window can't see it yet: close this window and double-click the launcher again.
echo If the install failed, install it from https://www.python.org/downloads/ instead.
goto :done

:declined
echo.
echo No problem. Install Python 3 from https://www.python.org/downloads/ when you're ready
echo (tick "Add python.exe to PATH"), then double-click this launcher again.
goto :done

:manual
echo.
echo Windows Package Manager isn't available here, so Python has to be installed by hand.
echo Opening the Python download page in your browser...
start "" "https://www.python.org/downloads/windows/"
echo Install it, tick "Add python.exe to PATH" during setup, then double-click this launcher again.
goto :done

:run
echo Starting Cisco Meraki WLAN Snapshot...
echo Your browser should open in a moment. If it doesn't, open the http://localhost address shown below.
echo Keep this window open while you use the app. Close it to stop.
echo.
%PY% server.py --open
echo.
echo The app has stopped. Any error is shown above.

:done
echo.
pause
exit /b

rem Sets PY to a working Python 3. The Microsoft Store "python" shortcut exists even when
rem Python isn't installed, so each candidate is actually run. A fresh install may not be on
rem this window's PATH yet, so the per-user install folders are checked too.
:findpy
set "PY="
py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (python --version >nul 2>nul && set "PY=python")
if not defined PY (python3 --version >nul 2>nul && set "PY=python3")
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "PY="%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" -3"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" set "PY="%%D\python.exe""
exit /b
