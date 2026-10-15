import json
import time

import pytest

from agents import builder_tools, repo_tools


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.tsx").write_text("const a = 1\n")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push\n")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "vitest run", "lint": "oxlint"}}))
    return builder_tools.Workspace(root=tmp_path, deadline=time.monotonic() + 600)


def tools(workspace, **budget):
    b = repo_tools.ToolBudget(**budget)
    named = {t.tool_name: t for t in builder_tools.make_tools(workspace, b)}
    return named, b


def test_edits_are_tracked_as_changes(workspace):
    t, _ = tools(workspace)
    t["edit_file"]("src/App.tsx", "const a = 1", "const a = 2")
    t["write_file"]("src/New.tsx", "export {}\n")
    assert workspace.changes() == {"src/App.tsx": "const a = 2\n", "src/New.tsx": "export {}\n"}


def test_reverting_an_edit_leaves_no_change(workspace):
    t, _ = tools(workspace)
    t["edit_file"]("src/App.tsx", "const a = 1", "const a = 2")
    t["edit_file"]("src/App.tsx", "const a = 2", "const a = 1")
    assert workspace.changes() == {}


def test_delete_is_recorded_as_none(workspace):
    t, _ = tools(workspace)
    t["delete_file"]("src/App.tsx")
    assert workspace.changes() == {"src/App.tsx": None}


@pytest.mark.parametrize(
    "path", [".github/workflows/ci.yml", ".env", "config/.env.production", "node_modules/x.js", ".npmrc"]
)
def test_protected_paths_are_refused(workspace, path):
    t, _ = tools(workspace)
    assert t["write_file"](path, "pwned").startswith("Refused")
    assert workspace.changes() == {}


def test_writes_cannot_escape_the_workspace(workspace):
    t, _ = tools(workspace)
    assert "Invalid path" in t["write_file"]("../escape.txt", "x")
    assert not (workspace.root.parent / "escape.txt").exists()


def test_absolute_paths_stay_inside_the_workspace(workspace):
    t, _ = tools(workspace)
    t["write_file"]("/etc/hosts-test", "x")
    assert (workspace.root / "etc" / "hosts-test").read_text() == "x"
    assert workspace.changes() == {"etc/hosts-test": "x"}


def test_edit_requires_a_unique_match(workspace):
    (workspace.root / "src" / "Dup.ts").write_text("x\nx\n")
    t, _ = tools(workspace)
    assert "appears 2 times" in t["edit_file"]("src/Dup.ts", "x", "y")


def test_size_limit_is_reported_to_the_model(workspace, monkeypatch):
    monkeypatch.setattr(builder_tools, "MAX_CHANGE_BYTES", 10)
    t, _ = tools(workspace)
    assert "limit" in t["write_file"]("src/Big.ts", "x" * 50)
    with pytest.raises(builder_tools.BuildLimitError):
        workspace.check_limits()


def test_file_count_limit(workspace, monkeypatch):
    monkeypatch.setattr(builder_tools, "MAX_CHANGED_FILES", 1)
    t, _ = tools(workspace)
    t["write_file"]("src/A.ts", "a")
    assert "limit" in t["write_file"]("src/B.ts", "b")


def test_only_known_checks_run(workspace):
    t, _ = tools(workspace)
    assert "Unknown check" in t["run_check"]("rm -rf /")
    assert "SKIPPED" in t["run_check"]("build")  # no build script in package.json


def test_checks_never_see_aws_credentials(workspace, monkeypatch):
    seen = {}

    def fake_run(command, cwd, env, **kwargs):
        seen.update(command=command, env=env)

        class Done:
            returncode = 0
            stdout = "ok"
            stderr = ""

        return Done()

    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "token")
    monkeypatch.setattr(builder_tools.subprocess, "run", fake_run)
    assert workspace.run("test")["passed"] is True
    assert seen["command"] == ["npm", "run", "test"]
    assert not any(k.startswith("AWS_") for k in seen["env"])


def test_check_run_budget(workspace, monkeypatch):
    monkeypatch.setattr(builder_tools, "MAX_CHECK_RUNS", 1)
    monkeypatch.setattr(workspace, "run", lambda name: {"passed": True, "output": ""})
    t, _ = tools(workspace)
    t["run_check"]("test")
    assert "budget exhausted" in t["run_check"]("test")


def test_time_budget_stops_edits(workspace):
    workspace.deadline = time.monotonic() - 1
    t, _ = tools(workspace)
    assert "Time budget exhausted" in t["write_file"]("src/A.ts", "a")


LEAKED = '<analysis>Now CSS.</analysis to=functions.edit_file code<|message|>{"path":"src/index.css"}'


@pytest.mark.parametrize("text", [LEAKED, "to=functions.write_file", "x <|call|> y", "<|channel|>final"])
def test_leaked_tool_calls_are_detected(text):
    assert builder_tools.has_leaked_tool_call(text)


def test_normal_markdown_is_not_flagged():
    assert not builder_tools.has_leaked_tool_call("## Summary\nAdded a `ThemeToggle` component <with> tests.")


def test_finish_requires_a_clean_summary_and_real_changes(workspace):
    finished = []
    workspace.on_finish = lambda: finished.append(True)
    t, _ = tools(workspace)
    assert "not changed any files" in t["finish"]("Done")
    t["edit_file"]("src/App.tsx", "const a = 1", "const a = 2")
    assert "rejected" in t["finish"](LEAKED)
    assert workspace.summary is None and not finished
    assert t["finish"]("## Summary\nChanged a.") == "Finished."
    assert workspace.summary == "## Summary\nChanged a." and finished == [True]


def test_package_json_scripts_cannot_be_changed(workspace):
    t, _ = tools(workspace)
    original = (workspace.root / "package.json").read_text()
    result = t["edit_file"]("package.json", '"vitest run"', '"exit 0"')
    assert "scripts may not be changed" in result
    with pytest.raises(builder_tools.BuildLimitError, match="scripts"):
        workspace.check_limits()
    t["write_file"]("package.json", original)
    workspace.check_limits()


def test_dependency_changes_in_package_json_are_allowed(workspace):
    t, _ = tools(workspace)
    data = json.loads((workspace.root / "package.json").read_text())
    data["dependencies"] = {"left-pad": "1.3.0"}
    assert "may not" not in t["write_file"]("package.json", json.dumps(data))
    workspace.check_limits()


def test_crlf_files_keep_their_line_endings(workspace):
    path = workspace.root / "src" / "Win.ts"
    path.write_bytes(b"line one\r\nline two\r\n")
    t, _ = tools(workspace)
    t["edit_file"]("src/Win.ts", "line one\nline two", "line one\nline 2")
    assert path.read_bytes() == b"line one\r\nline 2\r\n"


def test_reading_a_lot_does_not_block_editing(workspace):
    t, budget = tools(workspace, max_bytes=10)
    t["read_file"]("src/App.tsx")
    budget.bytes_read = 10_000
    assert t["edit_file"]("src/App.tsx", "const a = 1", "const a = 2") == "Edited src/App.tsx"
    assert "budget exhausted" in t["read_file"]("src/App.tsx")


def test_parse_leaked_tool_call():
    text = 'Now lint.</analysis to=functions.run_check<|channel|>commentary code<|message|>{"name":"lint"} trailing'
    assert builder_tools.parse_leaked_tool_call(text) == ("run_check", {"name": "lint"})
    nested = 'x to=functions.edit_file code<|message|>{"path":"a","old_text":"{","new_text":"}"}'
    assert builder_tools.parse_leaked_tool_call(nested) == ("edit_file", {"path": "a", "old_text": "{", "new_text": "}"})
    assert builder_tools.parse_leaked_tool_call("plain text") is None
    assert builder_tools.parse_leaked_tool_call("to=functions.x<|message|>{broken") is None
