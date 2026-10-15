import pytest

from agents import planner


def test_system_prompt_covers_required_sections():
    for section in [
        "Objective",
        "Implementation steps",
        "Files likely to change",
        "Testing requirements",
        "Potential risks",
    ]:
        assert section in planner.SYSTEM_PROMPT


def test_build_planner_requires_model_id(monkeypatch):
    monkeypatch.delenv("BEDROCK_MODEL_ID", raising=False)
    with pytest.raises(KeyError):
        planner.build_planner()


def test_build_planner_uses_env_config(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    agent = planner.build_planner()
    assert agent.model.config["model_id"] == "test-model"
    assert agent.system_prompt == planner.SYSTEM_PROMPT


def test_build_planner_caps_output_tokens(monkeypatch):
    monkeypatch.setenv("BEDROCK_MODEL_ID", "test-model")
    monkeypatch.delenv("PLANNER_MAX_TOKENS", raising=False)
    assert planner.build_planner().model.config["max_tokens"] == planner.DEFAULT_MAX_TOKENS

    monkeypatch.setenv("PLANNER_MAX_TOKENS", "512")
    assert planner.build_planner().model.config["max_tokens"] == 512


@pytest.mark.parametrize("raw", ["4k", "0", "-5"])
def test_invalid_max_tokens_has_clear_error(monkeypatch, raw):
    monkeypatch.setenv("PLANNER_MAX_TOKENS", raw)
    with pytest.raises(ValueError, match="PLANNER_MAX_TOKENS must be a positive integer"):
        planner.max_tokens()


def test_blank_max_tokens_uses_default(monkeypatch):
    monkeypatch.setenv("PLANNER_MAX_TOKENS", "")
    assert planner.max_tokens() == planner.DEFAULT_MAX_TOKENS


class ScriptedAgent:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.replies.pop(0)


def test_reasoning_only_turn_is_nudged_until_text():
    agent = ScriptedAgent(["", "  ", "the plan"])
    assert planner.run_until_text(agent, "ticket") == "the plan"
    assert agent.prompts == ["ticket", planner.NUDGE, planner.NUDGE]


def test_persistently_empty_output_raises():
    agent = ScriptedAgent([""] * (planner.MAX_NUDGES + 1))
    with pytest.raises(planner.EmptyPlanError):
        planner.run_until_text(agent, "ticket")


def test_hard_stopped_run_raises_over_budget():
    from agents import repo_tools

    budget = repo_tools.ToolBudget(stopped=True)
    with pytest.raises(planner.OverBudgetError):
        planner.run_until_text(ScriptedAgent([""]), "ticket", budget)
