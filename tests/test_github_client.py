import json

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from integrations.github import client


@pytest.fixture(scope="module")
def key_pair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    return pem, key.public_key()


class FakeGitHub:
    def __init__(self, existing_branches=(), modes=None):
        self.calls = []
        self.existing = set(existing_branches)
        self.modes = modes or {"src/App.tsx": "100644", "src/Old.tsx": "100644"}

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, body))
        if path.endswith("/access_tokens"):
            return {"token": "ghs_installation"}
        if method == "GET" and path == "/repos/o/r":
            return {"default_branch": "main"}
        if "/git/commits/" in path:
            return {"tree": {"sha": "base-tree"}}
        if method == "GET" and "/git/trees/" in path:
            return {"tree": [{"path": p, "mode": m} for p, m in self.modes.items()]}
        if path.endswith("/git/blobs"):
            return {"sha": f"blob-{len(self.calls)}"}
        if path.endswith("/git/trees"):
            return {"sha": "new-tree"}
        if path.endswith("/git/commits"):
            return {"sha": "new-commit"}
        if path.endswith("/git/refs"):
            name = body["ref"].removeprefix("refs/heads/")
            if name in self.existing:
                raise client.GitHubError(422, "Reference already exists")
            return {}
        if path.endswith("/pulls"):
            return {"number": 7, "html_url": "https://github.com/o/r/pull/7"}
        raise AssertionError(path)


def test_installation_token_is_scoped_to_one_repo(monkeypatch, key_pair):
    pem, public_key = key_pair
    fake = FakeGitHub()
    monkeypatch.setattr(client, "_request", fake)
    app = client.GitHubApp("123", "456", pem)
    assert app.installation_token("o/r") == "ghs_installation"
    method, path, body = fake.calls[0]
    assert path == "/app/installations/456/access_tokens"
    assert body == {"repositories": ["r"], "permissions": {"contents": "write", "pull_requests": "write"}}


def test_app_jwt_is_valid_rs256(key_pair):
    pem, public_key = key_pair
    token = client.GitHubApp("123", "456", pem)._app_jwt()
    claims = jwt.decode(token, public_key, algorithms=["RS256"])
    assert claims["iss"] == "123" and claims["exp"] - claims["iat"] <= 600


def open_pr(fake, monkeypatch, **overrides):
    monkeypatch.setattr(client, "_request", fake)
    kwargs = dict(
        base_sha="abc123",
        branch="factory/fac-1-dark-mode",
        changes={"src/App.tsx": "new", "src/Old.tsx": None},
        commit_message="FAC-1: Dark mode",
        title="FAC-1: Dark mode",
        body="body",
        draft=False,
    )
    kwargs.update(overrides)
    return client.open_pull_request("tok", "o/r", **kwargs)


def test_commit_is_based_on_the_snapshot_commit(monkeypatch):
    fake = FakeGitHub()
    pull = open_pr(fake, monkeypatch)
    assert pull == {"number": 7, "url": "https://github.com/o/r/pull/7", "branch": "factory/fac-1-dark-mode"}
    by_path = {path: body for _, path, body in fake.calls}
    assert by_path["/repos/o/r/git/commits"]["parents"] == ["abc123"]
    tree = by_path["/repos/o/r/git/trees"]
    assert tree["base_tree"] == "base-tree"
    assert {"path": "src/Old.tsx", "mode": "100644", "type": "blob", "sha": None} in tree["tree"]
    assert by_path["/repos/o/r/pulls"] == {
        "title": "FAC-1: Dark mode", "head": "factory/fac-1-dark-mode", "base": "main", "body": "body", "draft": False,
    }


def test_existing_branch_gets_a_suffix(monkeypatch):
    fake = FakeGitHub(existing_branches={"factory/fac-1-dark-mode"})
    assert open_pr(fake, monkeypatch)["branch"] == "factory/fac-1-dark-mode-2"


def test_failing_build_opens_draft(monkeypatch):
    fake = FakeGitHub()
    open_pr(fake, monkeypatch, draft=True)
    assert [b for _, p, b in fake.calls if p.endswith("/pulls")][0]["draft"] is True


def test_client_never_merges(monkeypatch):
    fake = FakeGitHub()
    open_pr(fake, monkeypatch)
    assert not any("merge" in path for _, path, _ in fake.calls)
    assert json.dumps(client.TOKEN_PERMISSIONS) == '{"contents": "write", "pull_requests": "write"}'


def test_executable_files_keep_their_mode(monkeypatch):
    fake = FakeGitHub(modes={"scripts/run.sh": "100755"})
    open_pr(fake, monkeypatch, changes={"scripts/run.sh": "#!/bin/sh\necho hi\n", "src/New.ts": "x"})
    tree = {e["path"]: e["mode"] for _, p, b in fake.calls if p.endswith("/git/trees") for e in b["tree"]}
    assert tree == {"scripts/run.sh": "100755", "src/New.ts": "100644"}


def test_symlinks_are_never_rewritten(monkeypatch):
    fake = FakeGitHub(modes={"link": "120000"})
    with pytest.raises(client.GitHubError, match="not a regular file"):
        open_pr(fake, monkeypatch, changes={"link": "target"})
    assert not any(p.endswith("/git/refs") for _, p, _ in fake.calls)
