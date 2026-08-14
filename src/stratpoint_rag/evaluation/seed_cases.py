"""Populate cases/pipeline_runs.jsonl — the input both new eval layers read.

`trajectory` and `e2e` score recorded traces, but traces carry tool *names*, not
tool *outputs*: `llmops.record(tool_calls=...)` is a `list[str]`. The extraction
and cost layers need the values themselves, and uploads and proposals are both
purged on API boot, so there is nothing on disk to read afterwards either. This
script captures what the chat response already returns — `proposal_data`'s
`requirements` and `estimation` — at the moment it exists.

One line per brief:

    {"file": ..., "requirements": {...}, "estimation": {...}, "brief_words": [...]}

**The case file is self-contained.** `brief_words` is the brief's content-word
set, computed here from the PDF text layer, so scoring never needs the PDFs
again. That is what lets the layers run on the LXC and inside Docker, where the
briefs do not exist — storing a path instead would make both layers unrunnable
anywhere but the machine that seeded them.

Usage:
    uv run python -m stratpoint_rag.evaluation.seed_cases --briefs a.pdf b.pdf
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import pymupdf as fitz
import requests
from dotenv import load_dotenv

from stratpoint_rag.docparse.transcribe import _content_words

load_dotenv()

API = os.getenv("STRATPOINT_API_URL", "http://localhost:8000").rstrip("/")
CASES_PATH = Path(__file__).parent / "cases" / "pipeline_runs.jsonl"


def brief_words(path: Path | str) -> set[str]:
    """The brief's content words, read from its embedded text layer.

    The text layer, not a vision transcription: it is ground truth for what the
    document says, it costs nothing, and it is deterministic — three properties
    a grounding baseline needs and a model's reading of the page does not have.
    A scanned brief with no text layer yields an empty set, which is why
    `seed()` refuses to write a case for one rather than scoring every value on
    it as ungrounded.
    """
    doc = fitz.open(str(path))
    try:
        return _content_words(" ".join(doc[i].get_text() for i in range(doc.page_count)))
    finally:
        doc.close()


def load_quote_cases(path: Path | None = None) -> list[dict[str, Any]]:
    """Rebuild scorable quotes from the seeded estimations.

    Runs the real `build_quote_context` — the mapping path that actually
    produces the printed numbers — locally and with a fixed date, so the cost
    layer needs neither the network nor a browser.
    """
    from datetime import date

    from stratpoint_rag.pdf_gen.mapping import build_quote_context

    path = path or CASES_PATH
    if not path.exists():
        return []

    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            estimation = row.get("estimation")
            # A run that stalled before the estimator is a valid extraction case
            # with no quote to score. Dropping it here is right; raising is not.
            if not estimation:
                continue
            case: dict[str, Any] = {
                "file": row.get("file"),
                "context": None,
                "declared_currency": estimation.get("currency_code"),
            }
            try:
                case["context"] = build_quote_context(
                    proposal_id=f"eval-{row.get('file', 'case')}",
                    requirements=row.get("requirements") or None,
                    estimation=estimation,
                    today=date(2026, 1, 1),
                )
            except Exception as e:
                # Carried, not raised and not dropped. mapping refuses to quote
                # an estimation that prices nothing (EmptyEstimate), and that is
                # a real defect worth scoring — but letting it escape here takes
                # the whole eval command down over one bad case, losing every
                # other layer's result with it.
                case["error"] = f"{type(e).__name__}: {e}"
            cases.append(case)
    return cases


def _one_run(path: Path) -> dict[str, Any] | None:
    """Drive upload -> parse -> proposal for one brief, capturing the payload."""
    session_id = f"case_{uuid.uuid4().hex[:12]}"
    pdf = path.read_bytes()

    r = requests.post(f"{API}/upload", data={"session_id": session_id},
                      files={"file": (path.name, pdf, "application/pdf")}, timeout=60)
    r.raise_for_status()
    upload_id = r.json()["upload_id"]

    r = requests.post(f"{API}/upload/{upload_id}/parse",
                      params={"session_id": session_id}, timeout=300)
    r.raise_for_status()

    body = {
        "session_id": session_id,
        "message": ("Please review the attached brief, estimate the cost and "
                    "timeline, and generate a proposal PDF."),
        "attachments": [upload_id],
    }
    r = requests.post(f"{API}/chat", json=body, timeout=300)
    r.raise_for_status()
    # The naming ask intercepts the first proposal request — see seed_traces.
    if not (r.json().get("proposal_data") or {}).get("pdf"):
        r = requests.post(f"{API}/chat", json={**body, "message": "ACME Retail"},
                          timeout=300)
        r.raise_for_status()

    data = r.json().get("proposal_data") or {}
    return {
        "file": path.name,
        "requirements": data.get("requirements"),
        "estimation": data.get("estimation"),
        "brief_words": sorted(brief_words(path)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--briefs", nargs="+", required=True, metavar="PDF")
    ap.add_argument("--out", default=str(CASES_PATH))
    args = ap.parse_args(argv)

    if not os.getenv("NVIDIA_API_KEY"):
        print("NVIDIA_API_KEY not set — parse and chat will fail. Aborting.",
              file=sys.stderr)
        return 2

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    print(f"Seeding {len(args.briefs)} case(s) against {API} ...")
    for spec in args.briefs:
        path = Path(spec)
        try:
            words = brief_words(path)
        except Exception as e:  # unreadable PDF — say so, do not write a case
            print(f"  {path.name}: SKIP — cannot read ({type(e).__name__})", file=sys.stderr)
            continue
        if not words:
            # No text layer means no grounding baseline. Writing the case anyway
            # would score every extracted value as ungrounded and report a model
            # failure that is really a missing baseline.
            print(f"  {path.name}: SKIP — no text layer to ground against", file=sys.stderr)
            continue
        try:
            row = _one_run(path)
        except requests.RequestException as e:
            print(f"  {path.name}: FAILED — {e}", file=sys.stderr)
            continue
        if row:
            rows.append(row)
            n = len(row["requirements"].get("features") or ()) if row["requirements"] else 0
            print(f"  {path.name}: captured, {n} features, "
                  f"estimation={'yes' if row['estimation'] else 'NO'}")
        time.sleep(1)  # be polite to the throttled endpoint

    if not rows:
        # Never overwrite good cases with nothing. Observed live: the API went
        # down mid-run, every brief failed, and an unconditional write turned a
        # transient outage into lost data and both layers into a SKIP.
        print("Captured nothing — leaving any existing cases untouched.",
              file=sys.stderr)
        return 1

    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")
    print(f"Done: {len(rows)} case(s) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
