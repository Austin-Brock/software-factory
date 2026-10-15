import hashlib
import hmac

from integrations.linear import webhook

SECRET = "test-secret"


def sign(body: bytes) -> str:
    return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_is_accepted():
    body = b'{"type":"Issue"}'
    assert webhook.signature_is_valid(body, sign(body), SECRET)


def test_tampered_body_or_missing_signature_is_rejected():
    body = b'{"type":"Issue"}'
    assert not webhook.signature_is_valid(b'{"type":"Hacked"}', sign(body), SECRET)
    assert not webhook.signature_is_valid(body, None, SECRET)
    assert not webhook.signature_is_valid(body, sign(body), "")


def test_freshness_window():
    now = 1_000_000_000_000
    assert webhook.is_fresh({"webhookTimestamp": now - 59_000}, now_ms=now)
    assert not webhook.is_fresh({"webhookTimestamp": now - 61_000}, now_ms=now)
    assert not webhook.is_fresh({}, now_ms=now)


def issue_event(action, label_ids, previous=None, type_="Issue"):
    event = {"type": type_, "action": action, "data": {"id": "i1", "labelIds": label_ids}}
    if previous is not None:
        event["updatedFrom"] = {"labelIds": previous}
    return event


def test_created_issue_reports_all_labels():
    assert webhook.added_label_ids(issue_event("create", ["b", "a"])) == ["a", "b"]


def test_update_reports_only_newly_added_labels():
    assert webhook.added_label_ids(issue_event("update", ["a", "b"], previous=["a"])) == ["b"]


def test_update_without_label_change_or_label_removal_is_ignored():
    assert webhook.added_label_ids({"type": "Issue", "action": "update", "data": {"labelIds": ["a"]}, "updatedFrom": {"title": "x"}}) == []
    assert webhook.added_label_ids(issue_event("update", ["a"], previous=["a", "b"])) == []


def test_non_issue_events_are_ignored():
    assert webhook.added_label_ids(issue_event("create", ["a"], type_="Comment")) == []
    assert webhook.added_label_ids(issue_event("remove", ["a"])) == []
