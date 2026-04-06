import json
import urllib.error

from app.email_service import send_expo_push_notifications, send_expo_push_notifications_result


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_send_expo_push_notifications_counts_ok_tickets(monkeypatch):
    def fake_urlopen(_req, timeout=10):
        return FakeHTTPResponse(
            {
                "data": [
                    {"status": "ok"},
                    {"status": "error"},
                    {"status": "ok"},
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    accepted = send_expo_push_notifications(
        tokens=["ExponentPushToken[abc]"],
        title="t",
        body="b",
    )

    assert accepted == 2


def test_send_expo_push_notifications_returns_zero_on_malformed_payload(monkeypatch):
    def fake_urlopen(_req, timeout=10):
        return FakeHTTPResponse({"data": "invalid"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    accepted = send_expo_push_notifications(
        tokens=["ExponentPushToken[abc]"],
        title="t",
        body="b",
    )

    assert accepted == 0


def test_send_expo_push_notifications_retries_once_then_succeeds(monkeypatch):
    state = {"calls": 0}

    def fake_urlopen(_req, timeout=10):
        state["calls"] += 1
        if state["calls"] == 1:
            raise urllib.error.URLError("temporary network error")
        return FakeHTTPResponse({"data": [{"status": "ok"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    accepted = send_expo_push_notifications(
        tokens=["ExponentPushToken[abc]"],
        title="t",
        body="b",
        max_retries=1,
    )

    assert accepted == 1
    assert state["calls"] == 2


def test_send_expo_push_notifications_returns_zero_after_retries(monkeypatch):
    def fake_urlopen(_req, timeout=10):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    accepted = send_expo_push_notifications(
        tokens=["ExponentPushToken[abc]"],
        title="t",
        body="b",
        max_retries=1,
    )

    assert accepted == 0


def test_send_expo_push_notifications_result_returns_structured_success(monkeypatch):
    def fake_urlopen(_req, timeout=10):
        return FakeHTTPResponse({"data": [{"status": "ok"}, {"status": "error"}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = send_expo_push_notifications_result(
        tokens=["ExponentPushToken[abc]", "ExponentPushToken[def]"],
        title="t",
        body="b",
    )

    assert result["total_tokens"] == 2
    assert result["accepted_count"] == 1
    assert result["failed_count"] == 1
    assert result["status"] == "partial"


def test_send_expo_push_notifications_result_returns_failed_on_exception(monkeypatch):
    def fake_urlopen(_req, timeout=10):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    result = send_expo_push_notifications_result(
        tokens=["ExponentPushToken[abc]"],
        title="t",
        body="b",
        max_retries=0,
    )

    assert result["status"] == "failed"
    assert result["accepted_count"] == 0
    assert result["failed_count"] == 1
    assert "network down" in (result["error_message"] or "")
