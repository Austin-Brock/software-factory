import hashlib
import hmac
import io
import json
import time

import pytest
from botocore.exceptions import ClientError

from integrations.linear import handlers

SECRET = "test-secret"


class FakeAws:
    def __init__(self):
        self.seen = set()
        self.invocations = []
        self.planner_result = {"plan": "1. Objective ..."}
        self.planner_error = None
        self.tickets = []

    def client(self, service, **kwargs):
        return self

    # DynamoDB
    def put_item(self, TableName, Item, ConditionExpression):
        key = Item["pk"]["S"]
        if key in self.seen:
            raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem")
        self.seen.add(key)

    # Lambda
    def invoke(self, FunctionName, InvocationType, Payload):
        self.invocations.append(json.loads(Payload))

    # AgentCore
    def invoke_agent_runtime(self, **kwargs):
        if self.planner_error:
            raise ClientError({"Error": {"Code": self.planner_error}}, "InvokeAgentRuntime")
        self.tickets.append(json.loads(kwargs["payload"])["ticket"])
        return {"response": io.BytesIO(json.dumps(self.planner_result).encode())}


class FakeLinear:
    def __init__(self, label_names):
        self.label_names = label_names
        self.comments = []

    def __call__(self, api_key):
        return self

    def get_issue(self, issue_id):
        return {
            "id": issue_id,
            "identifier": "FAC-1",
            "title": "Add dark mode",
            "description": "Persist the preference.",
            "url": "https://linear.app/x/issue/FAC-1",
            "labels": {"nodes": [{"id": i, "name": n} for i, n in self.label_names.items()]},
        }

    def comment(self, issue_id, body):
        self.comments.append(body)


@pytest.fixture
def aws(monkeypatch):
    fake = FakeAws()
    monkeypatch.setattr(handlers.boto3, "client", fake.client)
    monkeypatch.setattr(handlers, "_parameter", lambda name: SECRET)
    for name, value in {
        "WEBHOOK_SECRET_PARAM": "/sf/secret",
        "LINEAR_API_KEY_PARAM": "/sf/key",
        "DEDUPE_TABLE": "dedupe",
        "WORKER_FUNCTION": "worker",
        "PLANNER_RUNTIME_ARN": "arn:runtime",
        "TRIGGER_LABEL": "factory:plan",
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
    assert response["statusCode"] == 202
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
