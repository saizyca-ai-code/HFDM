@echo off
setlocal EnableExtensions
title HFDM portable builder

set "HFDM_ROOT=%~dp0"
set "HFDM_SCRIPT=%HFDM_ROOT%scripts\build_portable.ps1"

if not exist "%HFDM_SCRIPT%" (
  echo [ERROR] Portable build script was not found: %HFDM_SCRIPT%
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%HFDM_SCRIPT%" %*
exit /b %errorlevel%
