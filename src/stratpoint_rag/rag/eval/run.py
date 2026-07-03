"""hit@k over the gold set (plan §3.8): run `python -m stratpoint_rag.rag.eval.run`.

A question "hits" when the expected source page appears among the top-k retrieved
chunks. Needs a populated Chroma store (run `stratpoint-rag-ingest` first).
"""

from __future__ import annotations

import json
from pathlib import Path

from ..retrieve import retrieve

GOLD = Path(__file__).with_name("gold.jsonl")


def hit_at_k(k: int = 5, gold_path: Path = GOLD) -> float:
    rows = [json.loads(line) for line in gold_path.read_text().splitlines() if line.strip()]
    hits = 0
    for r in rows:
        got = {c.slug for c in retrieve(r["q"], k=k)}
        ok = r["slug"] in got
        hits += ok
        print(f"  [{'HIT ' if ok else 'MISS'}] {r['q']}  -> want {r['slug']}")
    score = hits / len(rows) if rows else 0.0
    print(f"hit@{k}: {hits}/{len(rows)} = {score:.2f}")
    return score


if __name__ == "__main__":
    hit_at_k()
