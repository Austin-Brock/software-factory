import os

from dotenv import load_dotenv
from strands import Agent
from strands.models import BedrockModel

load_dotenv()

SYSTEM_PROMPT = """
You are a senior software engineer working
inside an autonomous software factory.
Your job is to analyze development tickets
and produce implementation plans.
For every ticket, provide:
1. Objective
2. Implementation steps
3. Files likely to change
4. Testing requirements
5. Potential risks
Do not modify source code.
"""


def build_planner() -> Agent:
    model = BedrockModel(
        model_id=os.environ["BEDROCK_MODEL_ID"],
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
    )
    return Agent(model=model, system_prompt=SYSTEM_PROMPT)


if __name__ == "__main__":
    ticket = """
    Add a dark mode toggle to the application.
    The user's preference should persist
    between sessions.
    """
    planner = build_planner()
    # Strands streams the response to stdout as it generates.
    planner(ticket)
