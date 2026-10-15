import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stacks.planner_stack import PlannerStack, model_resource_arns


def synth(model_id="openai.gpt-oss-120b-1:0") -> Template:
    app = cdk.App()
    stack = PlannerStack(
        app,
        "TestPlanner",
        model_id=model_id,
        max_tokens="4096",
        max_ticket_chars="20000",
        budget_name="monthly-account-cap",
        budget_alert_email="alerts@example.com",
        env=cdk.Environment(account="111111111111", region="us-east-1"),
    )
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def template():
    return synth()


def test_runtime_avoids_always_on_costs(template):
    template.has_resource_properties(
        "AWS::BedrockAgentCore::Runtime",
        {
            "NetworkConfiguration": {"NetworkMode": "PUBLIC"},
            "LifecycleConfiguration": {
                "IdleRuntimeSessionTimeout": 300,
                "MaxLifetime": 3600,
            },
            "EnvironmentVariables": {
                "BEDROCK_MODEL_ID": "openai.gpt-oss-120b-1:0",
                "PLANNER_MAX_TOKENS": "4096",
                "PLANNER_MAX_TICKET_CHARS": "20000",
            },
        },
    )


def test_no_vpc_or_nat_gateway(template):
    template.resource_count_is("AWS::EC2::VPC", 0)
    template.resource_count_is("AWS::EC2::NatGateway", 0)


def test_runtime_can_only_invoke_configured_model(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "PolicyDocument": {
                "Statement": Match.array_with(
                    [
                        {
                            "Effect": "Allow",
                            "Action": [
                                "bedrock:InvokeModel",
                                "bedrock:InvokeModelWithResponseStream",
                            ],
                            "Resource": "arn:aws:bedrock:us-east-1::foundation-model/openai.gpt-oss-120b-1:0",
                        }
                    ]
                )
            }
        },
    )


def test_inference_profile_grants_profile_and_base_model():
    stack = cdk.Stack(env=cdk.Environment(account="111111111111", region="us-east-1"))
    assert model_resource_arns(stack, "us.anthropic.claude-sonnet-5-5") == [
        "arn:aws:bedrock:us-east-1:111111111111:inference-profile/us.anthropic.claude-sonnet-5-5",
        "arn:aws:bedrock:*::foundation-model/anthropic.claude-sonnet-5-5",
    ]


@pytest.mark.parametrize("prefix", ["us", "us-gov", "eu", "apac", "jp", "au", "ca", "global"])
def test_all_geography_prefixes_are_inference_profiles(prefix):
    stack = cdk.Stack(env=cdk.Environment(account="111111111111", region="us-east-1"))
    arns = model_resource_arns(stack, f"{prefix}.vendor.model-1")
    assert arns[1] == "arn:aws:bedrock:*::foundation-model/vendor.model-1"


def test_budgets_role_trust_is_scoped_to_this_account(template):
    template.has_resource_properties(
        "AWS::IAM::Role",
        {
            "AssumeRolePolicyDocument": {
                "Statement": [
                    {
                        "Action": "sts:AssumeRole",
                        "Effect": "Allow",
                        "Principal": {"Service": "budgets.amazonaws.com"},
                        "Condition": {
                            "StringEquals": {"aws:SourceAccount": "111111111111"}
                        },
                    }
                ]
            }
        },
    )


def test_budget_kill_switch_denies_bedrock_at_100_percent(template):
    template.has_resource_properties(
        "AWS::IAM::ManagedPolicy",
        {
            "PolicyDocument": {
                "Statement": [
                    {"Effect": "Deny", "Action": "bedrock:*", "Resource": "*"}
                ]
            }
        },
    )
    template.has_resource_properties(
        "AWS::Budgets::BudgetsAction",
        {
            "BudgetName": "monthly-account-cap",
            "ActionType": "APPLY_IAM_POLICY",
            "ApprovalModel": "AUTOMATIC",
            "NotificationType": "ACTUAL",
            "ActionThreshold": {"Type": "PERCENTAGE", "Value": 100},
            "Definition": {
                "IamActionDefinition": {
                    "Roles": [{"Ref": Match.string_like_regexp("PlannerRuntimeExecutionRole")}]
                }
            },
            "Subscribers": [{"Type": "EMAIL", "Address": "alerts@example.com"}],
        },
    )


def test_budgets_role_can_only_attach_kill_switch_to_runtime_role(template):
    template.has_resource_properties(
        "AWS::IAM::Policy",
        {
            "Roles": [{"Ref": Match.string_like_regexp("BudgetActionRole")}],
            "PolicyDocument": {
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Action": ["iam:AttachRolePolicy", "iam:DetachRolePolicy"],
                        "Resource": {
                            "Fn::GetAtt": [
                                Match.string_like_regexp("PlannerRuntimeExecutionRole"),
                                "Arn",
                            ]
                        },
                        "Condition": {
                            "ArnEquals": {
                                "iam:PolicyARN": {
                                    "Ref": Match.string_like_regexp("BudgetKillSwitchPolicy")
                                }
                            }
                        },
                    }
                ]
            },
        },
    )
