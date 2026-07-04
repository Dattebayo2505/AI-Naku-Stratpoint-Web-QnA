"""RAG config — env-switched providers + paths (plan §3.6).

Read at call time (not import) so tests can monkeypatch the environment.
"""

from __future__ import annotations

import os


def chroma_dir() -> str:
    return os.getenv("CHROMA_DIR", "chroma_db")


def collection_name() -> str:
    return os.getenv("CHROMA_COLLECTION", "stratpoint")


def embedding_provider() -> str:
    return os.getenv("EMBEDDING_PROVIDER", "local")


def embedding_model() -> str:
    return os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5")


def llm_model() -> str:
    # NVIDIA-hosted NIM (cloud) — see plan Decision #1
    return os.getenv("LLM_MODEL", "google/gemma-4-31b-it")


def nvidia_base_url() -> str:
    return os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")


def nvidia_api_key() -> str:
    return os.getenv("NVIDIA_API_KEY", "")
