import io
import tarfile
from pathlib import Path

import pytest

from agents import repo_tools


def make_tarball(files: dict[str, bytes], extra_members=()) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(f"repo-main/{name}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        for member in extra_members:
            archive.addfile(member)
    return buffer.getvalue()


@pytest.fixture
def repo(tmp_path):
    data = make_tarball(
        {
            "src/App.tsx": b"export default function App() {}\n",
            "src/index.css": b":root {\n  --accent: #4f46e5;\n}\n",
            "node_modules/react/index.js": b"--accent should not be searched\n",
            "public/logo.png": b"\x89PNG\x00\x00binary",
            "big.txt": b"x" * 50_000,
        }
    )
    return repo_tools.extract(data, tmp_path)


def tools(root, **budget):
    b = repo_tools.ToolBudget(**budget)
    list_files, read_file, search = repo_tools.make_tools(root, b)
    return list_files, read_file, search, b


def test_list_files_skips_dependency_folders(repo):
    list_files, *_ = tools(repo)
    listing = list_files("")
    assert "src/App.tsx" in listing
    assert "node_modules" not in listing


def test_read_file_records_what_was_read(repo):
    _, read_file, _, budget = tools(repo)
    assert "--accent" in read_file("src/index.css")
    assert budget.files_read == ["src/index.css"]


@pytest.mark.parametrize("path", ["../outside.txt", "/etc/passwd", "src/../../x"])
def test_paths_cannot_escape_the_snapshot(repo, path):
    (repo.parent / "outside.txt").write_text("secret")
    _, read_file, _, _ = tools(repo)
    assert read_file(path).startswith("Not a file")


def test_binary_and_large_files_are_guarded(repo):
    _, read_file, _, _ = tools(repo)
    assert "binary" in read_file("public/logo.png")
    text = read_file("big.txt")
    assert "truncated" in text and len(text) < repo_tools.MAX_FILE_CHARS + 100


def test_search_finds_lines_but_not_in_dependencies(repo):
    *_, search, _ = tools(repo)
    result = search("--ACCENT")
    assert "src/index.css:2:" in result
    assert "node_modules" not in result


def test_call_budget_stops_exploration(repo):
    list_files, *_ = tools(repo, max_calls=2)
    list_files("")
    list_files("")
    assert "budget exhausted" in list_files("")


def test_ignoring_the_budget_triggers_hard_stop(repo):
    cancelled = []
    budget = repo_tools.ToolBudget(max_calls=1, grace_calls=2, hard_stop=lambda: cancelled.append(True))
    list_files, *_ = repo_tools.make_tools(repo, budget)
    for _ in range(3):
        list_files("")
    assert not budget.stopped and not cancelled
    list_files("")
    assert budget.stopped and cancelled


def test_list_and_search_output_counts_toward_byte_budget(repo):
    list_files, _, search, budget = tools(repo)
    listing = list_files("")
    found = search("accent")
    assert budget.bytes_read == len(listing) + len(found)


def test_byte_budget_stops_exploration(repo):
    _, read_file, _, _ = tools(repo, max_bytes=1_000)
    read_file("big.txt")
    assert "budget exhausted" in read_file("src/App.tsx")


def test_archive_with_link_outside_tree_is_rejected(tmp_path):
    link = tarfile.TarInfo("repo-main/evil")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    data = make_tarball({"a.txt": b"a"}, extra_members=[link])
    with pytest.raises(tarfile.TarError):
        repo_tools.extract(data, tmp_path)


def test_snapshot_defaults_to_repo_default_branch(monkeypatch):
    seen = []
    monkeypatch.setattr(repo_tools, "_download", lambda repo, ref: seen.append(ref) or make_tarball({"a": b"a"}))
    with repo_tools.snapshot("Austin-Brock/factory-sandbox"):
        pass
    assert seen == ["HEAD"]


def test_invalid_repo_names_are_rejected():
    with pytest.raises(repo_tools.RepoError):
        with repo_tools.snapshot("not a repo/../x"):
            pass


def test_plan_ticket_with_repo_appends_files_examined(monkeypatch, tmp_path):
    from contextlib import contextmanager

    from agents import planner

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("app")

    @contextmanager
    def fake_snapshot(repo, ref="main"):
        yield Path(tmp_path)

    class FakeAgent:
        def __init__(self, tools):
            self.tools = tools

        def cancel(self):
            pass

        def __call__(self, ticket):
            self.tools[1]("src/App.tsx")  # read_file
            return "the plan"

    monkeypatch.setattr(planner.repo_tools, "snapshot", fake_snapshot)
    monkeypatch.setattr(
        planner, "build_planner", lambda stream_to_stdout=True, tools=None: FakeAgent(tools)
    )
    plan = planner.plan_ticket("Add dark mode", "Austin-Brock/factory-sandbox")
    assert plan.startswith("the plan")
    assert "_Files examined: `src/App.tsx`_" in plan
