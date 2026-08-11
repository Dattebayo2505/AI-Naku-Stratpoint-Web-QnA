# Code review — `src/stratpoint_rag/` — 2026-08-11

Scope: `agent/`, `api/`, `docparse/`, `pdf_gen/`, `prompts/`, `rag/`, `ui/`,
`evaluation/`, `currency_calculator.py`. Excluded by request: `disambiguation/`,
`guardrails/`, `llmops/`.

Items 1–5 were reproduced by running the code; the rest are from reading.

## Status — all 14 fixed

Fixed in two passes: **1, 2, 3, 4, 5, 7** (the capture bug and the currency/rate
cluster), then **6, 8, 9, 10, 11, 12, 13, 14**.

Every fix carries a regression test, and every one of those tests was confirmed
to **fail against the pre-fix source** (`git stash push -- src/`, run, pop) so
none of them is a test that would have passed either way — 22 assertions failed
on the old code in pass one, 19 in pass two.

Suite: **872 passed → 934 passed** (+62 tests), same 2 pre-existing failures
throughout (`test_count_tokens.py`, missing `count_tokens` module, unrelated to
this review and failing before any change here).

Two notes on the shape of the fixes:

- `EstimationResult.total_cost_usd` keeps its misleading *name*. It now carries
  a sibling `currency_code` and every reader honours it, so the wrong-currency
  claim is gone, but renaming the field would ripple through stored payloads and
  callers for no additional correctness. Follow-up, not a defect.
- Item 8 needed a new endpoint, `GET /upload/{id}/transcription`, plus
  `api_client.fetch_transcription`. The UI now fetches the artifact instead of
  stat-ing a path that belongs to the API host — the same rule proposals already
  followed. It also loads lazily, inside the expander, so a Streamlit rerun does
  not re-fetch a document nobody opened.

One caveat worth recording: for item 6 the *status code* was already 413 before
the fix, because `save_upload` raised after the read. The test therefore asserts
that the request never reaches the storage layer at all — asserting the status
alone would have passed against both versions.

Repro scripts used are in the session scratchpad (`repro_capture.py`,
`repro_money.py`, `repro_money2.py`, `repro_est.py`) — throwaway, not part of
the repo.

---

## 1. The agent path's capture sinks are blanked before they are read

**`agent/guardrail_agent.py:310–329`** (with `agent/react.py:511`, `610–611`)

`run_with_guardrails` calls `agent_tools.begin_capture()`, then `run_agent()`.
`run_react` calls `begin_capture()` again and, in its `finally`, `end_capture()`
— which sets the contextvars to `None`. Both functions run in one context, so
the outer `captured_chunks()` / `captured_grounded()` calls that follow always
return empty.

Verified — the tool really did retrieve a chunk, and the outer read still came
back zero:

```
answer            : done.
outer chunks      : 0   <-- guardrails' source_chunks
outer grounded    : 0   <-- sets is_grounded/confidence
proposal_data kept: True
```

Consequences, all silent:

- `source_chunks` is `[]` for `_run_output_guardrails`, so the hallucination
  check has no source to verify the answer against. The code comment at
  `guardrail_agent.py:308` states this is exactly what the capture exists to
  prevent.
- `result.is_grounded` / `result.confidence` are never set on any agent turn —
  the debug panel and `/metrics` see `None`.
- `_escalation_for_answer` only acts on `is_grounded is False`, so the
  clarify-streak hand-off is unreachable on the agent path.

`proposal_data` is unaffected: `_finish` reads it inside `run_react`'s `try`,
before the reset.

**Suggested edit:** make the capture re-entrant (nest a depth counter, or have
`end_capture` restore the previous token from `ContextVar.set`), or drop the
outer `begin_capture`/`end_capture` in `guardrail_agent` and have `run_react`
return the captured chunks on `AgentResult`.

---

## 2. Tech-stack rate lookup matches short keys as substrings of free text

**`currency_calculator.py:138–143`**

`if key in h_clean` matches `HANDBOOK_STACK_RATES_PHP` keys anywhere inside a
feature string. The keys include `"ai"`, `"ml"`, `"go"`, `"php"`. `tech_hints`
in `agent/tools.py:470` is `payload.features + payload.target_platform` — free
text from the model.

Verified, all for **UI/UX Designer**, whose own handbook rate is ₱2,100.00:

| feature hint | rate returned |
|---|---|
| `Email notifications` | ₱3,625.00 (AI/ML) |
| `Domain registration` | ₱3,625.00 (AI/ML) |
| `Google Maps integration` | ₱3,567.00 (Go) |
| `HTML export` | ₱3,625.00 (AI/ML) |
| `Plain CRUD forms` | ₱3,625.00 (AI/ML) |

`Plain` contains `ai`; `Email` contains `ai`; `HTML` contains `ml`; `Google`
contains `go`. A plain CRUD website is billed at the senior AI/ML rate — a 73%
overcharge on that role.

Compounding it: the stack hint is checked **before** the role
(`lookup_handbook_rate:135–146`), and the same hint list is passed for every
role, so one accidental match sets an identical rate for the Tech Lead, the
engineer, QA and the designer at once.

**Suggested edit:** match on tokenised, normalised hints with word boundaries
(`re.findall(r"[a-z0-9.+#]+", h.lower())` then exact-match against the key set),
and require the role rate to win unless the hint is an actual stack token.

---

## 3. `detect_currency` reads "PHP" the language as Philippine pesos

**`docparse/extract.py:77–104`**

`_PHP_PATTERN` includes `\bPHP\b` case-insensitively. `php_matches > 0` returns
immediately, before USD is even counted. Verified:

```
'Backend must be PHP 8.2 with Laravel.'       -> PHP
'Budget is $250,000 USD. Stack: PHP/Laravel.' -> PHP   <-- explicit USD loses
'Budget is $250,000 USD.'                     -> USD
```

The second row is the damaging one. `extract_requirements:387` runs this over
the **whole brief**, and `pdf_gen/mapping.py:335` uses the result as the quote's
currency — which then drives the ×60 conversion in `_line_items`. A US client
whose RFP names a PHP/Laravel backend gets a peso-denominated quote built from
USD rates multiplied by 60.

Note `HANDBOOK_STACK_RATES_PHP` itself has a `"php"` key for the language, so
the codebase already treats the token both ways in two adjacent modules.

**Suggested edit:** require a currency context for the bare code — `₱`, or
`PHP` adjacent to a number/amount — and let an explicit `$`/`USD` count rather
than short-circuiting on the first PHP hit. Compare match counts instead of
returning on the first.

---

## 4. `total_cost_usd` carries a non-USD amount, and the loop is told it is USD

**`agent/tools.py:536–541`, `agent/tools.py:761–768`, `agent/contracts.py:105`**

`estimate_cost_and_timeline` computes in `target_currency` (from
`requirements.currency_code`) but stores the result in a field named
`total_cost_usd`, described as "Total estimated cost in USD".
`_wrap_estimate_cost_and_timeline` then formats it as `$… USD`.

Verified — one Observation, two currencies, 60× apart:

```
Estimation Results:
- Summary: Handbook-Based Estimate: 6.6 weeks duration for a total
           investment of PHP 1,379,994.00.
- Duration: 6.6 weeks
- Total Cost: $1,379,994.00 USD
- Role Breakdown: Tech Lead / Solutions Architect ($295,713), ...
```

The system prompt instructs the loop to "summarize cost … in your final Answer",
so this is the number the visitor is quoted in chat. The PDF is separately
re-derived by `mapping.py` and may disagree with the chat answer.

`RoleBreakdownItem.hourly_rate` / `.total_cost` carry the same mislabel.

**Suggested edit:** rename to `total_cost` and add a `currency_code` field to
`EstimationResult` (`calculate_role_rate` already returns the code — `tools.py`
discards it into `_`), then format the wrapper string from that field.

---

## 5. Nearly every proposal is titled "Artificial Intelligence"

**`pdf_gen/mapping.py:234`**

`has_ai = any(k in all_text for k in ("ai", "ml", ...))` — bare substring, and
`all_text` includes **the entire brief markdown**, which `infer_project_title`
reads off disk at line 226–232. `has_ai` is tested first, at line 245, so it
pre-empts every other category. Verified:

```
['Email notifications']  -> Artificial Intelligence - AI/ML Engineering & Model Solutions
['Domain and hosting']   -> Artificial Intelligence - AI/ML Engineering & Model Solutions
['HTML templates']       -> Artificial Intelligence - AI/ML Engineering & Model Solutions
['Plain contact form']   -> Artificial Intelligence - AI/ML Engineering & Model Solutions
['Shopping cart', ...]   -> Software Services - E-Commerce Web Application
```

Any brief containing "email", "domain", "maintain", "detail", "available",
"chain" or "main" anywhere in its text lands in the AI category. The e-commerce
case only escaped because its words happen to contain none of them.

**Suggested edit:** tokenise `all_text` once and match whole words; consider
dropping the whole-document read (it makes the title depend on prose the
estimate never saw) or restricting it to the structured fields.

---

## 6. Upload is fully buffered in memory before the size limit is checked

**`api/app.py:144`, with `docparse/store.py:120–125`**

`data = file.file.read()` materialises the entire upload, and `UploadTooLarge`
is only raised later inside `save_upload`. On the 6 GB LXC target a single
oversized POST can exhaust memory before the guard it is supposed to hit.

**Suggested edit:** check `file.size` (Starlette populates it) before reading,
or read in bounded chunks and abort past `config.upload_max_bytes()`.

---

## 7. Line-item currency is inferred from the number's magnitude

**`pdf_gen/mapping.py:120–124`, `:141–144`**

```python
if target_currency == "PHP" and role.hourly_rate < 500:
    rate = convert_currency(role.hourly_rate, "USD", "PHP")
elif target_currency == "USD" and role.hourly_rate >= 500:
    rate = convert_currency(role.hourly_rate, "PHP", "USD")
```

The rate's actual currency is known upstream — `calculate_role_rate` returns
`(rate, code)` — but the code is discarded, so this re-guesses from size. It
happens to work for the current handbook numbers; it breaks for any USD rate at
or above 500/hr (divided by 60) or any PHP rate below 500/hr (multiplied by 60).

The fallback path at `:143` additionally keys on `"PHP" in estimation.summary`,
i.e. on free-text prose, and on a `>= 100000` magnitude test.

**Suggested edit:** carry the currency code with the amount (see item 4) and
convert on the declared code, never on magnitude.

---

## 8. The UI reads the transcription path off the local filesystem

**`ui/app.py:138–141`**

```python
path = attachment.get("markdown_path")
if path and os.path.isfile(path):
    with st.expander("View transcription"):
        st.markdown(open(path, encoding="utf-8").read())
```

`markdown_path` comes from the API's `ParseResponse` and is a path on the **API
host**. `CLAUDE.md` states the rule for proposals — "The UI fetches over HTTP and
never reads `pdf_path` off disk — `STRATPOINT_API_URL` explicitly supports
running Streamlit against the LXC" — and this is the same situation for the
transcription. When UI and API are on different hosts (the documented deployment
shape) `os.path.isfile` is False and the expander silently never renders.

Minor, same line: `open(...)` is never closed.

**Suggested edit:** add a `GET /upload/{id}/transcription` endpoint and fetch it
through `api_client`, matching how proposals are already served.

---

## 9. `_memories` grows without bound

**`agent/guardrail_agent.py:20, 89–93`**

One `ConversationMemory` per session id, inserted on first use and only ever
removed by an explicit `clear_memory` (i.e. the user pressing "Reset
conversation"). On a long-lived uvicorn on the LXC, every abandoned browser tab
leaks one entry permanently. The same module's neighbours (`docparse/store.py`,
`pdf_gen/store.py`, `docparse/extract._CACHE`) all bound themselves; this does
not.

**Suggested edit:** bound it the way `extract._CACHE` is bounded (cap +
insertion-order eviction), or attach a timestamp and drop it in the existing
sweep.

---

## 10. An upload can be named `meta.json` or `transcription.md`

**`docparse/store.py:130–141`**, with `:201`

`_safe_filename` prevents traversal but not collision with the two reserved
names in the same directory. A file named `transcription.md` is written to the
exact path `save_transcription` uses, so `resolve_briefs:201` sees
`transcription_path.is_file()` and marks the brief `transcribed` — hop 1 never
ran. `read_brief` then serves the raw upload as if it were a transcription, and
`extract_brief`'s `FileNotFoundError` guard is bypassed, with all-zero
provenance. A file named `meta.json` is overwritten by the metadata write two
lines later, leaving `record.path` pointing at the JSON.

Self-inflicted only (it is the user's own session and own file), so this is
correctness, not a boundary break.

**Suggested edit:** store the payload under a fixed name (`source<ext>`) with
the original in `meta.json`, or reject/rename the two reserved names.

---

## 11. The extraction cache is keyed by file hash but carries a session path

**`docparse/extract.py:432–471`**

`_CACHE` is keyed on `brief.sha256` alone. The cached `ExtractedRequirements`
carries `source_markdown_path`, which is
`data/uploads/<session_id>/<upload_id>/transcription.md` for whichever session
extracted first. A second session uploading identical bytes gets the first
session's path back — and `mapping.py:226` and `:338` both `read_text()` that
path.

The content is identical by construction (same sha), so this is not a
disclosure of different data; but it does mean one session's quote reads a file
under another session's directory, which the sweep may delete underneath it.

**Suggested edit:** key on `(session_id, sha256)`, or drop
`source_markdown_path` from the cached value and re-stamp it per call.

---

## 12–14. Smaller items

- **`agent/guardrail_agent.py:341–347`** — `m["slug"]` raises `KeyError` on any
  metadata row lacking the key, in an unguarded block on the non-agent path.
  Use `m.get("slug")` and filter. Same pattern at `rag/store.py:39, 43`
  (`m["content_hash"]`, `m["slug"]`).
- **`rag/answer.py:39`** — `_strip_code_fences` does `s.strip()` with no `(s or "")`
  guard, unlike its twin at `docparse/extract.py:150`. A NIM reply with
  `content: null` raises `AttributeError`, which the surrounding `except` turns
  into `answer=None`; on the non-agent path that then fails
  `AgentResult` validation and surfaces as a 502.
- **`agent/guardrail_agent.py:361–371`** — when an output rail blocks and
  `config.mode != "fail_closed"`, the answer is returned unchanged and
  `result.guardrail_reason` is never set, so a fail-open block leaves no trace
  in the response or in `/metrics`.

---

## Suggested order of work

1. Item 1 — it disables output verification on every agent turn.
2. Items 2, 3, 4, 7 — one connected cluster: currency and rate selection. Fixing
   4 (carry the currency code) makes 7 straightforward, and 2 and 3 are both
   "substring match where a token match was meant", as is 5.
3. Item 6 before the LXC deployment.
4. The rest as cleanup.

## Note on how this review ran

`/code-review ultra` was launched first and did not produce these findings — 7
of its 8 agents terminated on a monthly spend limit, and the workflow reported
"no findings survived verification" because no finder ever reported. That result
should not be read as a clean bill of health. This review was then done inline.
