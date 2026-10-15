"""Send a ticket to the deployed planner on AgentCore and print the plan.

Usage: python -m scripts.invoke_planner "Ticket text"
"""

import json
import os
import sys
import uuid

import boto3
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

RUNTIME_NAME = "software_factory_planner"
# Plans arrive in one response after generation finishes, which can exceed
# botocore's 60s default. Retrying would start a second billed invocation.
INVOKE_CONFIG = Config(read_timeout=600, retries={"total_max_attempts": 1})
DEFAULT_TICKET = "Add a dark mode toggle that persists between sessions."


def find_runtime_arn(control) -> str:
    paginator = control.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for runtime in page["agentRuntimes"]:
            if runtime["agentRuntimeName"] == RUNTIME_NAME:
                return runtime["agentRuntimeArn"]
    raise SystemExit(f"Runtime {RUNTIME_NAME!r} not found; is the stack deployed?")


def main() -> None:
    ticket = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TICKET
    region = os.environ.get("AWS_REGION", "us-east-1")
    arn = os.environ.get("PLANNER_RUNTIME_ARN") or find_runtime_arn(
        boto3.client("bedrock-agentcore-control", region_name=region)
    )

    response = boto3.client("bedrock-agentcore", region_name=region, config=INVOKE_CONFIG).invoke_agent_runtime(
        agentRuntimeArn=arn,
        # Each ticket gets its own session so nothing carries over between tickets.
        runtimeSessionId=str(uuid.uuid4()),
        payload=json.dumps({"ticket": ticket}).encode(),
        contentType="application/json",
        accept="application/json",
    )
    body = json.loads(response["response"].read())
    if "plan" not in body:
        raise SystemExit(f"Planner returned an error: {body}")
    print(body["plan"])


if __name__ == "__main__":
    main()
