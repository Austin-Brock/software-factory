"""Read-only repository access for the planner.

A snapshot of the repo is downloaded as a tarball into a temporary directory,
and the planner explores it with three tools locked to that directory. Budgets
cap how much a single plan can read, so a confused model can't loop forever.
"""

import io
import os
import re
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from strands import tool

REPO_PATTERN = re.compile(r"^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$")
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_MEMBERS = 5_000
MAX_FILE_CHARS = 40_000
MAX_LISTED = 300
MAX_MATCHES = 50
SKIPPED_DIRS = {".git", "node_modules", "dist", "build", ".venv", "__pycache__", "cdk.out"}


class RepoError(RuntimeError):
    pass


@dataclass
class ToolBudget:
    max_calls: int = 25
    max_bytes: int = 200_000
    # Calls tolerated after the budget is spent before the run is cancelled.
    grace_calls: int = 5
    calls: int = 0
    bytes_read: int = 0
    files_read: list[str] = field(default_factory=list)
    hard_stop: Callable[[], None] | None = None
    stopped: bool = False

    def charge(self, output: str) -> str:
        """Count tool output toward the byte budget and pass it through."""
        self.bytes_read += len(output)
        return output

    def spend(self) -> str | None:
        """Count one tool call; return a stop message once the budget is used up."""
        self.calls += 1
        exhausted = self.calls > self.max_calls or self.bytes_read >= self.max_bytes
        if exhausted and self.calls > self.max_calls + self.grace_calls:
            # The model ignored the stop message; cancel instead of looping.
            self.stopped = True
            if self.hard_stop:
                self.hard_stop()
        if exhausted:
            return "Tool budget exhausted. Stop exploring and write the plan now."
        return None


def _download(repo: str, ref: str) -> bytes:
    url = f"https://codeload.github.com/{repo}/tar.gz/{ref}"
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(MAX_DOWNLOAD_BYTES + 1)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise RepoError(f"{repo} is larger than {MAX_DOWNLOAD_BYTES // 2**20} MB")
    return data


def extract(data: bytes, destination: Path) -> Path:
    """Safely unpack a GitHub tarball; returns the repo root inside destination."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_MEMBERS:
            raise RepoError("Repository has too many files to snapshot")
        if sum(m.size for m in members) > MAX_EXTRACTED_BYTES:
            raise RepoError("Repository is too large to snapshot")
        # The "data" filter rejects absolute paths, links outside the tree, and devices.
        archive.extractall(destination, filter="data")
    roots = [p for p in destination.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RepoError("Unexpected archive layout")
    return roots[0]


@contextmanager
def snapshot(repo: str, ref: str = "HEAD"):
    """Download the repo at ref (HEAD = its default branch) into a temp dir."""
    if not REPO_PATTERN.match(repo):
        raise RepoError(f"Invalid repository name {repo!r}")
    data = _download(repo, ref)
    with tempfile.TemporaryDirectory(prefix="repo-") as tmp:
        yield extract(data, Path(tmp))


def _resolve(root: Path, path: str) -> Path | None:
    """Resolve a repo-relative path, refusing anything outside the snapshot."""
    candidate = (root / path.lstrip("/")).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    return candidate


def _visible_files(base: Path):
    for directory, dirnames, filenames in os.walk(base):
        # Prune in place so os.walk never descends into dependency/build folders.
        dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS)
        for name in sorted(filenames):
            yield Path(directory) / name


def make_tools(root: Path, budget: ToolBudget) -> list:
    root = root.resolve()

    def rel(path: Path) -> str:
        return path.relative_to(root).as_posix()

    @tool
    def list_files(path: str = "") -> str:
        """List files in the repository, recursively, under a directory.

        Args:
            path: Directory relative to the repository root; empty for the root.
        """
        if stop := budget.spend():
            return stop
        base = _resolve(root, path)
        if base is None or not base.is_dir():
            return f"Not a directory: {path!r}"
        files = [rel(p) for p in _visible_files(base)]
        listing = "\n".join(files[:MAX_LISTED])
        if len(files) > MAX_LISTED:
            listing += f"\n… {len(files) - MAX_LISTED} more; list a subdirectory"
        return budget.charge(listing or "(empty)")

    @tool
    def read_file(path: str) -> str:
        """Read one text file from the repository.

        Args:
            path: File path relative to the repository root.
        """
        if stop := budget.spend():
            return stop
        target = _resolve(root, path)
        if target is None or not target.is_file():
            return f"Not a file: {path!r}"
        raw = target.read_bytes()
        if b"\x00" in raw[:8192]:
            return f"{path} is a binary file"
        text = raw.decode("utf-8", errors="replace")
        remaining = budget.max_bytes - budget.bytes_read
        limit = min(MAX_FILE_CHARS, remaining)
        budget.bytes_read += min(len(text), limit)
        if rel(target) not in budget.files_read:
            budget.files_read.append(rel(target))
        if len(text) > limit:
            return text[:limit] + f"\n… truncated ({len(text) - limit} more characters)"
        return text

    @tool
    def search(text: str, path: str = "") -> str:
        """Find lines containing text (case-insensitive) in repository files.

        Args:
            text: Plain text to look for (not a regular expression).
            path: Directory relative to the repository root to search; empty for all.
        """
        if stop := budget.spend():
            return stop
        base = _resolve(root, path)
        if base is None or not base.is_dir():
            return f"Not a directory: {path!r}"
        needle = text.casefold()
        matches = []
        for file in _visible_files(base):
            try:
                lines = file.read_text(encoding="utf-8").splitlines()
            except (UnicodeDecodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if needle in line.casefold():
                    matches.append(f"{rel(file)}:{number}: {line.strip()[:200]}")
                    if len(matches) >= MAX_MATCHES:
                        return budget.charge("\n".join(matches) + "\n… more matches; narrow the search")
        return budget.charge("\n".join(matches) or "No matches")

    return [list_files, read_file, search]
