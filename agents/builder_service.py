"""AgentCore Runtime entrypoint for the builder agent.

Returns the change set for the caller to commit; this container never holds
GitHub credentials. Run locally with `python -m agents.builder_service`.
"""

import tarfile
from typing import Annotated

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from botocore.exceptions import ClientError
from pydantic import BaseModel, StringConstraints, ValidationError
from strands.types.exceptions import MaxTokensReachedException, ModelThrottledException

from agents import builder, builder_tools, planner, repo_tools
from agents.planner_service import ALLOWED_REPOS, MAX_TICKET_CHARS, bedrock_error, error

app = BedrockAgentCoreApp()


class BuildRequest(BaseModel):
    ticket: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TICKET_CHARS)
    ]
    plan: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40_000)]
    repo: Annotated[str, StringConstraints(pattern=repo_tools.REPO_PATTERN.pattern)]


@app.entrypoint
def invoke(payload: dict):
    try:
        request = BuildRequest.model_validate(payload)
    except ValidationError as exc:
        return error("Invalid request", exc.errors(include_url=False), 400)
    if request.repo not in ALLOWED_REPOS:
        return error("Invalid request", f"Repository {request.repo} is not allowed", 400)
    try:
        return builder.build_ticket(request.ticket, request.plan, request.repo)
    except builder_tools.BuildLimitError as exc:
        return error("Change exceeded the size limits", str(exc), 422)
    except planner.OverBudgetError:
        return error("Builder exceeded its tool budget", "Split the ticket into smaller pieces.", 422)
    except MaxTokensReachedException:
        return error("Builder exceeded the output token cap", "Raise PLANNER_MAX_TOKENS.", 422)
    except planner.EmptyPlanError:
        return error("Builder returned no summary", "Relabel the ticket to try again.", 502)
    except (repo_tools.RepoError, tarfile.TarError, OSError) as exc:
        return error("Could not read the repository", str(exc) or type(exc).__name__, 502)
    except ModelThrottledException:
        return error("Bedrock is busy", "Retry later.", 429)
    except ClientError as exc:
        return bedrock_error(exc)


if __name__ == "__main__":
    app.run()
