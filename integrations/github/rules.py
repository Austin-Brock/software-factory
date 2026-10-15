"""Rules every builder change must pass before it is committed.

Enforced here, at the trust boundary, even though the builder's tools apply
the same limits: the builder runs untrusted code, so its output is checked
again by the side that holds the GitHub credentials.
"""

import re

MAX_CHANGED_FILES = 20
MAX_CHANGE_BYTES = 200_000
FORBIDDEN_PREFIXES = (".github/", ".git/", "node_modules/", "dist/", "build/")
FORBIDDEN_NAMES = (".env", ".npmrc")
SAFE_PATH = re.compile(r"^[A-Za-z0-9._@+-]+(/[A-Za-z0-9._@+-]+)*$")


def change_problems(changes: dict) -> list[str]:
    problems = []
    if not changes:
        problems.append("The builder made no changes")
    if len(changes) > MAX_CHANGED_FILES:
        problems.append(f"{len(changes)} files changed (limit {MAX_CHANGED_FILES})")
    size = sum(len(c.encode()) for c in changes.values() if isinstance(c, str))
    if size > MAX_CHANGE_BYTES:
        problems.append(f"{size} bytes changed (limit {MAX_CHANGE_BYTES})")
    for path, content in changes.items():
        name = path.rsplit("/", 1)[-1]
        if not SAFE_PATH.match(path) or ".." in path.split("/"):
            problems.append(f"Unsafe path: {path!r}")
        elif path.startswith(FORBIDDEN_PREFIXES):
            problems.append(f"Protected path: {path}")
        elif name in FORBIDDEN_NAMES or name.startswith(".env."):
            problems.append(f"Secrets file: {path}")
        if content is not None and not isinstance(content, str):
            problems.append(f"Invalid content for {path}")
    return problems
