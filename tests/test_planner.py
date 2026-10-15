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
