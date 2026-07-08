import os
import requests
from typing import Dict, Any

API_BASE_URL = os.environ.get("STRATPOINT_API_URL", "http://localhost:8000").rstrip("/")

class APIError(Exception):
    """Custom exception for API errors."""
    pass

def health_check() -> bool:
    """Check if the API is reachable."""
    try:
        response = requests.get(f"{API_BASE_URL}/health", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False

def send_message(session_id: str, message: str, history: list[dict] = None, enable_reasoning: bool = False) -> Dict[str, Any]:
    """Send a message to the chat API and return the parsed JSON response."""
    payload = {
        "message": message,
        "session_id": session_id,
        "enable_reasoning": enable_reasoning,
    }
    if history is not None:
        payload["history"] = history
        
    try:
        # Generous timeout for agentic workflows
        response = requests.post(f"{API_BASE_URL}/chat", json=payload, timeout=120)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.ConnectionError:
        raise APIError(f"Cannot connect to the API at {API_BASE_URL}. Is it running?")
    except requests.exceptions.Timeout:
        raise APIError("The API request timed out. The agent took too long to respond.")
    except requests.exceptions.HTTPError as e:
        error_msg = f"API returned an error: {e.response.status_code}"
        try:
            detail = e.response.json().get("detail", "")
            if detail:
                error_msg += f" - {detail}"
        except Exception:
            pass
        raise APIError(error_msg)
    except requests.exceptions.RequestException as e:
        raise APIError(f"An unexpected error occurred while contacting the API: {str(e)}")
