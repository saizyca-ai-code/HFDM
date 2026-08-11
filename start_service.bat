@echo off
setlocal EnableExtensions EnableDelayedExpansion
title HFDM

REM ============================================================================
REM HFDM settings - edit these defaults to match your machine or LAN.
REM Existing HFDM_* environment variables override the defaults for automation.
REM
REM HFDM_HOST          0.0.0.0 = allow LAN access; 127.0.0.1 = local machine only
REM HFDM_PORT          TCP port used by the web service (1-65535)
REM HFDM_OPEN_BROWSER  1 = open the page after startup; 0 = do not open it
REM HFDM_BROWSER_URL   Optional page to open. Leave empty to derive it automatically.
REM ============================================================================
set "HFDM_DEFAULT_HOST=0.0.0.0"
set "HFDM_DEFAULT_PORT=8765"
set "HFDM_DEFAULT_OPEN_BROWSER=1"
set "HFDM_DEFAULT_BROWSER_URL="

if not defined HFDM_HOST set "HFDM_HOST=%HFDM_DEFAULT_HOST%"
if not defined HFDM_PORT set "HFDM_PORT=%HFDM_DEFAULT_PORT%"
if not defined HFDM_OPEN_BROWSER set "HFDM_OPEN_BROWSER=%HFDM_DEFAULT_OPEN_BROWSER%"
if not defined HFDM_BROWSER_URL set "HFDM_BROWSER_URL=%HFDM_DEFAULT_BROWSER_URL%"

REM Resolve every path from this batch file so the whole folder stays portable.
set "HFDM_ROOT=%~dp0"
set "HFDM_APP_DIR=%HFDM_ROOT%app"
set "HFDM_PYTHON=%HFDM_ROOT%python_embed\python.exe"

if not exist "%HFDM_PYTHON%" (
  echo [ERROR] Embedded Python was not found:
  echo         %HFDM_PYTHON%
  pause
  exit /b 1
)

if not exist "%HFDM_APP_DIR%\app.py" (
  echo [ERROR] HFDM application was not found:
  echo         %HFDM_APP_DIR%\app.py
  pause
  exit /b 1
)

REM Load HFDM from source. Installing the HFDM package itself is not required.
set "PYTHONPATH=%HFDM_APP_DIR%\src"
set "PYTHONPYCACHEPREFIX=%HFDM_ROOT%.pycache"
set "PATH=%HFDM_ROOT%python_embed;%HFDM_ROOT%python_embed\Scripts;%PATH%"

echo Python: %HFDM_PYTHON%
echo HFDM root: %HFDM_ROOT%
echo Listen: %HFDM_HOST%:%HFDM_PORT%
if "%HFDM_OPEN_BROWSER%"=="1" echo Browser: automatic

pushd "%HFDM_ROOT%"

if /I "%~1"=="--check" (
  "%HFDM_PYTHON%" -c "from hfdm.config import AppPaths; from hfdm.main import ServerSettings; import hfdm; p=AppPaths.discover(); s=ServerSettings.from_environment(); print('HFDM startup check passed:', hfdm.__file__); print('Data:', p.data); print('Downloads:', p.downloads); print('Frontend:', p.frontend_dist); print('Listen:', s.listen_url); print('Browser:', s.browser_url if s.open_browser else 'disabled')"
  set "HFDM_EXIT_CODE=!errorlevel!"
  popd
  exit /b !HFDM_EXIT_CODE!
)

"%HFDM_PYTHON%" "%HFDM_APP_DIR%\app.py" %*
set "HFDM_EXIT_CODE=%errorlevel%"
popd

if not "%HFDM_EXIT_CODE%"=="0" (
  echo [ERROR] HFDM stopped with exit code %HFDM_EXIT_CODE%.
  pause
)
exit /b %HFDM_EXIT_CODE%
