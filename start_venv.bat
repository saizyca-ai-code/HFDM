@echo off
setlocal EnableExtensions
title HFDM embedded Python shell

set "HFDM_ROOT=%~dp0"
set "HFDM_APP_DIR=%HFDM_ROOT%app"
set "HFDM_PYTHON=%HFDM_ROOT%python_embed\python.exe"

if not exist "%HFDM_PYTHON%" (
  echo [ERROR] Embedded Python was not found: %HFDM_PYTHON%
  pause
  exit /b 1
)

set "PYTHONPATH=%HFDM_APP_DIR%\src"
set "PYTHONPYCACHEPREFIX=%HFDM_ROOT%.pycache"
set "PATH=%HFDM_ROOT%python_embed;%HFDM_ROOT%python_embed\Scripts;%PATH%"

echo Python: %HFDM_PYTHON%
echo HFDM root: %HFDM_ROOT%
echo.

cd /d "%HFDM_ROOT%"
cmd /k
