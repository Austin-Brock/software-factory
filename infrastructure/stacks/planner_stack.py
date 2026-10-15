from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_iam as iam
from constructs import Construct

from stacks.linear_integration import LinearIntegration

REPO_ROOT = Path(__file__).resolve().parents[2]

# Cross-region inference profile IDs carry a geography prefix.
INFERENCE_PROFILE_PREFIXES = ("us.", "us-gov.", "eu.", "apac.", "jp.", "au.", "ca.", "global.")


def model_resource_arns(stack: Stack, model_id: str) -> list[str]:
    """ARNs the runtime needs to invoke one model, and nothing else."""
    for prefix in INFERENCE_PROFILE_PREFIXES:
        if model_id.startswith(prefix):
            base_model_id = model_id[len(prefix):]
            return [
                f"arn:aws:bedrock:{stack.region}:{stack.account}:inference-profile/{model_id}",
                # A profile routes to the base model in any region it covers.
                f"arn:aws:bedrock:*::foundation-model/{base_model_id}",
            ]
    return [f"arn:aws:bedrock:{stack.region}::foundation-model/{model_id}"]


class PlannerStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        model_id: str,
        max_tokens: str,
        max_ticket_chars: str,
        budget_name: str,
        budget_alert_email: str,
        linear_trigger_label: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        artifact = agentcore.AgentRuntimeArtifact.from_asset(
            str(REPO_ROOT),
            platform=ecr_assets.Platform.LINUX_ARM64,
        )

        self.runtime = agentcore.Runtime(
            self,
            "PlannerRuntime",
            runtime_name="software_factory_planner",
            description="Turns tickets into implementation plans",
            agent_runtime_artifact=artifact,
            # Public network avoids a VPC and its always-on NAT gateway cost.
            network_configuration=agentcore.RuntimeNetworkConfiguration.using_public_network(),
            # Short limits so forgotten sessions stop accruing charges.
            lifecycle_configuration=agentcore.LifecycleConfiguration(
                idle_runtime_session_timeout=Duration.minutes(5),
                max_lifetime=Duration.hours(1),
            ),
            environment_variables={
                "BEDROCK_MODEL_ID": model_id,
                "PLANNER_MAX_TOKENS": max_tokens,
                "PLANNER_MAX_TICKET_CHARS": max_ticket_chars,
            },
        )

        self.runtime.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "bedrock:InvokeModel",
                    "bedrock:InvokeModelWithResponseStream",
                ],
                resources=model_resource_arns(self, model_id),
            )
        )

        self.linear = LinearIntegration(
            self, "Linear", runtime=self.runtime, trigger_label=linear_trigger_label
        )

        self._add_budget_kill_switch(
            budget_name,
            budget_alert_email,
            roles=[self.runtime.role, self.linear.worker.role],
        )

    def _add_budget_kill_switch(
        self, budget_name: str, alert_email: str, roles: list[iam.IRole]
    ) -> None:
        """At 100% of the monthly budget, cut agents off from Bedrock and AgentCore."""
        deny_bedrock = iam.ManagedPolicy(
            self,
            "BudgetKillSwitchPolicy",
            description="Attached automatically when the monthly budget is exceeded",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.DENY,
                    actions=["bedrock:*", "bedrock-agentcore:InvokeAgentRuntime"],
                    resources=["*"],
                )
            ],
        )

        # Budgets may attach exactly this policy to exactly these roles.
        budgets_role = iam.Role(
            self,
            "BudgetActionRole",
            # Only budget actions in this account may assume the role.
            assumed_by=iam.ServicePrincipal(
                "budgets.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
        )
        budgets_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
                resources=[role.role_arn for role in roles],
                conditions={
                    "ArnEquals": {"iam:PolicyARN": deny_bedrock.managed_policy_arn}
                },
            )
        )

        budgets.CfnBudgetsAction(
            self,
            "BudgetKillSwitch",
            budget_name=budget_name,
            action_type="APPLY_IAM_POLICY",
            approval_model="AUTOMATIC",
            notification_type="ACTUAL",
            action_threshold=budgets.CfnBudgetsAction.ActionThresholdProperty(
                type="PERCENTAGE", value=100
            ),
            execution_role_arn=budgets_role.role_arn,
            definition=budgets.CfnBudgetsAction.DefinitionProperty(
                iam_action_definition=budgets.CfnBudgetsAction.IamActionDefinitionProperty(
                    policy_arn=deny_bedrock.managed_policy_arn,
                    roles=[role.role_name for role in roles],
                )
            ),
            subscribers=[
                budgets.CfnBudgetsAction.SubscriberProperty(
                    type="EMAIL", address=alert_email
                )
            ],
        )
