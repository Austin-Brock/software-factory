import os
from pathlib import Path

import aws_cdk as cdk
from dotenv import load_dotenv

from stacks.planner_stack import PlannerStack

# Personal settings such as the alert email live in the gitignored repo .env.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = cdk.App()

budget_alert_email = app.node.try_get_context("budgetAlertEmail") or os.environ.get(
    "BUDGET_ALERT_EMAIL"
)
if not budget_alert_email:
    raise SystemExit("Set BUDGET_ALERT_EMAIL in the repo .env (or pass -c budgetAlertEmail=...).")

PlannerStack(
    app,
    "SoftwareFactoryPlanner",
    model_id=app.node.get_context("bedrockModelId"),
    max_tokens=app.node.get_context("plannerMaxTokens"),
    max_ticket_chars=app.node.get_context("plannerMaxTicketChars"),
    budget_name=app.node.get_context("budgetName"),
    budget_alert_email=budget_alert_email,
    linear_trigger_label=app.node.get_context("linearTriggerLabel"),
    linear_team_repos=app.node.get_context("linearTeamRepos"),
    env=cdk.Environment(region="us-east-1"),
)

cdk.Tags.of(app).add("project", "software-factory")

app.synth()
