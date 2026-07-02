"""RAG core (planned): chunking, embeddings, vector store, and retrieval.

Consumes the crawled corpus in ``data/pages/`` + ``data/index.jsonl``.
Use ``content_hash`` from the manifest to re-embed only changed pages
(corpus invariant: a page is present when status is ``ok`` OR ``skipped``).
"""
