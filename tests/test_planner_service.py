import pytest
from starlette.testclient import TestClient

from agents import planner, planner_service


@pytest.fixture
def client():
    return TestClient(planner_service.app)


@pytest.fixture
def fake_plan(monkeypatch):
    calls = []

    def plan_ticket(ticket):
        calls.append(ticket)
        return f"plan for: {ticket}"

    monkeypatch.setattr(planner, "plan_ticket", plan_ticket)
    return calls


def test_ping_reports_healthy(client):
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json()["status"] == "Healthy"


def test_invocation_returns_plan(client, fake_plan):
    response = client.post("/invocations", json={"ticket": "Add dark mode"})
    assert response.status_code == 200
    assert response.json() == {"plan": "plan for: Add dark mode"}
    assert fake_plan == ["Add dark mode"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"ticket": ""},
        {"ticket": "   "},
        {"ticket": 42},
        {"ticket": "x" * (planner_service.MAX_TICKET_CHARS + 1)},
    ],
)
def test_invalid_payload_is_rejected_without_calling_model(client, fake_plan, payload):
    response = client.post("/invocations", json=payload)
    assert response.status_code == 400
    assert fake_plan == []


def test_each_ticket_gets_a_fresh_agent(monkeypatch):
    built = []

    class FakeAgent:
        def __call__(self, ticket):
            return f"plan for: {ticket}"

    def build_planner(stream_to_stdout=True):
        built.append(stream_to_stdout)
        return FakeAgent()

    monkeypatch.setattr(planner, "build_planner", build_planner)
    planner.plan_ticket("one")
    planner.plan_ticket("two")
    assert built == [False, False]
