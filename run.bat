@echo off
REM run.bat - Windows batch launcher that delegates to run.ps1 for unified single-terminal execution.
REM
REM Requires: PowerShell and uv (https://docs.astral.sh/uv/)
REM Ports:    API 8000, UI 8501 (override with API_PORT / UI_PORT).
REM Env:      set RUN_INGEST=0 to skip index build/refresh.
REM
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
exit /b %ERRORLEVEL%
