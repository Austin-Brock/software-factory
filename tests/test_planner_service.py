import pytest
from botocore.exceptions import ClientError
from starlette.testclient import TestClient
from strands.types.exceptions import MaxTokensReachedException, ModelThrottledException

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


def test_ticket_whitespace_is_trimmed(client, fake_plan):
    client.post("/invocations", json={"ticket": "  Add dark mode \n"})
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


def test_token_cap_returns_clear_error(client, monkeypatch):
    def plan_ticket(ticket):
        raise MaxTokensReachedException("cap hit")

    monkeypatch.setattr(planner, "plan_ticket", plan_ticket)
    response = client.post("/invocations", json={"ticket": "Huge ticket"})
    assert response.status_code == 422
    assert "PLANNER_MAX_TOKENS" in response.json()["details"]


def raise_client_error(code):
    def plan_ticket(ticket):
        raise ClientError(
            {"Error": {"Code": code, "Message": "arn:aws:iam::111111111111:role/secret"}},
            "ConverseStream",
        )

    return plan_ticket


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("AccessDeniedException", 503),
        ("ServiceUnavailableException", 429),
        ("ValidationException", 502),
    ],
)
def test_bedrock_errors_are_mapped_without_leaking_details(client, monkeypatch, code, status):
    monkeypatch.setattr(planner, "plan_ticket", raise_client_error(code))
    response = client.post("/invocations", json={"ticket": "Add dark mode"})
    assert response.status_code == status
    assert "arn:aws" not in response.text


def test_throttling_returns_429(client, monkeypatch):
    def plan_ticket(ticket):
        raise ModelThrottledException("slow down")

    monkeypatch.setattr(planner, "plan_ticket", plan_ticket)
    response = client.post("/invocations", json={"ticket": "Add dark mode"})
    assert response.status_code == 429


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
