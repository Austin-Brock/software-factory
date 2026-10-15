import json
from pathlib import Path

from aws_cdk import BundlingOptions, CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

REPO_ROOT = Path(__file__).resolve().parents[2]

# SecureString parameters can't be created by CloudFormation; they are
# stored once with the AWS CLI (see infrastructure/README.md).
API_KEY_PARAM = "/software-factory/linear/api-key"
WEBHOOK_SECRET_PARAM = "/software-factory/linear/webhook-secret"
GITHUB_APP_KEY_PARAM = "/software-factory/github/app-private-key"


class LinearIntegration(Construct):
    """Linear webhook -> receiver Lambda -> worker Lambda -> planner -> comment."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runtime: agentcore.Runtime,
        builder: agentcore.Runtime,
        trigger_label: str,
        team_repos: dict[str, str],
        github_app_id: str,
        github_installation_id: str,
    ) -> None:
        super().__init__(scope, construct_id)
        stack = Stack.of(self)

        dedupe = dynamodb.Table(
            self,
            "DeliveryDedupe",
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="expiresAt",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # The bundle is integrations/ plus its pinned dependencies (PyJWT for
        # GitHub App auth), built for the Lambda runtime's architecture.
        code = lambda_.Code.from_asset(
            str(REPO_ROOT),
            exclude=["*", "!integrations", "!integrations/**", "**/__pycache__"],
            bundling=BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                platform="linux/arm64",
                command=[
                    "bash",
                    "-c",
                    "pip install --no-cache-dir -r integrations/requirements.txt -t /asset-output"
                    " && cp -r integrations /asset-output/",
                ],
            ),
        )

        def function(name: str, handler: str, timeout: Duration, env: dict) -> lambda_.Function:
            return lambda_.Function(
                self,
                name,
                runtime=lambda_.Runtime.PYTHON_3_13,
                architecture=lambda_.Architecture.ARM_64,
                handler=handler,
                code=code,
                memory_size=256,
                timeout=timeout,
                environment=env,
                log_group=logs.LogGroup(
                    self,
                    f"{name}Logs",
                    retention=logs.RetentionDays.ONE_WEEK,
                    removal_policy=RemovalPolicy.DESTROY,
                ),
            )

        # Builds take up to 12 minutes; Lambda's maximum is 15.
        self.build_worker = function(
            "BuildWorker",
            "integrations.linear.handlers.build_worker",
            Duration.minutes(15),
            {
                "LINEAR_API_KEY_PARAM": API_KEY_PARAM,
                "BUILDER_RUNTIME_ARN": builder.agent_runtime_arn,
                "GITHUB_APP_ID": github_app_id,
                "GITHUB_INSTALLATION_ID": github_installation_id,
                "GITHUB_APP_KEY_PARAM": GITHUB_APP_KEY_PARAM,
            },
        )
        # A retry would start a second billed build and open a duplicate PR.
        self.build_worker.configure_async_invoke(retry_attempts=0, max_event_age=Duration.hours(1))

        self.worker = function(
            "Worker",
            "integrations.linear.handlers.worker",
            # Repo exploration can take several minutes on large tickets.
            Duration.minutes(10),
            {
                "LINEAR_API_KEY_PARAM": API_KEY_PARAM,
                "PLANNER_RUNTIME_ARN": runtime.agent_runtime_arn,
                "TRIGGER_LABEL": trigger_label,
                "TEAM_REPOS": json.dumps(team_repos, sort_keys=True),
                "BUILD_FUNCTION": self.build_worker.function_name,
            },
        )
        # A retry would start a second billed planner run and a duplicate comment.
        self.worker.configure_async_invoke(retry_attempts=0, max_event_age=Duration.hours(1))

        self.receiver = function(
            "Receiver",
            "integrations.linear.handlers.receiver",
            # Linear gives up after 5 seconds.
            Duration.seconds(5),
            {
                "WEBHOOK_SECRET_PARAM": WEBHOOK_SECRET_PARAM,
                "DEDUPE_TABLE": dedupe.table_name,
                "WORKER_FUNCTION": self.worker.function_name,
            },
        )

        def read_parameter(fn: lambda_.Function, name: str) -> None:
            fn.add_to_role_policy(
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[f"arn:aws:ssm:{stack.region}:{stack.account}:parameter{name}"],
                )
            )

        read_parameter(self.receiver, WEBHOOK_SECRET_PARAM)
        dedupe.grant(self.receiver, "dynamodb:PutItem", "dynamodb:DeleteItem")
        self.worker.grant_invoke(self.receiver)

        read_parameter(self.worker, API_KEY_PARAM)
        runtime.grant_invoke_runtime(self.worker)
        self.build_worker.grant_invoke(self.worker)

        read_parameter(self.build_worker, API_KEY_PARAM)
        read_parameter(self.build_worker, GITHUB_APP_KEY_PARAM)
        builder.grant_invoke_runtime(self.build_worker)

        # Public URL for Linear; every request is checked against the signing secret.
        url = self.receiver.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        CfnOutput(stack, "LinearWebhookUrl", value=url.url)
