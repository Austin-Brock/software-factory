# software-factory
A lightweight, autonomous software factory powered by AWS Bedrock AgentCore, with Linear as the task management system and GitHub as the code repository.

## Layout

| Path | Purpose |
| --- | --- |
| `agents/` | Strands agents (starting with `planner.py`) |
| `integrations/` | External connectors: `linear/` (webhook receiver + worker Lambdas) |
| `orchestration/` | Wiring that routes tickets to agents |
| `workspace/` | Scratch area for agent working copies |
| `infrastructure/` | CDK app for AgentCore deployment |
| `scripts/` | Operator scripts (e.g. invoke the deployed planner) |
| `tests/` | pytest suite |

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt   # runtime deps + pytest
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

## Run the planner container

AgentCore runs the ARM64 image built from `Dockerfile`. `.dockerignore` keeps `.env` and everything except `agents/` and `requirements.txt` out of it.

```bash
docker build -t software-factory-planner:local .

# Local test only: pass short-lived credentials from your SSO session.
# In AWS, the container gets permissions from its IAM role instead.
eval "$(aws configure export-credentials --profile software-factory --format env)"
docker run --rm -p 8080:8080 \
  -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
  -e AWS_REGION=us-east-1 -e BEDROCK_MODEL_ID=openai.gpt-oss-120b-1:0 \
  software-factory-planner:local
```

## Cost limits

| Setting (`.env`) | Default | Purpose |
| --- | --- | --- |
| `PLANNER_MAX_TOKENS` | 4096 | Max output tokens per plan |
| `PLANNER_MAX_TICKET_CHARS` | 20000 | Max ticket length accepted by the service |

## Call the deployed planner

After deploying `infrastructure/` (see its README):

```bash
python -m scripts.invoke_planner "Add a dark mode toggle that persists between sessions."
```

The script finds the `software_factory_planner` runtime by name (or uses `PLANNER_RUNTIME_ARN`) and starts a fresh session per ticket.

## Tests

```bash
pytest
```

## Roadmap

1. ✅ Planning agent running locally against Bedrock
2. ✅ Deploy to Bedrock AgentCore via CDK
3. ✅ Linear: adding the `factory:plan` label posts a plan as a comment (setup in `infrastructure/README.md`)
4. Planner reads the target repo's code so plans name real files ← next
