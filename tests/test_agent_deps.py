"""Dependency surface after dropping native function-calling.

The langchain stack is gone from src/ — nothing in the answer path is
provider-specific except the NIM URL. This test is the regression guard for
that property; without it an incidental `from langchain...` import creeps back
and the LXC install quietly regains four packages.
"""
import pathlib
import re

_SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "stratpoint_rag"
_BANNED = re.compile(r"^\s*(?:from|import)\s+(langchain\w*|langgraph)\b", re.M)


def test_runtime_dependencies_importable():
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    import uvicorn  # noqa: F401


def test_no_langchain_imports_in_src():
    offenders = [
        str(p.relative_to(_SRC))
        for p in _SRC.rglob("*.py")
        if _BANNED.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"langchain imports leaked back into src/: {offenders}"
