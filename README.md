# software-factory
A lightweight, autonomous software factory powered by AWS Bedrock AgentCore, with Linear as the task management system and GitHub as the code repository.

## Layout

| Path | Purpose |
| --- | --- |
| `agents/` | Strands agents (starting with `planner.py`) |
| `integrations/` | Linear, GitHub, and other external connectors |
| `orchestration/` | Wiring that routes tickets to agents |
| `workspace/` | Scratch area for agent working copies |
| `infrastructure/` | CDK app for AgentCore deployment |
| `tests/` | pytest suite |

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then set BEDROCK_MODEL_ID
aws sts get-caller-identity   # confirm AWS credentials
```

## Run the planning agent

```bash
python -m agents.planner
```

## Tests

```bash
pytest
```

## Roadmap

1. Planning agent running locally against Bedrock ← current
2. Deploy to Bedrock AgentCore via CDK
3. Linear tickets trigger the planner automatically
