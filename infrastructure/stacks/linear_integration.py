from pathlib import Path

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from constructs import Construct

INTEGRATIONS_DIR = Path(__file__).resolve().parents[2] / "integrations"

# SecureString parameters can't be created by CloudFormation; they are
# stored once with the AWS CLI (see infrastructure/README.md).
API_KEY_PARAM = "/software-factory/linear/api-key"
WEBHOOK_SECRET_PARAM = "/software-factory/linear/webhook-secret"


class LinearIntegration(Construct):
    """Linear webhook -> receiver Lambda -> worker Lambda -> planner -> comment."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        runtime: agentcore.Runtime,
        trigger_label: str,
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

        code = lambda_.Code.from_asset(str(INTEGRATIONS_DIR), exclude=["**/__pycache__"])

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

        self.worker = function(
            "Worker",
            "linear.handlers.worker",
            Duration.minutes(5),
            {
                "LINEAR_API_KEY_PARAM": API_KEY_PARAM,
                "PLANNER_RUNTIME_ARN": runtime.agent_runtime_arn,
                "TRIGGER_LABEL": trigger_label,
            },
        )
        # A retry would start a second billed planner run and a duplicate comment.
        self.worker.configure_async_invoke(retry_attempts=0, max_event_age=Duration.hours(1))

        self.receiver = function(
            "Receiver",
            "linear.handlers.receiver",
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

        # Public URL for Linear; every request is checked against the signing secret.
        url = self.receiver.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)
        CfnOutput(stack, "LinearWebhookUrl", value=url.url)
