# run.ps1 — launch the whole Stratpoint RAG chatbot (backend API + frontend UI) together.
# Windows PowerShell (pwsh or Windows PowerShell 5.1). Mirrors the docker-compose commands.
#
# Requires: uv (https://docs.astral.sh/uv/) and a .env with NVIDIA_API_KEY (copy .envexample).
# Ports:    API 8000, UI 8501 (override with $env:API_PORT / $env:UI_PORT).
# Env:      $env:RUN_INGEST = "0" skips the index build/refresh (faster restarts once warm).
#
#   ./run.ps1        (you may first need:  Set-ExecutionPolicy -Scope Process Bypass)
#
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$ApiPort = if ($env:API_PORT) { $env:API_PORT } else { "8000" }
$UiPort  = if ($env:UI_PORT)  { $env:UI_PORT }  else { "8501" }
$env:STRATPOINT_API_URL = "http://localhost:$ApiPort"

Write-Host "==> Syncing dependencies (uv sync)"
uv sync

if (-not (Test-Path ".env")) {
  Write-Warning ".env not found - the API needs NVIDIA_API_KEY to answer questions."
  Write-Warning "Copy .envexample -> .env and set NVIDIA_API_KEY. The UI will still start."
}

if (($env:RUN_INGEST) -ne "0") {
  Write-Host "==> Building/refreshing the retrieval index (first run downloads the embedding model"
  Write-Host "    and embeds the 371-page corpus - a few minutes; later runs are near-instant)"
  uv run stratpoint-rag-ingest
}

# Backend in the background (same console, logs interleave); tracked so we can stop it on exit.
Write-Host "==> Starting backend API  -> http://localhost:$ApiPort"
$api = Start-Process -FilePath "uv" -PassThru -NoNewWindow -ArgumentList @(
  "run", "uvicorn", "stratpoint_rag.api.app:app", "--host", "0.0.0.0", "--port", $ApiPort
)

try {
  # Frontend in the foreground — Ctrl+C here stops the UI, then `finally` stops the API.
  Write-Host "==> Starting frontend UI  -> http://localhost:$UiPort"
  uv run streamlit run src/stratpoint_rag/ui/app.py `
    --server.port $UiPort --server.address 0.0.0.0 --server.headless true
}
finally {
  Write-Host "`n==> Shutting down backend (pid $($api.Id))..."
  if ($api -and -not $api.HasExited) {
    # /T kills the whole process tree (uv -> python -> uvicorn workers).
    taskkill /PID $api.Id /T /F | Out-Null
  }
}
