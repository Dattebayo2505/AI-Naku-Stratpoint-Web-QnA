"""Populate llmops_traces.jsonl by driving the real chain over HTTP.

Layers 2 and 3 score recorded traces, and a fresh trace file is empty. Manual
app usage fills it equally well; this script's only advantage is reproducibility
— the same N runs every time. It adds no new code path: it uploads a brief,
parses it, and asks for a proposal, the same requests the UI issues.

Requirements: a running API (STRATPOINT_API_URL, default http://localhost:8000),
NVIDIA_API_KEY (parse uses vision; the chain calls NIM), and — for PDF
generation — `playwright install chromium`. Never invoked by the test suite.

Usage: uv run python -m stratpoint_rag.evaluation.seed_traces --runs 5
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

import requests
from dotenv import load_dotenv

import pymupdf as fitz

load_dotenv()  # every other entry point gets the key via rag.config; this one imports neither

API = os.getenv("STRATPOINT_API_URL", "http://localhost:8000").rstrip("/")

BRIEF_TEXT = (
    "Project Brief: ACME Retail Mobile App\n\n"
    "We need a cross-platform mobile application with user accounts, a product\n"
    "catalog, cart and checkout, push notifications, and an admin dashboard.\n"
    "Integrations: payment gateway, cloud hosting, and a recommendation model.\n"
    "Security: PCI-compliant payment handling and role-based access control.\n"
    "Timeline: launch within four months.\n"
)

def _make_brief_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), BRIEF_TEXT, fontsize=11)
    return doc.tobytes()

def _generated_pdf(result: dict) -> bool:
    """True only when the PDF tool actually produced something.

    Not `bool(result["proposal_data"])`: that field is the turn's capture sink
    and `extract_brief_requirements` fills its `requirements` slot on the way
    past, so a run that read the brief and then wandered off into
    `search_stratpoint` without ever rendering a quote still reported success.
    Measured: two of thirteen real-RFP runs printed `proposal=yes` with no
    `generate_proposal_pdf` in their trace at all.
    """
    return bool((result.get("proposal_data") or {}).get("pdf"))


def _one_run(pdf: bytes, name: str = "brief.pdf") -> None:
    session_id = f"seed_{uuid.uuid4().hex[:12]}"
    # upload
    r = requests.post(
        f"{API}/upload",
        data={"session_id": session_id},
        files={"file": (name, pdf, "application/pdf")},
        timeout=60,
    )
    r.raise_for_status()
    upload_id = r.json()["upload_id"]
    # parse (hop 1 — vision/text transcription)
    r = requests.post(f"{API}/upload/{upload_id}/parse", params={"session_id": session_id}, timeout=300)
    r.raise_for_status()
    # ask for a proposal — drives read_brief/extract -> estimate -> generate_proposal_pdf
    r = requests.post(
        f"{API}/chat",
        json={
            "session_id": session_id,
            "message": "Please review the attached brief, estimate the cost and timeline, and generate a proposal PDF.",
            "attachments": [upload_id],
        },
        timeout=300,
    )
    r.raise_for_status()
    # The naming ask (disambiguation/engagement.py) intercepts the first proposal
    # request and returns a question, so the chain never reaches the PDF on one
    # turn. Answer it — a real visitor does the same. `proposal_data` is the tell.
    if not _generated_pdf(r.json()):
        r = requests.post(
            f"{API}/chat",
            json={
                "session_id": session_id,
                "message": "ACME Retail",
                "attachments": [upload_id],
            },
            timeout=300,
        )
        r.raise_for_status()
    got_pdf = bool(r.json().get("proposal_data"))
    print(f"  {session_id}: {name} — chat ok, proposal={'yes' if got_pdf else 'NO'}")

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    # Real briefs beat N copies of the synthetic one: the generated brief is one
    # page with no tables, so it exercises neither map-reduced extraction nor the
    # table rebuild, and five identical runs measure variance, not coverage.
    ap.add_argument("--briefs", nargs="+", metavar="PDF",
                    help="real brief PDFs, one run each (--runs is ignored)")
    args = ap.parse_args(argv)

    if not os.getenv("NVIDIA_API_KEY"):
        print("NVIDIA_API_KEY not set — parse and chat will fail. Aborting.", file=sys.stderr)
        return 2

    if args.briefs:
        jobs = [(os.path.basename(p), open(p, "rb").read()) for p in args.briefs]
    else:
        jobs = [("brief.pdf", _make_brief_pdf())] * args.runs

    print(f"Seeding {len(jobs)} run(s) against {API} ...")
    ok = 0
    for i, (name, pdf) in enumerate(jobs):
        try:
            _one_run(pdf, name)
            ok += 1
        except requests.RequestException as e:
            print(f"  run {i} ({name}): FAILED — {e}", file=sys.stderr)
        time.sleep(1)  # be polite to the throttled endpoint
    print(f"Done: {ok}/{len(jobs)} runs traced.")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
