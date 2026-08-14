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

import pymupdf as fitz

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

def _one_run(pdf: bytes) -> None:
    session_id = f"seed_{uuid.uuid4().hex[:12]}"
    # upload
    r = requests.post(
        f"{API}/upload",
        data={"session_id": session_id},
        files={"file": ("brief.pdf", pdf, "application/pdf")},
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
    print(f"  {session_id}: chat ok")

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args(argv)

    if not os.getenv("NVIDIA_API_KEY"):
        print("NVIDIA_API_KEY not set — parse and chat will fail. Aborting.", file=sys.stderr)
        return 2

    pdf = _make_brief_pdf()
    print(f"Seeding {args.runs} run(s) against {API} ...")
    ok = 0
    for i in range(args.runs):
        try:
            _one_run(pdf)
            ok += 1
        except requests.RequestException as e:
            print(f"  run {i}: FAILED — {e}", file=sys.stderr)
        time.sleep(1)  # be polite to the throttled endpoint
    print(f"Done: {ok}/{args.runs} runs traced.")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
