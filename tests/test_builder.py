import pytest

from agents import builder, planner, repo_tools


class FakeWorkspace:
    summary = None


class ScriptedAgent:
    """Calls finish (sets the summary) on the given turn, if any."""

    def __init__(self, workspace, finish_on_turn=None):
        self.workspace = workspace
        self.finish_on_turn = finish_on_turn
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if len(self.prompts) == self.finish_on_turn:
            self.workspace.summary = "done"
        return '<analysis>to=functions.edit_file<|message|>{}'


def test_finishing_on_first_turn_needs_no_nudge():
    ws = FakeWorkspace()
    agent = ScriptedAgent(ws, finish_on_turn=1)
    builder.run_until_finished(agent, ws, repo_tools.ToolBudget(), "go")
    assert agent.prompts == ["go"]


def test_stopping_early_with_leaked_text_is_nudged():
    ws = FakeWorkspace()
    agent = ScriptedAgent(ws, finish_on_turn=3)
    builder.run_until_finished(agent, ws, repo_tools.ToolBudget(), "go")
    assert agent.prompts == ["go", builder.NUDGE, builder.NUDGE]


def test_never_finishing_fails_the_build():
    ws = FakeWorkspace()
    with pytest.raises(builder.IncompleteBuildError):
        builder.run_until_finished(ScriptedAgent(ws), ws, repo_tools.ToolBudget(), "go")


def test_hard_stop_wins_over_nudging():
    ws = FakeWorkspace()
    with pytest.raises(planner.OverBudgetError):
        builder.run_until_finished(ScriptedAgent(ws), ws, repo_tools.ToolBudget(stopped=True), "go")


def test_missing_base_commit_fails_before_building(monkeypatch, tmp_path):
    from contextlib import contextmanager

    @contextmanager
    def no_sha(repo, ref="HEAD"):
        yield tmp_path, None

    monkeypatch.setattr(builder.repo_tools, "snapshot_at_commit", no_sha)
    monkeypatch.setattr(builder, "Agent", lambda **kw: pytest.fail("agent should not start"))
    with pytest.raises(repo_tools.RepoError, match="commit"):
        builder.build_ticket("t", "p", "a/b")


def test_final_checks_get_their_own_time_and_include_typecheck(monkeypatch, tmp_path):
    import json
    import time
    from contextlib import contextmanager

    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"lint": "x", "typecheck": "x", "test": "x"}}))

    @contextmanager
    def snap(repo, ref="HEAD"):
        yield tmp_path, "a" * 40

    runs = []

    class FakeAgent:
        def __init__(self, tools, **kw):
            self.tools = {t.tool_name: t for t in tools}

        def cancel(self):
            pass

        def __call__(self, prompt):
            self.tools["write_file"]("src/a.ts", "x")
            self.tools["finish"]("done")

    def fake_run(self, name):
        runs.append((name, self.deadline - time.monotonic()))
        return {"passed": True, "output": ""}

    monkeypatch.setattr(builder.repo_tools, "snapshot_at_commit", snap)
    monkeypatch.setattr(builder, "BedrockModel", lambda **kw: None)
    monkeypatch.setattr(builder, "Agent", lambda tools, **kw: FakeAgent(tools))
    monkeypatch.setattr(builder.builder_tools.Workspace, "run", fake_run)
    monkeypatch.setenv("BEDROCK_MODEL_ID", "m")
    result = builder.build_ticket("t", "p", "a/b")
    assert [name for name, _ in runs] == ["install", "lint", "typecheck", "test"]  # no build script
    assert all(remaining > builder.FINAL_CHECK_SECONDS - 5 for _, remaining in runs)
    assert result["passed"] is True


def test_leaked_call_is_executed_and_fed_back(tmp_path):
    import time

    from agents import builder_tools

    (tmp_path / "a.ts").write_text("old")
    ws = builder_tools.Workspace(root=tmp_path, deadline=time.monotonic() + 60)
    budget = repo_tools.ToolBudget()
    tools = builder_tools.make_tools(ws, budget)

    class LeakyAgent:
        def __init__(self):
            self.prompts = []

        def __call__(self, prompt):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                return 'Editing.</analysis to=functions.edit_file code<|message|>{"path":"a.ts","old_text":"old","new_text":"new"}'
            if len(self.prompts) == 2:
                return 'Done.</analysis to=functions.finish code<|message|>{"summary":"Changed a.ts"}'
            raise AssertionError("should have finished")

    agent = LeakyAgent()
    builder.run_until_finished(agent, ws, budget, "go", tools)
    assert (tmp_path / "a.ts").read_text() == "new"
    assert "I ran it for you" in agent.prompts[1] and "Edited a.ts" in agent.prompts[1]
    assert ws.summary == "Changed a.ts"
