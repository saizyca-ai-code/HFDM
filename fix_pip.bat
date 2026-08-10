@echo off
setlocal EnableExtensions
title HFDM embedded Python package repair

set "HFDM_ROOT=%~dp0"
set "HFDM_PYTHON=%HFDM_ROOT%python_embed\python.exe"
set "HFDM_GET_PIP=%HFDM_ROOT%python_embed\get-pip.py"

if not exist "%HFDM_PYTHON%" (
  echo [ERROR] Embedded Python was not found: %HFDM_PYTHON%
  pause
  exit /b 1
)

if not exist "%HFDM_GET_PIP%" (
  echo [ERROR] get-pip.py was not found: %HFDM_GET_PIP%
  pause
  exit /b 1
)

"%HFDM_PYTHON%" "%HFDM_GET_PIP%"
if errorlevel 1 goto failed

"%HFDM_PYTHON%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto failed

"%HFDM_PYTHON%" -m pip install -r "%HFDM_ROOT%requirements.txt"
if errorlevel 1 goto failed

echo [OK] Embedded Python packages are ready.
pause
exit /b 0

:failed
echo [ERROR] Could not repair the embedded Python packages.
pause
exit /b 1
