# Deploying to a 6 GB / 2-core Proxmox LXC (no Docker)

Setup guide for running the Stratpoint RAG chatbot on a constrained Linux container:
**6 GB RAM, 2 CPU cores, Docker not available.**

## Why this fits at all

The LLM is **cloud-hosted** (NVIDIA NIM — `rag/answer.py` calls `integrate.api.nvidia.com`),
so **no model weights load into your 6 GB**. The only heavy local process is the API, which
loads:

- `torch` + `sentence-transformers` (the local `bge-small` embedder) — the biggest RAM user
- `ChromaDB` (embedded, persistent vector store)
- optionally **NeMo Guardrails** — skip it (Step 4) to save ~0.5–1 GB

Worst case ~3.5–5 GB total; comfortable once NeMo is off and torch is CPU-only. Docker was
only ever needed for the *local-model* plan, which this deployment does not use.

> **Platform note:** these are Linux commands for the LXC. Development happens on Windows,
> but nothing below runs on the dev machine except the crawl (Step 5a).

---

## Step 0 — Prerequisites (required)

On the LXC you need: internet egress (to reach NVIDIA + download deps and the embedder
model on first run), a `NVIDIA_API_KEY`, Python 3.13, and `git`.

```bash
sudo apt update
sudo apt install -y git curl build-essential
sudo apt install -y libreoffice-impress   # required for .pptx uploads — see below
python3 --version   # must be 3.13.x; install via deadsnakes/pyenv if not
```

> **`libreoffice-impress` is the largest single package in this deployment**, and
> it is the reason the original document-parser design rejected deck support
> outright. That call was reversed on 2026-08-14: a client brief arrives as a
> deck often enough, and the alternative (`python-pptx`) is text-only — it would
> miss every architecture diagram, which is where the requirements live.
>
> `libreoffice-impress`, not the `libreoffice` metapackage: it pulls the
> presentation filters and the headless core without Writer, Calc and Base.
>
> Measure it on the box rather than trusting a number in a doc — it moves
> between Debian releases:
>
> ```bash
> dpkg-query -Wf '${Installed-Size}\t${Package}\n' | sort -rn | head -20
> ```
>
> If you will never accept `.pptx` on this box, you can skip it: PDF and image
> uploads never spawn LibreOffice, and `/upload` answers **503** with an
> actionable message when a deck arrives and the binary is absent.

## Step 1 — Get the code onto the box (required)

```bash
git clone <your-repo-url> stratpoint-rag
cd stratpoint-rag
```

## Step 2 — Install uv (required)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env      # or restart the shell
uv --version
```

## Step 3 — Force the CPU-only torch wheel (optional, strongly recommended)

By default `sentence-transformers` pulls the CUDA build of `torch` on Linux — multiple GB of
GPU libraries you cannot use, inflating disk and RAM. Pinning torch to the CPU index takes
**two** edits to `pyproject.toml`, and both are required — doing only one silently leaves you
on the CUDA wheel (`torch.__version__` ends in `+cuXXX` instead of `+cpu`):

1. **Declare `torch` as a direct dependency.** In this project torch is only a *transitive*
   dependency (pulled in by `sentence-transformers`), and `uv`'s `[tool.uv.sources]` mapping
   below is **only applied to packages you list in your own `[project.dependencies]`**. If
   torch isn't listed there, uv resolves it straight from PyPI and grabs the CUDA wheel — the
   index redirect never fires. This is the step that's easy to miss.
2. **Add the CPU index + source mapping** (the two tables) so the declared torch resolves from
   the CPU wheel index.

**3a. Add `torch` to the `dependencies` list.** Open `pyproject.toml` and add a `torch` line
inside the `dependencies = [ ... ]` array (any version — the *index*, not the version, is what
selects CPU vs CUDA):

```toml
dependencies = [
    # ... existing deps ...
    "sentence-transformers>=3.0.0",
    "torch>=2.2",
    # ... rest ...
]
```

Or do it non-interactively — this inserts the line right after `sentence-transformers`:

```bash
sed -i '/"sentence-transformers>=3.0.0",/a\    "torch>=2.2",' pyproject.toml
```

**3b. Add the CPU index tables at the very END of the file.** These are **top-level tables** —
they must NOT go inside any existing `[table]` or `[[table]]` section, or they silently become
sub-keys of whatever section they land in. The safe place is the very bottom, after everything
else.

Open the file and go to the bottom:

```bash
nano pyproject.toml
```

In `nano`, press `Ctrl+End` (or `Alt+/`) to jump to the end of the file.

Append these two blocks on new lines at the very bottom, then save (`Ctrl+O`,
`Enter`) and exit (`Ctrl+X`):

```toml
[tool.uv.sources]
torch = { index = "pytorch-cpu" }

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true
```

**Alternative — append without an editor.** This heredoc writes the same two blocks (with a
leading blank line so they don't glue onto the last existing line) to the end of the file:

```bash
cat >> pyproject.toml <<'EOF'

[tool.uv.sources]
torch = { index = "pytorch-cpu" }

[[tool.uv.index]]
name = "pytorch-cpu"
url = "https://download.pytorch.org/whl/cpu"
explicit = true
EOF
```

> Note the `>>` (append). Do **not** use a single `>`, which would truncate `pyproject.toml`
> to just these blocks and destroy the rest of the file.

**3c. Re-lock so the change takes effect.** If you already ran `uv sync` (or installed torch)
before these edits, the existing `uv.lock` still pins the CUDA wheel — `uv sync` alone won't
switch it. Force a re-resolve:

```bash
uv lock       # re-resolves torch against the CPU index now that it's a direct dep
```

Then Step 4's `uv sync` installs the CPU wheel. Verify after Step 4:

```bash
uv run python -c "import torch; print(torch.__version__)"   # should end in +cpu, not +cuXXX
```

## Step 4 — Install dependencies WITHOUT the NeMo extra (required)

Plain `uv sync` installs the base deps and **skips** the optional `nemo` group — exactly what
you want. Do **not** pass `--extra nemo`.

```bash
uv sync          # base deps only; NeMo Guardrails NOT installed
```

The app degrades gracefully: `guardrail_agent.py` catches the missing NeMo import and falls
back to the built-in regex + PII guardrails (near-zero cost). Later, force NeMo off per request
too (Step 8) so nothing tries to build the NeMo rails.

> Playwright's Chromium is **not** needed on this box — you are not crawling here (Step 5a).
> Skip `playwright install chromium`.

> **Document parsing adds one `apt` step, and only one.** `pymupdf`, `jinja2`,
> and `python-multipart` are pure pip wheels with no system packages behind
> them, so for PDFs and images `uv sync` is still the whole story. This is
> exactly why `pdf2image` was rejected for the document parser: it is a
> subprocess wrapper around **poppler-utils**, which would have meant a root
> `apt install` on this box. If you ever see a suggestion to swap PyMuPDF for
> `pdf2image`, that trade is what you would be paying.
>
> `libreoffice-impress` (Step 0) is the one exception, and it was accepted
> knowingly for `.pptx` support — a far larger install than poppler would have
> been. It buys a format PyMuPDF cannot open at all, which poppler would not
> have.

### Disk: uploaded briefs

Uploaded client briefs land in `data/uploads/` (gitignored). Three things keep
that from growing without bound on a container that may never reboot:

- the API wipes `UPLOAD_DIR` **on every boot** — restart the service and the
  directory is empty;
- each upload sweeps directories older than `UPLOAD_TTL_SECONDS` (default 3600);
- the UI's "✕" and "Reset conversation" delete immediately.

Worst case between sweeps is roughly `UPLOAD_MAX_BYTES` (default 25 MB) per
concurrent upload, so the practical ceiling is small. If disk is tight, lower
`UPLOAD_TTL_SECONDS` and `UPLOAD_MAX_BYTES` in `.env` rather than adding a cron
job — the sweep already runs on the only event that creates files.

> These briefs are **confidential client documents** on a box with no
> authentication (see Step 11). Prefer the SSH-tunnel access path if real client
> material will ever be uploaded.

## Step 5 — Provide the corpus and build the vector index (required)

### 5a. Crawl OFF the box (required, run on your dev machine)

Never run the crawler on the LXC — headless Chromium is ~0.5–1 GB per context. Crawl on your
laptop and copy only the corpus over:

```bash
# on the dev machine
uv run stratpoint-crawler --out ./data
scp -r ./data <user>@<lxc-host>:~/stratpoint-rag/data
```

### 5b. Get the Chroma index onto the box (required, one-time / on corpus change)

Pick **one** of the two paths below. Both leave a `chroma_db/` folder in the project directory;
the app reads it via the default `CHROMA_DIR=chroma_db`, resolved relative to wherever you
launch the API — so it must sit in the directory you `cd` into to run uvicorn (Step 8).

> **Embedder must match (applies to both paths).** Query-time and index-time embeddings have to
> come from the **same** model or retrieval silently returns garbage. If you leave
> `EMBEDDING_MODEL` unset everywhere you get the default (`BAAI/bge-small-en-v1.5`) on both
> sides and you're fine; if you set it, set the *same* value on the machine that builds the
> index and on the LXC.

#### Path A — Build locally, copy the finished index over (lighter on the LXC)

If you already have a working `chroma_db/` on your dev machine (from `uv run stratpoint-rag-ingest`
there), just copy it. This skips the embedder download and the torch-heavy ingest on the box —
and means you don't need to `scp` the `data/` corpus over at all.

```powershell
# from the project root on the dev machine (Windows PowerShell)
scp -P <ssh-port> -r ./chroma_db <user>@<lxc-host>:/home/<user>/<project-dir>/
```

- **`-P` is capital** for `scp` (lowercase `-p` is `ssh`'s port flag — a common mix-up).
- `<ssh-port>` / `<user>` / `<lxc-host>` are the same values from your `ssh -p ...` login;
  `<project-dir>` is the folder you cloned into (e.g. `AI-Naku-Stratpoint-Web-QnA`).
- **If a `chroma_db/` already exists on the box, delete it first** — `scp` merges into an
  existing folder and can leave stale files behind:
  ```bash
  rm -rf /home/<user>/<project-dir>/chroma_db     # on the LXC, before copying
  ```

Verify on the box after the transfer:

```bash
ls -la /home/<user>/<project-dir>/chroma_db     # expect chroma.sqlite3 + a UUID subfolder
```

#### Path B — Build the index on the box (needs the corpus from 5a)

Run ingest while the API is **not** running (avoid two torch copies). First run downloads the
`bge-small` embedder (~130 MB) into `~/.cache/huggingface`.

```bash
cd /home/<user>/<project-dir>
uv run stratpoint-rag-ingest        # embeds changed pages into ./chroma_db
# add --force to re-embed everything
```

## Step 6 — Configure `.env` (required)

Copy the committed template and fill it in. `.env` is gitignored.

```bash
cp .envexample .env
nano .env
```

Set at minimum:

```dotenv
# --- required: cloud LLM ---
NVIDIA_API_KEY=nvapi-xxxxxxxx

# --- recommended tuning ---
LLM_TIMEOUT=60          # default is 300s; a hung cloud call parks a worker thread that long

# --- optional overrides (defaults shown; leave unset to accept them) ---
# LLM_MODEL=meta/llama-3.1-8b-instruct
# NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
# EMBEDDING_PROVIDER=local
# EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
# CHROMA_DIR=chroma_db
# CHROMA_COLLECTION=stratpoint
```

Keep the existing container vars from `.envexample` (`PORT`, `PUBLIC_IP_ADDRESS`,
`LCX_*`, `NON_ROOT_*`) as they are used by your environment.

## Step 7 — Cap thread oversubscription (required on 2 cores)

`torch`/OpenMP default to grabbing every core and spinning per-thread memory arenas. Pin them.
Put these in `.env` **and** export them wherever the API launches (systemd `Environment=` below):

```dotenv
OMP_NUM_THREADS=2
OPENBLAS_NUM_THREADS=2
TOKENIZERS_PARALLELISM=false
```

## Step 8 — Run the API (required)

**One worker only.** Every extra uvicorn worker forks a full duplicate of the torch + Chroma +
model footprint (~1.5 GB each) — on 6 GB you get exactly one. Bind `0.0.0.0` so the UI (and
remote clients) can reach it. The API listens on **internal port 8000** (the host maps an
external port to it — see Step 11).

Run it via the **module form** (`python -m uvicorn`) — it works whenever the `uvicorn` package
is importable, even if its console script isn't on `PATH`:

```bash
uv run python -m uvicorn stratpoint_rag.api.app:app --host 0.0.0.0 --port 8000 --workers 1
```

> **Got `error: Failed to spawn: uvicorn` / `No such file or directory`?** uv couldn't find the
> `uvicorn` *console script* in the project venv — the environment isn't fully synced (common
> right after the Step 3 re-lock changes `uv.lock`). Sync once, then confirm the package imports:
> ```bash
> cd /home/<user>/<project-dir>        # the folder containing pyproject.toml
> uv sync                              # installs uvicorn + the rest into the venv
> uv run python -c "import uvicorn; print(uvicorn.__version__)"   # should print a version
> ```
> The `python -m uvicorn` form above then runs regardless of whether the bare `uvicorn` script
> made it onto `PATH`. (Plain `uv run uvicorn ...` also works once synced — the module form just
> sidesteps the PATH question.)

First request is slow (~10–30 s) — torch import + embedder load happen lazily on first call.
To avoid the cloud NeMo round-trips entirely, ensure callers send `use_nemo: false` (the UI's
`api_client` does not send it, so with NeMo uninstalled it already no-ops via ImportError).

Health check from another shell:

```bash
curl -s http://localhost:8000/health      # {"status":"ok"}
```

## Step 9 — Run the Streamlit UI (required)

The UI is a separate, lightweight process. Point it at the API with `STRATPOINT_API_URL`
(defaults to `http://localhost:8000`, fine if UI and API share the box).

> **Serve the UI on port `7860`, not Streamlit's default `8501`.** The Proxmox host forwards a
> public port to internal **`7860`** for the UI (see the port map in Step 11) — nothing forwards
> `8501`, so a UI left on the default is unreachable from outside. `7860` (the conventional
> "web UI" port) is what the host's NAT rule expects; matching it is the whole reason public
> access works without touching the host.

```bash
# same box as API:
uv run streamlit run src/stratpoint_rag/ui/app.py --server.port 7860 --server.address 0.0.0.0

# API elsewhere:
STRATPOINT_API_URL=http://<api-host>:8000 \
  uv run streamlit run src/stratpoint_rag/ui/app.py --server.port 7860 --server.address 0.0.0.0
```

Confirm it's listening on the right interface and port:

```bash
ss -tlnp | grep 7860        # want 0.0.0.0:7860 (or *:7860), not 127.0.0.1
```

Reach it at `http://localhost:7860` on the box, or publicly via the forwarded external port
(Step 11). The sidebar shows **API: Connected** when wired correctly.

> **UI loads but sticks on "Connecting…" through the public port?** Streamlit's websocket is
> being blocked by its CORS/XSRF checks behind the NAT hop. Re-run with them off:
> `--server.enableCORS false --server.enableXsrfProtection false`. Try *without* first — it
> usually isn't needed.

## Step 10 — Run as systemd services (optional, recommended for a persistent box)

So the API and UI survive reboots and detach from your SSH session.

`/etc/systemd/system/stratpoint-api.service`:

```ini
[Unit]
Description=Stratpoint RAG API
After=network-online.target
Wants=network-online.target

[Service]
User=<non-root-user>
WorkingDirectory=/home/<non-root-user>/stratpoint-rag
EnvironmentFile=/home/<non-root-user>/stratpoint-rag/.env
Environment=OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false
ExecStart=/home/<non-root-user>/.local/bin/uv run python -m uvicorn stratpoint_rag.api.app:app --host 0.0.0.0 --port 8000 --workers 1
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/stratpoint-ui.service`:

```ini
[Unit]
Description=Stratpoint RAG UI
After=stratpoint-api.service

[Service]
User=<non-root-user>
WorkingDirectory=/home/<non-root-user>/stratpoint-rag
EnvironmentFile=/home/<non-root-user>/stratpoint-rag/.env
Environment=STRATPOINT_API_URL=http://localhost:8000
ExecStart=/home/<non-root-user>/.local/bin/uv run streamlit run src/stratpoint_rag/ui/app.py --server.port 7860 --server.address 0.0.0.0
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now stratpoint-api stratpoint-ui
sudo systemctl status stratpoint-api
journalctl -u stratpoint-api -f          # tail logs
```

## Step 11 — Exposure & public access

### How reachability works on this box (NAT port-forwards)

The container sits **behind NAT on the Proxmox host** — it has a private IP, and the public IP
belongs to the host. The internet reaches a service only if the host has a **port-forward rule**
mapping a public (external) port to the container's internal port. Binding a process to
`0.0.0.0` inside the container is necessary but **not** sufficient; without a host forward, an
external packet has nowhere to go.

These forwards live **on the Proxmox host** (outside this repo). For this deployment:

| Public (external) port | → Internal (container) port | Service |
|---|---|---|
| `2127` | `22`   | SSH |
| `2147` | `7860` | Streamlit UI |
| `2167` | `8000` | API |

Run the UI on `7860` (Step 9) and the API on `8000` (Step 8) so they line up with the rules —
the internal port must match the right-hand column, or the forward lands on a dead port. (This
is why the default Streamlit `8501` is unreachable: nothing forwards to it.)

### Access it publicly

Point a browser at the **public IP + external port**:

- UI:  `http://<public-ip>:2147`
- API: `http://<public-ip>:2167` — only if external clients call the API directly. The UI itself
  reaches the API over internal `localhost:8000` and does **not** need this forward.

Test reachability from a machine on a **different network** (e.g. a phone hotspot — testing from
the same LAN as the host can give a false pass):

```powershell
# Windows PowerShell — capital -Port takes a NUMBER; use the EXTERNAL port
Test-NetConnection <public-ip> -Port 2147     # TcpTestSucceeded : True  == reachable
```

Reading the result:

| Result | Meaning |
|---|---|
| `True` + page loads | Working. |
| `False`, fast fail | Nothing serving the internal port — is the UI actually running on `7860`? |
| `False`, slow timeout | A firewall is dropping it (host/provider firewall, or container `ufw` below). |

`PingSucceeded : False` is normal (ICMP is usually blocked) — ignore it; only `TcpTestSucceeded`
matters.

If `ufw` is active on the container, open the **internal** ports it serves:

```bash
sudo ufw allow 7860/tcp     # UI  (reached publicly via external 2147)
# sudo ufw allow 8000/tcp   # API — only if exposing it publicly via 2167
```

### ⚠️ There is no authentication

Streamlit has **no built-in login**. `http://<public-ip>:2147` is open to anyone who finds it,
and every question they ask spends your `NVIDIA_API_KEY` quota. For a short-lived demo that's
acceptable; for anything longer put a reverse proxy (Caddy/nginx) with TLS **and** auth in front,
or take the UI down between sessions.

### Private access without exposing anything — SSH tunnel

If only *you* need the UI, skip public exposure entirely and forward the port over your existing
SSH connection:

```powershell
ssh -p 2127 -L 7860:localhost:7860 <user>@<public-ip>
```

Leave that open and browse `http://localhost:7860` on your own machine — the traffic rides the
encrypted SSH tunnel. No extra forward rule, no firewall change, nothing exposed to the internet.

## Step 12 — Verify end-to-end (required)

```bash
free -h                                   # confirm headroom; watch during a query
curl -s http://localhost:8000/health
# then ask a question in the UI and watch: `journalctl -u stratpoint-api -f`
```

Watch `free -h` during the first real query — that is the peak (torch + embedder + a Chroma
query). If resident memory approaches the ceiling, re-check Steps 3, 4, and 8.

---

## Quick reference

| Do | Don't |
|---|---|
| `uv sync` (base only) | `uv sync --extra nemo` |
| CPU-only torch (Step 3) | default CUDA torch wheel |
| `--workers 1` | scale uvicorn workers |
| Crawl on dev machine, `scp` the corpus | run `stratpoint-crawler` on the LXC |
| Run ingest once, then serve | ingest and serve at the same time |
| `LLM_TIMEOUT=60`, thread caps set | leave 300 s timeout / uncapped threads |
| UI on `7860` (matches host forward `2147`) | UI on default `8501` (unforwarded, unreachable) |
| `python -m uvicorn …` (PATH-proof) | bare `uv run uvicorn` on an unsynced venv |

**Required steps:** 0, 1, 2, 4, 5, 6, 7, 8, 9, 12.
**Optional (recommended):** 3 (CPU torch), 10 (systemd), 11 (exposure / public access).
