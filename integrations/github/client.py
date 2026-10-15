"""GitHub App client: commit a change set to a new branch and open a PR.

Only this code (in the worker Lambda) holds GitHub credentials. Installation
tokens are requested for a single repository with only the permissions the
factory needs.
"""

import json
import time
import urllib.error
import urllib.request

import jwt

API = "https://api.github.com"
TOKEN_PERMISSIONS = {"contents": "write", "pull_requests": "write"}


class GitHubError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub API {status}: {message}")
        self.status = status


def _request(method: str, path: str, token: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        f"{API}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "software-factory",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read()).get("message", exc.reason)
        except ValueError:
            message = exc.reason
        raise GitHubError(exc.code, message) from exc


class GitHubApp:
    def __init__(self, app_id: str, installation_id: str, private_key: str):
        self.app_id = app_id
        self.installation_id = installation_id
        self.private_key = private_key

    def _app_jwt(self) -> str:
        now = int(time.time())
        # GitHub allows at most 10 minutes; backdate a minute for clock skew.
        claims = {"iat": now - 60, "exp": now + 9 * 60, "iss": self.app_id}
        return jwt.encode(claims, self.private_key, algorithm="RS256")

    def installation_token(self, repo: str) -> str:
        """A short-lived token for one repo with contents + PR write only."""
        response = _request(
            "POST",
            f"/app/installations/{self.installation_id}/access_tokens",
            self._app_jwt(),
            {"repositories": [repo.split("/", 1)[1]], "permissions": TOKEN_PERMISSIONS},
        )
        return response["token"]


def open_pull_request(
    token: str,
    repo: str,
    *,
    base_sha: str,
    branch: str,
    changes: dict,
    commit_message: str,
    title: str,
    body: str,
    draft: bool,
) -> dict:
    """Commit changes on top of base_sha to a new branch and open a PR."""
    default_branch = _request("GET", f"/repos/{repo}", token)["default_branch"]
    base_tree = _request("GET", f"/repos/{repo}/git/commits/{base_sha}", token)["tree"]["sha"]
    # Keep each file's existing mode (e.g. executable scripts stay executable).
    listing = _request("GET", f"/repos/{repo}/git/trees/{base_tree}?recursive=1", token)
    modes = {entry["path"]: entry["mode"] for entry in listing.get("tree", [])}

    tree = []
    for path, content in sorted(changes.items()):
        mode = modes.get(path, "100644")
        if mode not in ("100644", "100755"):
            raise GitHubError(422, f"Refusing to change {path}: it is not a regular file")
        if content is None:
            tree.append({"path": path, "mode": mode, "type": "blob", "sha": None})
        else:
            blob = _request(
                "POST", f"/repos/{repo}/git/blobs", token, {"content": content, "encoding": "utf-8"}
            )
            tree.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})
    new_tree = _request("POST", f"/repos/{repo}/git/trees", token, {"base_tree": base_tree, "tree": tree})
    commit = _request(
        "POST",
        f"/repos/{repo}/git/commits",
        token,
        {"message": commit_message, "tree": new_tree["sha"], "parents": [base_sha]},
    )

    # A re-run for the same ticket gets a fresh branch instead of overwriting.
    for attempt in range(5):
        name = branch if attempt == 0 else f"{branch}-{attempt + 1}"
        try:
            _request(
                "POST", f"/repos/{repo}/git/refs", token, {"ref": f"refs/heads/{name}", "sha": commit["sha"]}
            )
            break
        except GitHubError as exc:
            if exc.status != 422 or attempt == 4:
                raise
    pull = _request(
        "POST",
        f"/repos/{repo}/pulls",
        token,
        {"title": title, "head": name, "base": default_branch, "body": body, "draft": draft},
    )
    return {"number": pull["number"], "url": pull["html_url"], "branch": name}
