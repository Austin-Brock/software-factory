import pytest

from integrations.github import rules


def test_normal_change_passes():
    assert rules.change_problems({"src/App.tsx": "x", "src/Old.tsx": None}) == []


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "no changes"),
        ({".github/workflows/ci.yml": "x"}, "Protected path"),
        ({"node_modules/a.js": "x"}, "Protected path"),
        ({".env": "SECRET=1"}, "Secrets file"),
        ({"app/.env.local": "x"}, "Secrets file"),
        ({"../escape.txt": "x"}, "Unsafe path"),
        ({"/etc/passwd": "x"}, "Unsafe path"),
        ({"src/a b.ts": "x"}, "Unsafe path"),
        ({"src/a.ts": 42}, "Invalid content"),
    ],
)
def test_bad_changes_are_rejected(changes, expected):
    assert any(expected in p for p in rules.change_problems(changes))


def test_size_limits(monkeypatch):
    monkeypatch.setattr(rules, "MAX_CHANGED_FILES", 1)
    assert any("files changed" in p for p in rules.change_problems({"a": "1", "b": "2"}))
    monkeypatch.setattr(rules, "MAX_CHANGE_BYTES", 3)
    assert any("bytes changed" in p for p in rules.change_problems({"a": "1234"}))
