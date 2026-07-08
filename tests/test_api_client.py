import stratpoint_rag.ui.api_client as api_client


class _FakeResp:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return {"answer": "ok"}


def test_send_message_includes_enable_reasoning(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return _FakeResp()

    monkeypatch.setattr(api_client.requests, "post", fake_post)
    api_client.send_message("sess", "hi", enable_reasoning=True)
    assert captured["json"]["enable_reasoning"] is True


def test_send_message_defaults_enable_reasoning_false(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        api_client.requests, "post",
        lambda url, json=None, timeout=None: captured.update(json=json) or _FakeResp(),
    )
    api_client.send_message("sess", "hi")
    assert captured["json"]["enable_reasoning"] is False
