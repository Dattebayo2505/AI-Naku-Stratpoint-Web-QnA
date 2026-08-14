"""One-shot import of the old JSONL trace file into the MLflow store.

The trajectory and e2e eval layers score *recorded* traces, so the trace file is
not disposable history — it is the measurement. Dropping it when the store
changed would have taken both layers to SKIP and deleted the 21/21 the floors
were calibrated against.

Run once, then delete llmops_traces.jsonl:

    uv run python -m stratpoint_rag.llmops.migrate

Idempotent only in the sense that it never modifies the source file; running it
twice imports the records twice, so check the printed count.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from stratpoint_rag.llmops.sink import append, enabled, read_records

DEFAULT_SOURCE = "llmops_traces.jsonl"


def load_jsonl(path: str) -> list[dict]:
    """Read the old file, skipping blank and corrupt lines (as the sink did)."""
    if not os.path.exists(path):
        return []
    records: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=DEFAULT_SOURCE, help=f"default {DEFAULT_SOURCE}")
    args = ap.parse_args(argv)

    if not enabled():
        print("LLMOPS_ENABLED=0 — nothing would be written. Aborting.", file=sys.stderr)
        return 2

    records = load_jsonl(args.source)
    if not records:
        print(f"no records in {args.source} — nothing to do")
        return 0

    before = len(read_records())
    for rec in records:
        append(rec)
    after = len(read_records())

    print(f"read {len(records)} record(s) from {args.source}")
    print(f"store went from {before} to {after} record(s)")
    # append() swallows store errors by design, so a short count is the only
    # signal that something did not land. Say so rather than exiting 0 quietly.
    if after - before != len(records):
        print(f"WARNING: {len(records) - (after - before)} record(s) did not land", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
