"""RAG config — env-switched providers + paths (plan §3.6).

Read at call time (not import) so tests can monkeypatch the environment.
"""

from __future__ import annotations

import os
from dotenv import load_dotenv

load_dotenv()



def chroma_dir() -> str:
    val = os.getenv("CHROMA_DIR")
    return val if val else "chroma_db"


def collection_name() -> str:
    val = os.getenv("CHROMA_COLLECTION")
    return val if val else "stratpoint"


def embedding_provider() -> str:
    val = os.getenv("EMBEDDING_PROVIDER")
    return val if val else "local"


def embedding_model() -> str:
    val = os.getenv("EMBEDDING_MODEL")
    return val if val else "BAAI/bge-small-en-v1.5"


def llm_model() -> str:
    # NVIDIA-hosted NIM (cloud) — see plan Decision #1
    val = os.getenv("LLM_MODEL")
    return val if val else "google/gemma-4-31b-it"


def nvidia_base_url() -> str:
    val = os.getenv("NVIDIA_BASE_URL")
    return val if val else "https://integrate.api.nvidia.com/v1"


def nvidia_api_key() -> str:
    val = os.getenv("NVIDIA_API_KEY")
    return val.strip() if val else ""


def llm_timeout() -> int:
    val = os.getenv("LLM_TIMEOUT")
    try:
        return int(val) if val else 300
    except ValueError:
        return 300

