@echo off
setlocal EnableExtensions
title HFDM runtime manager

set "HFDM_ROOT=%~dp0"
set "HFDM_SCRIPT=%HFDM_ROOT%scripts\manage_runtime.ps1"

if not exist "%HFDM_SCRIPT%" (
  echo [ERROR] Runtime manager was not found: %HFDM_SCRIPT%
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%HFDM_SCRIPT%" %*
exit /b %errorlevel%
