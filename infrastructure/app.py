import aws_cdk as cdk

from stacks.planner_stack import PlannerStack

app = cdk.App()

PlannerStack(
    app,
    "SoftwareFactoryPlanner",
    model_id=app.node.get_context("bedrockModelId"),
    max_tokens=app.node.get_context("plannerMaxTokens"),
    budget_name=app.node.get_context("budgetName"),
    budget_alert_email=app.node.get_context("budgetAlertEmail"),
    env=cdk.Environment(region="us-east-1"),
)

cdk.Tags.of(app).add("project", "software-factory")

app.synth()
