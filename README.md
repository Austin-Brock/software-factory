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

## Run the planner as a service (AgentCore contract)

```bash
python -m agents.planner_service   # listens on :8080
curl localhost:8080/ping
curl -X POST localhost:8080/invocations \
  -H 'content-type: application/json' \
  -d '{"ticket": "Add a dark mode toggle that persists between sessions."}'
```

Each request builds a fresh agent, so tickets never share conversation history.

## Cost limits

| Setting (`.env`) | Default | Purpose |
| --- | --- | --- |
| `PLANNER_MAX_TOKENS` | 4096 | Max output tokens per plan |
| `PLANNER_MAX_TICKET_CHARS` | 20000 | Max ticket length accepted by the service |

## Tests

```bash
pytest
```

## Roadmap

1. Planning agent running locally against Bedrock ← current
2. Deploy to Bedrock AgentCore via CDK
3. Linear tickets trigger the planner automatically
