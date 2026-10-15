import pytest
from starlette.testclient import TestClient

from agents import builder, builder_service, builder_tools

REQUEST = {"ticket": "Add dark mode", "plan": "1. Objective", "repo": "Austin-Brock/factory-sandbox"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(builder_service, "ALLOWED_REPOS", {"Austin-Brock/factory-sandbox"})
    return TestClient(builder_service.app)


def test_build_result_is_returned(client, monkeypatch):
    monkeypatch.setattr(builder, "build_ticket", lambda t, p, r: {"passed": True, "changes": {"a": "b"}})
    response = client.post("/invocations", json=REQUEST)
    assert response.status_code == 200
    assert response.json()["passed"] is True


@pytest.mark.parametrize(
    "payload",
    [
        {**REQUEST, "repo": "someone/else"},
        {**REQUEST, "plan": ""},
        {k: v for k, v in REQUEST.items() if k != "repo"},
    ],
)
def test_bad_requests_never_start_a_build(client, monkeypatch, payload):
    calls = []
    monkeypatch.setattr(builder, "build_ticket", lambda *a: calls.append(a))
    assert client.post("/invocations", json=payload).status_code == 400
    assert calls == []


def test_size_limit_returns_422(client, monkeypatch):
    def build_ticket(*args):
        raise builder_tools.BuildLimitError("Change touches 30 files (limit 20)")

    monkeypatch.setattr(builder, "build_ticket", build_ticket)
    response = client.post("/invocations", json=REQUEST)
    assert response.status_code == 422
    assert "30 files" in response.json()["details"]
