@echo off
REM run.bat - launch the whole Stratpoint RAG chatbot (backend API + frontend UI) together.
REM Windows cmd.exe. Mirrors the docker-compose commands.
REM
REM Requires: uv (https://docs.astral.sh/uv/) and a .env with NVIDIA_API_KEY (copy .envexample).
REM Ports:    API 8000, UI 8501 (override by setting API_PORT / UI_PORT before running).
REM Env:      set RUN_INGEST=0 to skip the index build/refresh (faster restarts once warm).
REM
REM The backend opens in its own window titled "Stratpoint API"; the UI runs in THIS window.
REM Stop the UI with Ctrl+C, then close the "Stratpoint API" window to stop the backend.
REM
setlocal
cd /d "%~dp0"

if "%API_PORT%"=="" set "API_PORT=8000"
if "%UI_PORT%"=="" set "UI_PORT=8501"
set "STRATPOINT_API_URL=http://localhost:%API_PORT%"

echo ==^> Syncing dependencies (uv sync)
call uv sync || goto :error

if not exist ".env" (
  echo WARNING: .env not found - the API needs NVIDIA_API_KEY to answer questions.
  echo          Copy .envexample to .env and set NVIDIA_API_KEY. The UI will still start.
)

if not "%RUN_INGEST%"=="0" (
  echo ==^> Building/refreshing the retrieval index ^(first run is slow: downloads the embedding
  echo     model and embeds the 371-page corpus; later runs are near-instant, hash-gated^)
  call uv run stratpoint-rag-ingest || goto :error
)

echo ==^> Starting backend API in a new window  -^> http://localhost:%API_PORT%
start "Stratpoint API" cmd /k uv run uvicorn stratpoint_rag.api.app:app --host 0.0.0.0 --port %API_PORT%

echo ==^> Starting frontend UI  -^> http://localhost:%UI_PORT%
call uv run streamlit run src/stratpoint_rag/ui/app.py --server.port %UI_PORT% --server.address 0.0.0.0 --server.headless true

echo.
echo ==^> UI stopped. Close the "Stratpoint API" window to stop the backend.
goto :eof

:error
echo.
echo Failed - see the output above.
exit /b 1
