import hashlib
import hmac
import io
import json
import time

import urllib.error

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

from integrations.linear import handlers

SECRET = "test-secret"


class FakeAws:
    def __init__(self):
        self.seen = set()
        self.invocations = []
        self.invoke_error = None
        self.planner_result = {"plan": "1. Objective ..."}
        self.planner_raw = None
        self.planner_error = None
        self.tickets = []
        self.repos = []

    def client(self, service, **kwargs):
        return self

    # DynamoDB
    def put_item(self, TableName, Item, ConditionExpression):
        key = Item["pk"]["S"]
        if key in self.seen:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.seen.add(key)

    def delete_item(self, TableName, Key):
        self.seen.discard(Key["pk"]["S"])

    # Lambda
    def invoke(self, FunctionName, InvocationType, Payload):
        if self.invoke_error:
            raise self.invoke_error
        self.invocations.append(json.loads(Payload))

    # AgentCore
    def invoke_agent_runtime(self, **kwargs):
        if isinstance(self.planner_error, Exception):
            raise self.planner_error
        if self.planner_error:
            raise ClientError({"Error": {"Code": self.planner_error}}, "InvokeAgentRuntime")
        payload = json.loads(kwargs["payload"])
        self.tickets.append(payload["ticket"])
        self.repos.append(payload.get("repo"))
        raw = self.planner_raw or json.dumps(self.planner_result).encode()
        return {"response": io.BytesIO(raw)}


class FakeLinear:
    def __init__(self, label_names, description="Persist the preference.", exists=True, team="FAC"):
        self.label_names = label_names
        self.team = team
        self.description = description
        self.exists = exists
        self.comment_error = None
        self.comments = []

    def __call__(self, api_key):
        return self

    def get_issue(self, issue_id):
        if not self.exists:
            return None
        return {
            "id": issue_id,
            "identifier": "FAC-1",
            "title": "Add dark mode",
            "description": self.description,
            "url": "https://linear.app/x/issue/FAC-1",
            "team": {"key": self.team},
            "labels": {"nodes": [{"id": i, "name": n} for i, n in self.label_names.items()]},
        }

    def comment(self, issue_id, body):
        if self.comment_error:
            raise self.comment_error
        self.comments.append(body)


@pytest.fixture
def aws(monkeypatch):
    fake = FakeAws()
    handlers._client.cache_clear()
    monkeypatch.setattr(handlers.boto3, "client", fake.client)
    monkeypatch.setattr(handlers, "_parameter", lambda name: SECRET)
    for name, value in {
        "WEBHOOK_SECRET_PARAM": "/sf/secret",
        "LINEAR_API_KEY_PARAM": "/sf/key",
        "DEDUPE_TABLE": "dedupe",
        "WORKER_FUNCTION": "worker",
        "PLANNER_RUNTIME_ARN": "arn:runtime",
        "TRIGGER_LABEL": "factory:plan",
        "TEAM_REPOS": '{"FAC": "Austin-Brock/factory-sandbox"}',
    }.items():
        monkeypatch.setenv(name, value)
    return fake


def delivery(payload, delivery_id="d1", secret=SECRET, signature=None):
    body = json.dumps(payload)
    sig = signature or hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
    return {"headers": {"Linear-Signature": sig, "Linear-Delivery": delivery_id}, "body": body}


def label_added(label_ids=("L1",)):
    return {
        "type": "Issue",
        "action": "update",
        "data": {"id": "issue-1", "labelIds": list(label_ids)},
        "updatedFrom": {"labelIds": []},
        "webhookTimestamp": int(time.time() * 1000),
    }


def test_receiver_hands_label_add_to_worker(aws):
    response = handlers.receiver(delivery(label_added()), None)
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"accepted": "d1"}
    assert aws.invocations == [{"issueId": "issue-1", "addedLabelIds": ["L1"], "deliveryId": "d1"}]


def test_receiver_rejects_bad_signature(aws):
    response = handlers.receiver(delivery(label_added(), signature="0" * 64), None)
    assert response["statusCode"] == 401
    assert aws.invocations == []


def test_receiver_rejects_stale_delivery(aws):
    payload = label_added()
    payload["webhookTimestamp"] -= 120_000
    assert handlers.receiver(delivery(payload), None)["statusCode"] == 401


def test_receiver_ignores_retried_delivery(aws):
    handlers.receiver(delivery(label_added()), None)
    response = handlers.receiver(delivery(label_added()), None)
    assert response["statusCode"] == 200
    assert len(aws.invocations) == 1


def test_receiver_ignores_events_without_new_labels(aws):
    payload = label_added()
    payload["updatedFrom"] = {"title": "old"}
    assert handlers.receiver(delivery(payload), None)["statusCode"] == 200
    assert aws.invocations == []


def test_worker_posts_plan_for_trigger_label(aws, monkeypatch):
    linear = FakeLinear({"L1": "factory:plan"})
    monkeypatch.setattr(handlers, "LinearClient", linear)
    result = handlers.worker({"issueId": "issue-1", "addedLabelIds": ["L1"]}, None)
    assert result == {"planned": "FAC-1"}
    assert linear.comments[0].startswith(handlers.PLAN_HEADER)
    assert "<ticket>\nPersist the preference.\n</ticket>" in aws.tickets[0]


def test_worker_skips_other_labels_without_calling_planner(aws, monkeypatch):
    linear = FakeLinear({"L1": "bug"})
    monkeypatch.setattr(handlers, "LinearClient", linear)
    assert handlers.worker({"issueId": "issue-1", "addedLabelIds": ["L1"]}, None) == {"skipped": "FAC-1"}
    assert aws.tickets == [] and linear.comments == []


def test_worker_comments_planner_error(aws, monkeypatch):
    aws.planner_result = {"error": "Plan exceeded the output token cap", "details": "Raise PLANNER_MAX_TOKENS"}
    linear = FakeLinear({"L1": "factory:plan"})
    monkeypatch.setattr(handlers, "LinearClient", linear)
    assert handlers.worker({"issueId": "issue-1", "addedLabelIds": ["L1"]}, None) == {"failed": "FAC-1"}
    assert "could not produce a plan" in linear.comments[0]


def test_worker_reports_kill_switch(aws, monkeypatch):
    aws.planner_error = "AccessDeniedException"
    linear = FakeLinear({"L1": "factory:plan"})
    monkeypatch.setattr(handlers, "LinearClient", linear)
    handlers.worker({"issueId": "issue-1", "addedLabelIds": ["L1"]}, None)
    assert "Planner is paused" in linear.comments[0]


def test_failed_handoff_lets_linear_retry(aws):
    aws.invoke_error = ClientError({"Error": {"Code": "TooManyRequestsException"}}, "Invoke")
    assert handlers.receiver(delivery(label_added()), None)["statusCode"] == 500
    aws.invoke_error = None
    assert handlers.receiver(delivery(label_added()), None)["statusCode"] == 200
    assert len(aws.invocations) == 1


def worker_with(aws, monkeypatch, **linear_kwargs):
    linear = FakeLinear({"L1": "factory:plan"}, **linear_kwargs)
    monkeypatch.setattr(handlers, "LinearClient", linear)
    result = handlers.worker({"issueId": "issue-1", "addedLabelIds": ["L1"]}, None)
    return result, linear


def test_worker_reports_planner_timeout(aws, monkeypatch):
    aws.planner_error = ReadTimeoutError(endpoint_url="https://agentcore")
    result, linear = worker_with(aws, monkeypatch)
    assert result == {"failed": "FAC-1"}
    assert "Planner did not respond" in linear.comments[0]


def test_worker_reports_unreadable_planner_response(aws, monkeypatch):
    aws.planner_raw = b"<html>bad gateway</html>"
    _, linear = worker_with(aws, monkeypatch)
    assert "unreadable response" in linear.comments[0]


def test_long_descriptions_are_truncated(aws, monkeypatch):
    worker_with(aws, monkeypatch, description="x" * 30_000)
    assert len(aws.tickets[0]) < 16_000
    assert "[description truncated]" in aws.tickets[0]


def test_validation_details_are_readable(aws, monkeypatch):
    aws.planner_result = {"error": "Invalid request", "details": [{"msg": "String should have at most 20000 characters"}]}
    _, linear = worker_with(aws, monkeypatch)
    assert "String should have at most 20000 characters" in linear.comments[0]
    assert "{" not in linear.comments[0]


def test_deleted_issue_is_skipped(aws, monkeypatch):
    result, linear = worker_with(aws, monkeypatch, exists=False)
    assert result == {"skipped": "issue-1"}
    assert aws.tickets == []


def test_comment_failure_is_reported_not_raised(aws, monkeypatch):
    linear = FakeLinear({"L1": "factory:plan"})
    linear.comment_error = urllib.error.URLError("down")
    monkeypatch.setattr(handlers, "LinearClient", linear)
    assert handlers.worker({"issueId": "issue-1", "addedLabelIds": ["L1"]}, None) == {"failed": "FAC-1"}


def test_trigger_label_match_ignores_case(aws, monkeypatch):
    linear = FakeLinear({"L1": "Factory:Plan"})
    monkeypatch.setattr(handlers, "LinearClient", linear)
    assert handlers.worker({"issueId": "issue-1", "addedLabelIds": ["L1"]}, None) == {"planned": "FAC-1"}


def test_parameters_are_refreshed_after_ttl(monkeypatch):
    calls = []

    class FakeSsm:
        def get_parameter(self, Name, WithDecryption):
            calls.append(Name)
            return {"Parameter": {"Value": f"v{len(calls)}"}}

    handlers._client.cache_clear()
    handlers._parameters.clear()
    monkeypatch.setattr(handlers.boto3, "client", lambda service, **kw: FakeSsm())
    clock = [1000.0]
    monkeypatch.setattr(handlers.time, "monotonic", lambda: clock[0])
    assert handlers._parameter("/p") == "v1"
    assert handlers._parameter("/p") == "v1"
    clock[0] += handlers.PARAMETER_TTL_SECONDS + 1
    assert handlers._parameter("/p") == "v2"
    handlers._client.cache_clear()
    handlers._parameters.clear()


def test_worker_plans_against_the_teams_repo(aws, monkeypatch):
    worker_with(aws, monkeypatch)
    assert aws.repos == ["Austin-Brock/factory-sandbox"]


def test_worker_plans_without_repo_for_unmapped_team(aws, monkeypatch):
    worker_with(aws, monkeypatch, team="OPS")
    assert aws.repos == [None]
