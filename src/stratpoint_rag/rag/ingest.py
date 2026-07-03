"""Ingest CLI: content_hash-gated embed of the corpus into Chroma (plan §3.7).

Idempotent. Re-embeds only pages whose content_hash changed; carries removed
pages out of the store. Backs the Docker auto-ingest entrypoint (plan §4.2) and
is runnable by hand: ``stratpoint-rag-ingest``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .chunker import chunk_page
from .embeddings import Embedder, get_embedder
from .loader import load_manifest, load_pages
from .store import VectorStore


def ingest(
    data_dir: str = "data",
    *,
    embedder: Embedder | None = None,
    store: VectorStore | None = None,
    force: bool = False,
) -> dict[str, int]:
    embedder = embedder or get_embedder()
    store = store or VectorStore()
    pages = load_pages(data_dir)
    # present = every slug in the manifest, NOT just the pages that loaded. A page whose
    # .md is transiently missing is skipped from re-embedding but must not be evicted from
    # the store; only slugs genuinely dropped from the manifest get removed below.
    present = {r["slug"] for r in load_manifest(Path(data_dir) / "index.jsonl")}
    added = updated = skipped = 0

    for p in pages:
        prior = store.stored_hash(p.slug)
        if not force and prior == p.content_hash:
            skipped += 1
            continue
        chunks = chunk_page(p)
        if not chunks:
            continue
        embs = embedder.embed([c.text for c in chunks])
        store.upsert_page(chunks, embs, p.content_hash)
        if prior is None:
            added += 1
        else:
            updated += 1

    removed = 0
    for slug in store.slugs() - present:  # pages dropped from the corpus
        store.delete_slug(slug)
        removed += 1

    return {"added": added, "updated": updated, "skipped": skipped, "removed": removed}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="stratpoint-rag-ingest")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--force", action="store_true", help="re-embed every page")
    args = ap.parse_args(argv)
    stats = ingest(args.data_dir, force=args.force)
    print(
        f"ingest: added={stats['added']} updated={stats['updated']} "
        f"skipped={stats['skipped']} removed={stats['removed']}"
    )


if __name__ == "__main__":
    main()
