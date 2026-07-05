import pytest
from fastapi.testclient import TestClient

from stratpoint_rag.api.app import app


@pytest.mark.integration
def test_chat_live_end_to_end():
    """Hits the real NIM endpoint + local Chroma store. Requires:
    - `uv run stratpoint-rag-ingest` has built chroma_db/
    - NVIDIA_API_KEY set in .env
    Run with: uv run pytest -m integration tests/test_agent_integration.py -v
    """
    client = TestClient(app)
    r = client.post("/chat", json={"message": "What services does Stratpoint offer?"})
    assert r.status_code == 200
    assert r.json()["answer"].strip()
