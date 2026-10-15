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
