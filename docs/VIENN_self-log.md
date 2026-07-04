# Vienn — Self Log

My personal working log. Detailed and technical (unlike the general log). Newest entry on top.
Modules I own: **RAG** (retrieval) and **Dockerization**.

---

## 2026-07-04

### Switched generation from local Ollama → NVIDIA cloud endpoint

Killed the local-model dependency for `answer()`. gemma4:e2b was too big for the LXC host,
so generation now hits the NVIDIA-hosted NIM (OpenAI-compatible `/v1/chat/completions`)
instead of Ollama's `/api/generate`. No new dep — still the one `httpx.post`.

**Changes:**
- `rag/answer.py` — POST to `{NVIDIA_BASE_URL}/chat/completions` with `Bearer` auth,
  single user message, parse `choices[0].message.content`. Guards on missing key
  (`RuntimeError`). Skipped `enable_thinking` — grounded answer wants clean text, not CoT.
- `rag/config.py` — `llm_provider` default `ollama`→`nvidia`, `llm_model`→`google/gemma-4-31b-it`;
  dropped `ollama_host()`, added `nvidia_base_url()` + `nvidia_api_key()`.
- `.envexample` — `OLLAMA_HOST` → `NVIDIA_BASE_URL` / `NVIDIA_API_KEY` (blank). Real key in
  gitignored `.env`.
- `RAG-UnitTests/test_answer.py` — respx now mocks the NVIDIA URL + `choices[...]` shape;
  added missing-key sad path. **33 passed** (RAG-UnitTests).

**Gotcha:** nothing auto-loads `.env` — must `export NVIDIA_API_KEY` (or `set -a; source .env`)
per shell before `answer()` sees it.

**Open / next:** implement Dockerization module; cloud route means the compose `ollama` profile
is now optional-only (no longer the default generation path).

### 01:36 PST — QA + audit pass, applied fixes, suite fully green (81 passed)

Ran a ponytail over-engineering audit + a high-recall correctness QA over the RAG work.
Verdict: module is lean (423 LOC for a full pipeline). Applied the clear wins and the
correctness fixes; **`uv run pytest` now 81 passed, 1 deselected** (crawler + RAG-UnitTests).

**Cleanup wins applied:**
- Deleted duplicate `tests/test_rag_ingest.py` (superseded by `RAG-UnitTests/test_ingest.py`).
- Added `RAG-UnitTests` to `testpaths` — the RAG suite now runs on a plain `uv run pytest`
  (previously silently skipped).
- Implemented **skip-and-warn** in `load_pages` (missing `.md` → `log.warning` + skip, no crash).
  **This supersedes the 00:43 note** — the 2 previously-red tests are now green.

**Correctness fixes (found in QA):**
- **Missing-file eviction bug:** skip-and-warn dropped a missing page from `present`, so the
  ingest removal loop *deleted* that page's existing embeddings. Fixed by deriving `present`
  from the **manifest slugs** (all `ok`/`skipped` rows), not from successfully-loaded pages —
  a transiently-missing `.md` is now skipped from re-embed but NOT evicted. Added regression
  test `test_ingest_transient_missing_md_keeps_existing`. (Empty-corpus wipe behavior preserved.)
- **`upsert_page` IndexError** on empty chunk list → added `if not chunks: return` guard.

**Left as known trade-off:** chunker `size` isn't a hard cap — overlap prepend can push a chunk
past bge's 512-token window (tail truncated). Documented via `ponytail:` comment; low impact.

**Open / next (unchanged):** implement Dockerization module; decide deployment LLM (smaller
local model vs cloud) since gemma4:e2b is too big for the LXC host.

### 01:00 PST — ✅ Full RAG pipeline working end-to-end (retrieval + grounded answer)

Got `answer()` working end-to-end for the first time. Query → bge embed (server) → Chroma
retrieval (server) → generation on my Windows PC's Ollama → grounded answer **with a correct
source citation, no hallucination**.

- Q: "Does Stratpoint do mobile app development?"
- A: "Yes, Stratpoint develops mobile apps... Sources used: https://stratpoint.com/mobileappdev/"
- Latency: **~129s** (gemma4:e2b on the PC's CPU — functional but slow; a ~2GB model on the
  GTX 1650 GPU would be far faster later, optimization not a blocker).

**What unblocked it:** PC Ollama upgraded to 0.31.1 (fixed the GGML crash); GPU can't fit the
7.2GB model in the GTX 1650's 4GB VRAM, so forced CPU on the PC via `CUDA_VISIBLE_DEVICES=-1`.

---

#### 🏃 RUNBOOK — how I run the project from now on (until stated otherwise)

**Generation runs on my Windows PC; retrieval + code stay on the server.**

Prereqs on the **Windows PC** (once per boot):
- Ollama running, bound to the LAN: `OLLAMA_HOST=0.0.0.0:11434` (persistent), CPU forced via
  `CUDA_VISIBLE_DEVICES=-1` (persistent), firewall port 11434 open.
- Model present: `gemma4:e2b`. Verify from server: `curl http://<PC-LAN-IP>:11434/api/version`.

On the **server** (this box), each shell session:
```bash
cd ~/School/AI-Naku-Stratpoint-Web-QnA
export OLLAMA_HOST=http://<PC-LAN-IP>:11434        # PC over LAN (Tailscale alt: <PC-TAILSCALE-IP>)
export TOKENIZERS_PARALLELISM=false                   # silence HF warning

# ask a question end-to-end:
uv run python -c "from stratpoint_rag.rag.answer import answer; print(answer('YOUR QUESTION', k=3))"

# retrieval only (fast, no LLM needed):
uv run python -c "from stratpoint_rag.rag.retrieve import retrieve; [print(c.score, c.url) for c in retrieve('YOUR QUESTION')]"
```
Notes: `OLLAMA_HOST` must be exported per shell (nothing auto-loads `.env`). Expect ~2min/answer
on CPU. If `answer()` errors with connection refused → PC Ollama isn't running/reachable.

---

### 00:43 PST — RAG unit-test suite + LLM integration attempt (blocked on hardware)

**Unit tests (`RAG-UnitTests/`):**
- Planned a happy/sad-path test list, audited it, then built 8 test files + `conftest.py`
  (shared `FakeEmbedder` + tmp Chroma fixtures). 31 tests total.
- Result: **29 passed, 2 failed** — both failures are the `missing .md` tests, which assert
  the **skip-and-warn** behavior I chose but haven't implemented yet (current code crashes with
  `FileNotFoundError`). Left red on purpose per instruction (no fixes yet).
- Two behaviors pinned by tests via explicit decisions: missing `.md` → **skip-and-warn**
  (TARGET, not yet coded); empty corpus → **wipe the store** (accepted current behavior).
- Ran via `uv run pytest RAG-UnitTests`; not added to `testpaths` (default `uv run pytest`
  won't pick it up). Original `tests/test_rag_ingest.py` still passes; some overlap now.

**LLM integration (`answer()` end-to-end) — attempted, not yet proven:**
- Installed Ollama on the server, model `gemma4:e2b` present (7.2 GB).
- **Blocked by server hardware:** no GPU, **7.5 GB total RAM**, model needs ~6.8 GB resident →
  it doesn't fit → swap-thrashing. Measured: cold load + one word = **6m33s**; full grounded
  answer exceeded a ~10-min timeout. Diagnosis: RAM/hardware, not the RAG code.
- Bumped `answer()` httpx timeout 120s → 600s (throwaway-path tolerance for slow CPU).

**Solution in progress — offload generation to my Windows main PC (no code change):**
- `answer()` reads `OLLAMA_HOST` from config, so pointing it at the PC offloads *generation*
  while retrieval (bge + Chroma) stays on the server. Nice payoff of the config seam.
- Server (`<SERVER-LAN-IP>`, also Tailscale `<SERVER-TAILSCALE-IP>`) reaches PC over LAN
  (`<PC-LAN-IP>`) and Tailscale (`<PC-TAILSCALE-IP>`). PC Ollama set to bind `0.0.0.0:11434`,
  firewall opened. Connection confirmed (`/api/version` OK).
- **Current blocker:** PC runs Ollama **0.30.7**, which **crashes loading the model**
  (`GGML_ASSERT(n_inputs < GGML_SCHED_MAX_SPLIT_INPUTS)`, exit `0xc0000409`). Fix: upgrade PC
  Ollama to ≥0.31.1 and retry. GPU usage still unconfirmed (model never finished loading).

**State:** retrieval fully proven; `answer()`/generation still **not** demonstrated end-to-end,
pending the PC Ollama upgrade. `EMBEDDING_MODEL` default clarified as bge-small-en-v1.5.

**Open / next:**
- Upgrade PC Ollama → re-run `answer()` via `OLLAMA_HOST=http://<PC-LAN-IP>:11434`; confirm
  it generates and whether it uses the PC GPU.
- Implement `load_pages` skip-and-warn to turn the 2 red tests green (awaiting my go-ahead).
- Note for deployment: `gemma4:e2b` is too big for the current LXC/host RAM — revisit model
  choice (smaller local model) or the cloud route before relying on local generation.

## 2026-07-03

### 21:27 PST — Built the RAG module (code + real ingest + eval), Dockerization deferred

Implemented the RAG module per `docs/plan-rag-dockerization.md`. Held off on Dockerization
per instruction. Files under `src/stratpoint_rag/rag/`: `loader`, `chunker`, `embeddings`,
`store`, `retrieve`, `ingest`, `answer`, `config`, `models`, plus `eval/` (gold set + hit@k).

**Implementation decisions made during the build:**
- **Chunk size vs embedding window (correctness fix):** plan said ~800 tokens, but the local
  models truncate below that (MiniLM 256, bge-small 512). Chose **bge-small-en-v1.5** and sized
  chunks to **~450 tok / 80 overlap** (implemented as ~1600/300 chars) so nothing is silently
  truncated. Char-budgeted split with bge's 512-token truncation as a hard backstop (marked with
  a `ponytail:` comment; upgrade to token-exact only if eval shows truncation).
- **Embedder as the swap seam:** `Embedder` protocol; local bge wired, cloud route (Google/NIM)
  stubbed behind the same interface (YAGNI until the group picks it). Embeddings passed to Chroma
  explicitly so nothing downloads implicitly and the seam stays single-source-of-truth.
- **Chroma:** embedded `PersistentClient`, cosine space, `content_hash` stored per chunk for
  hash-gated re-ingest; removed pages carried out of the store.
- **Boundary (Option C):** `retrieve(query, k)` is the real seam; `answer()` written as clearly
  marked throwaway scaffolding (thin Ollama call via httpx — no new dep).
- **Build scope:** chose full build + real ingest in-environment (added `chromadb` +
  `sentence-transformers`, ran the actual embed).

**What I verified (evidence):**
- Offline tests 4/4 (hash-gating proven: re-ingest → skipped=all, added=0); full suite **53 passed**.
- Real ingest: **370 pages → 1464 chunks** in Chroma.
- Live retrieval on both spec questions returns correct Stratpoint pages + source URLs
  (`/retail/`, `/mobileappdev/`, SM Malls retail app, etc.).
- **hit@5 = 0.80** (4/5; the one miss is a strict gold label, not a bad retrieval).

**Housekeeping:** registered `stratpoint-rag-ingest` console script; gitignored `chroma_db/`
+ `ingest.log`; mirrored new env vars into `.envexample` (blank, kept `LCX_` spelling).

**Open / caveats:**
- `answer()` untested here — Ollama not running (`localhost:11434` down). `retrieve()` (the real
  deliverable) fully proven.
- hit@5 gold set is a 5-question seed — expand for a stronger eval/presentation story.
- Dockerization module still not started (deliberately deferred).

### 19:02 PST — Set interim default LLM = gemma4:e2b (provisional)

Got word to pick an interim default while the group decides cloud vs local. Defaulting the
**generation LLM to local Ollama `gemma4:e2b`** (embeddings stay on the local
`sentence-transformers` default). **This is provisional — only permanent until I say otherwise.**

Updated `docs/plan-rag-dockerization.md` in four spots, all marked provisional:
- §3.2 — "Interim default" callout.
- §3.6 — `LLM_PROVIDER=ollama`, `LLM_MODEL=gemma4:e2b`.
- §4.4 — ollama profile comment.
- Decision Log #1 — status = "interim default set until Vienn says otherwise."

Because the LLM sits behind the env-switched interface, flipping to cloud (or another model)
later is a config change, not a rewrite. Decision #1 remains officially **undecided** at the
group level.

### 18:59 PST — Planning session for RAG + Dockerization modules

Ran a grilled Q&A with Claude to lock down how my two modules should be built, then had it
write the planning doc: `docs/plan-rag-dockerization.md`.

**Decisions I made this session:**

| # | Decision | Choice | Status |
|---|----------|--------|--------|
| 1 | Generation LLM host | Document **both** cloud (NVIDIA NIM / Google AI Studio) and local (Ollama + gemma) routes | Group undecided — both in plan |
| 2 | Vector store | **Chroma**, embedded (in-process, not a server) + persisted to a bind-mounted volume | Locked |
| 3 | Embeddings provider | Interface with **local default** (`sentence-transformers`, e.g. all-MiniLM-L6-v2) + **cloud** route, env-switched | Both documented |
| 4 | Chunking | Recursive character split, ~800 tokens / ~100 overlap, carry page `url`+`title` as metadata | Locked |
| 5 | Retrieval | Top-k (k=4–6) cosine similarity + return source URLs so answers cite stratpoint.com | Locked |
| 6 | RAG boundary (vs teammates' agent) | **Option C** — expose `retrieve(query, k)` as the real seam; keep a thin **throwaway** `answer()` only for standalone eval/demo before the agent exists | Locked |
| 7 | Crawler in Docker image | **No** — Playwright/Chromium excluded; corpus is produced offline, app only reads `data/` | Locked |
| 8 | Ingestion timing | **Option 3** — auto-ingest on container startup (entrypoint), backed by a standalone `content_hash`-gated `ingest` command; warm the volume once at setup so demo boot is fast | Locked |
| 9 | Docker Compose topology | Recommended `app + optional ollama + optional mlflow` profiles (Chroma stays embedded, not a service) | **Deferred to integration** |

**Reasoning notes (why, not just what):**
- **Chroma over FAISS / Qdrant** — embedded + persistent, no separate container, trivial on the
  Proxmox LXC; 371 pages doesn't justify a server DB. Qdrant/pgvector noted as a Final-Capstone
  upgrade path.
- **Embeddings vs generation are separate models** — same embedding model MUST be used for
  ingestion and queries (switching = full re-embed). Kept local as default so ingestion is
  self-contained even if generation is cloud.
- **Option C boundary** — `retrieve()` is the permanent wall socket the ReAct agent (teammate)
  plugs into; `answer()` is just a lamp I plug in to confirm the socket has power. Marked
  throwaway so it doesn't silently grow into a second agent and blur ownership.
- **Option 3 ingestion** — slow first-boot only happens once on an empty volume; if I warm it
  during setup, demo-day boot is instant. Same `ingest` code either way, so it's really just an
  entrypoint wrapper. Avoids the live-demo footgun the rubric penalizes.
- **Crawler out of the image** — keeps the image lean (no ~heavy Chromium); matches CLAUDE.md's
  rule that `stratpoint_rag` must not import from `stratpoint_crawl`.

**Config plan (env-switched routes):** `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `LLM_PROVIDER`,
`LLM_MODEL`, `CHROMA_DIR`, plus API keys — all mirrored blank in `.envexample` (keep `LCX_` spelling).

**Artifacts produced:** `docs/plan-rag-dockerization.md` (design + decision log + build order + rubric talking points).

**Next up for me:**
- Build order steps 1–5 (loader → chunker+embedder+Chroma → hash-gated `ingest` → `retrieve()` →
  thin `answer()` + tiny hit@k eval) — all independent of teammates.
- Resolve with the group: which LLM/embedding provider (decision #1 and #3).
- Finalize Compose topology (#9) once the app is assembled.
