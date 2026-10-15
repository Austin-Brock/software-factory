"""Workspace tools for the builder: edit files and run the repo's own checks.

There is no general shell. The builder can read, search and edit files inside
its snapshot of the repo, and run a fixed set of npm scripts. Every edit is
recorded so the final change can be checked and committed by the caller,
which (unlike this container) holds the GitHub credentials.
"""

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from strands import tool

from agents import repo_tools

MAX_CHANGED_FILES = 20
MAX_CHANGE_BYTES = 200_000
MAX_CHECK_RUNS = 10
CHECK_TIMEOUT_SECONDS = 300
OUTPUT_TAIL_CHARS = 4_000
# The builder may not touch CI, secrets, or dependency/build output.
FORBIDDEN_PREFIXES = (".github/", ".git/", "node_modules/", "dist/", "build/")
FORBIDDEN_NAMES = (".env", ".npmrc")
CHECKS = ("install", "lint", "typecheck", "test", "build")
# Only these variables reach npm; AWS credentials and the like never do.
SAFE_ENV_KEYS = ("PATH", "LANG", "LC_ALL", "TZ")


class BuildLimitError(RuntimeError):
    pass


def forbidden_reason(path: str) -> str | None:
    if path.startswith(FORBIDDEN_PREFIXES):
        return f"{path} is in a protected folder"
    name = path.rsplit("/", 1)[-1]
    if name in FORBIDDEN_NAMES or name.startswith(".env."):
        return f"{path} may contain secrets"
    return None


@dataclass
class Workspace:
    root: Path
    deadline: float
    originals: dict[str, bytes | None] = field(default_factory=dict)
    check_runs: int = 0
    check_results: dict[str, dict] = field(default_factory=dict)

    def remember(self, rel: str, target: Path) -> None:
        if rel not in self.originals:
            self.originals[rel] = target.read_bytes() if target.exists() else None

    def changes(self) -> dict[str, str | None]:
        """Files that differ from the snapshot: new content, or None if deleted."""
        changed = {}
        for rel, original in self.originals.items():
            target = self.root / rel
            current = target.read_bytes() if target.exists() else None
            if current != original:
                changed[rel] = None if current is None else current.decode("utf-8")
        return changed

    def limit_problem(self) -> str | None:
        changes = self.changes()
        if len(changes) > MAX_CHANGED_FILES:
            return f"Change touches {len(changes)} files (limit {MAX_CHANGED_FILES})"
        size = sum(len(c.encode()) for c in changes.values() if c)
        if size > MAX_CHANGE_BYTES:
            return f"Change is {size} bytes (limit {MAX_CHANGE_BYTES})"
        return None

    def check_limits(self) -> None:
        if problem := self.limit_problem():
            raise BuildLimitError(problem)

    def scripts(self) -> dict:
        try:
            return json.loads((self.root / "package.json").read_text()).get("scripts", {})
        except (OSError, ValueError):
            return {}

    def run(self, name: str) -> dict:
        """Run one check; returns {passed, output} with the tail of the output."""
        if name == "install":
            command = ["npm", "ci", "--no-audit", "--no-fund"]
        elif name in self.scripts():
            command = ["npm", "run", name]
        else:
            return {"passed": None, "output": f"No '{name}' script in package.json"}
        env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
        env.update({"CI": "1", "HOME": "/tmp", "npm_config_cache": "/tmp/.npm"})
        timeout = max(1, min(CHECK_TIMEOUT_SECONDS, self.deadline - time.monotonic()))
        try:
            completed = subprocess.run(
                command,
                cwd=self.root,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = completed.stdout + completed.stderr
            result = {"passed": completed.returncode == 0, "output": output[-OUTPUT_TAIL_CHARS:]}
        except subprocess.TimeoutExpired:
            result = {"passed": False, "output": f"'{name}' timed out after {int(timeout)}s"}
        except FileNotFoundError:
            result = {"passed": False, "output": "npm is not installed in this environment"}
        self.check_results[name] = result
        return result


def make_tools(workspace: Workspace, budget: repo_tools.ToolBudget) -> list:
    root = workspace.root.resolve()
    list_files, read_file, search = repo_tools.make_tools(root, budget)

    def target_for(path: str) -> tuple[str, Path] | str:
        target = repo_tools._resolve(root, path)
        if target is None or target == root:
            return f"Invalid path: {path!r}"
        rel = target.relative_to(root).as_posix()
        if reason := forbidden_reason(rel):
            return f"Refused: {reason}"
        return rel, target

    def out_of_time() -> str | None:
        if time.monotonic() > workspace.deadline:
            return "Time budget exhausted. Stop and write your summary now."
        return None

    @tool
    def write_file(path: str, content: str) -> str:
        """Create or overwrite a text file in the repository.

        Args:
            path: File path relative to the repository root.
            content: The complete new file content.
        """
        if stop := budget.spend() or out_of_time():
            return stop
        resolved = target_for(path)
        if isinstance(resolved, str):
            return resolved
        rel, target = resolved
        workspace.remember(rel, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if problem := workspace.limit_problem():
            return f"Wrote {rel}, but {problem}. Undo or shrink changes."
        return f"Wrote {rel} ({len(content)} characters)"

    @tool
    def edit_file(path: str, old_text: str, new_text: str) -> str:
        """Replace one exact, unique snippet of text in an existing file.

        Args:
            path: File path relative to the repository root.
            old_text: Text to find; must appear exactly once in the file.
            new_text: Replacement text.
        """
        if stop := budget.spend() or out_of_time():
            return stop
        resolved = target_for(path)
        if isinstance(resolved, str):
            return resolved
        rel, target = resolved
        if not target.is_file():
            return f"Not a file: {rel}; use write_file to create it"
        text = target.read_text(encoding="utf-8")
        count = text.count(old_text)
        if count != 1:
            return f"old_text appears {count} times in {rel}; it must appear exactly once"
        workspace.remember(rel, target)
        target.write_text(text.replace(old_text, new_text), encoding="utf-8")
        if problem := workspace.limit_problem():
            return f"Edited {rel}, but {problem}. Undo or shrink changes."
        return f"Edited {rel}"

    @tool
    def delete_file(path: str) -> str:
        """Delete a file from the repository.

        Args:
            path: File path relative to the repository root.
        """
        if stop := budget.spend() or out_of_time():
            return stop
        resolved = target_for(path)
        if isinstance(resolved, str):
            return resolved
        rel, target = resolved
        if not target.is_file():
            return f"Not a file: {rel}"
        workspace.remember(rel, target)
        target.unlink()
        return f"Deleted {rel}"

    @tool
    def run_check(name: str) -> str:
        """Run one of the repository's checks and return its output.

        Args:
            name: One of "install", "lint", "typecheck", "test", "build".
                Run "install" once before the others.
        """
        if stop := budget.spend() or out_of_time():
            return stop
        if name not in CHECKS:
            return f"Unknown check {name!r}; choose from {', '.join(CHECKS)}"
        if workspace.check_runs >= MAX_CHECK_RUNS:
            return "Check budget exhausted. Stop and write your summary now."
        workspace.check_runs += 1
        result = workspace.run(name)
        status = {True: "PASSED", False: "FAILED", None: "SKIPPED"}[result["passed"]]
        return f"{name}: {status}\n{result['output']}"

    return [list_files, read_file, search, write_file, edit_file, delete_file, run_check]
