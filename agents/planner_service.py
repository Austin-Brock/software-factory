"""AgentCore Runtime entrypoint for the planning agent.

AgentCore calls POST /invocations with a JSON payload and polls GET /ping
for health. Run locally with `python -m agents.planner_service` (port 8080).
"""

import logging
import os
import tarfile
from typing import Annotated

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.exceptions import ClientError
from pydantic import BaseModel, StringConstraints, ValidationError
from starlette.responses import JSONResponse
from strands.types.exceptions import MaxTokensReachedException, ModelThrottledException

from agents import planner, repo_tools

# Bounds input size so oversized tickets can't run up the Bedrock bill.
MAX_TICKET_CHARS = planner.env_positive_int("PLANNER_MAX_TICKET_CHARS", 20_000)
# Fail at startup, not on the first request, if the token cap is misconfigured.
planner.max_tokens()
# Repos the planner may download, so a ticket can't point it at arbitrary ones.
ALLOWED_REPOS = {
    repo.strip() for repo in os.environ.get("PLANNER_ALLOWED_REPOS", "").split(",") if repo.strip()
}
if not ALLOWED_REPOS:
    logging.getLogger(__name__).warning(
        "PLANNER_ALLOWED_REPOS is empty; requests that name a repo will be rejected"
    )

app = BedrockAgentCoreApp()


class PlanRequest(BaseModel):
    ticket: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TICKET_CHARS),
    ]
    repo: Annotated[str, StringConstraints(pattern=repo_tools.REPO_PATTERN.pattern)] | None = None


def error(message: str, details, status_code: int) -> JSONResponse:
    return JSONResponse({"error": message, "details": details}, status_code=status_code)


def bedrock_error(exc: ClientError) -> JSONResponse:
    # Return only the error code: the raw message includes role and model ARNs.
    code = exc.response.get("Error", {}).get("Code", "Unknown")
    if code == "AccessDeniedException":
        return error(
            "Bedrock refused the request",
            "Access denied. If the monthly budget was exceeded, the budget kill switch is engaged.",
            503,
        )
    if code == "ServiceUnavailableException":
        return error("Bedrock is busy", "Retry later.", 429)
    return error("Bedrock request failed", code, 502)


@app.entrypoint
def invoke(payload: dict):
    try:
        request = PlanRequest.model_validate(payload)
    except ValidationError as exc:
        return error("Invalid request", exc.errors(include_url=False), 400)
    if request.repo and request.repo not in ALLOWED_REPOS:
        return error("Invalid request", f"Repository {request.repo} is not allowed", 400)
    try:
        plan = planner.plan_ticket(request.ticket, request.repo)
    except MaxTokensReachedException:
        return error(
            "Plan exceeded the output token cap",
            "Raise PLANNER_MAX_TOKENS or split the ticket.",
            422,
        )
    except planner.OverBudgetError:
        return error(
            "Planner exceeded its exploration budget",
            "Narrow the ticket or raise the tool budget.",
            422,
        )
    except planner.EmptyPlanError:
        return error("Planner returned an empty plan", "Relabel the ticket to try again.", 502)
    except (repo_tools.RepoError, tarfile.TarError, OSError) as exc:
        return error("Could not read the repository", str(exc) or type(exc).__name__, 502)
    except ModelThrottledException:
        return error("Bedrock is busy", "Retry later.", 429)
    except ClientError as exc:
        return bedrock_error(exc)
    return {"plan": plan}


if __name__ == "__main__":
    app.run()
