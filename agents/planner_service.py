"""AgentCore Runtime entrypoint for the planning agent.

AgentCore calls POST /invocations with a JSON payload and polls GET /ping
for health. Run locally with `python -m agents.planner_service` (port 8080).
"""

import os

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import BaseModel, Field, ValidationError
from starlette.responses import JSONResponse

from agents import planner

# Bounds input size so oversized tickets can't run up the Bedrock bill.
MAX_TICKET_CHARS = int(os.environ.get("PLANNER_MAX_TICKET_CHARS", 20_000))

app = BedrockAgentCoreApp()


class PlanRequest(BaseModel):
    ticket: str = Field(min_length=1, max_length=MAX_TICKET_CHARS)


@app.entrypoint
def invoke(payload: dict):
    try:
        request = PlanRequest.model_validate(payload)
    except ValidationError as error:
        return JSONResponse(
            {"error": "Invalid request", "details": error.errors(include_url=False)},
            status_code=400,
        )
    if not request.ticket.strip():
        return JSONResponse(
            {"error": "Invalid request", "details": "ticket is blank"},
            status_code=400,
        )
    return {"plan": planner.plan_ticket(request.ticket)}


if __name__ == "__main__":
    app.run()
