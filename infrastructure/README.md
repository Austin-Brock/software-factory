# infrastructure

CDK app (Python) that deploys the planner to Bedrock AgentCore Runtime.

## What it creates (`SoftwareFactoryPlanner` stack, us-east-1)

| Resource | Notes |
| --- | --- |
| Container image | Built from the repo `Dockerfile` (ARM64), pushed to the CDK bootstrap ECR repo |
| AgentCore Runtime `software_factory_planner` | Public network (no VPC/NAT), 5 min idle timeout, 1 h max lifetime |
| Runtime execution role | Invoke only the configured model; pull its image; write its logs |
| Budget kill switch | At 100% of budget `monthly-account-cap`, AWS Budgets attaches a `Deny bedrock:*` policy to the runtime role |

Everything is tagged `project=software-factory`. The budget itself was created outside CDK and must exist before deploying.

## Configuration (`cdk.json` context)

| Key | Default |
| --- | --- |
| `bedrockModelId` | `openai.gpt-oss-120b-1:0` |
| `plannerMaxTokens` | `4096` |
| `plannerMaxTicketChars` | `20000` |
| `budgetName` | `monthly-account-cap` |

The kill-switch alert email is personal, so it is not committed: set `BUDGET_ALERT_EMAIL` in the repo-root `.env` (or pass `-c budgetAlertEmail=...`).

## Commands

```bash
cd infrastructure
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest              # template assertions
export AWS_PROFILE=software-factory
npx aws-cdk@2.1145.0 synth              # render CloudFormation
npx aws-cdk@2.1145.0 bootstrap          # once per account/region
npx aws-cdk@2.1145.0 deploy             # create/update the stack
npx aws-cdk@2.1145.0 destroy            # remove everything
```

## Releasing the kill switch

If the budget action fires, the planner stops being able to call Bedrock and returns HTTP 503 "Bedrock refused the request". After reviewing the spend, detach the `BudgetKillSwitchPolicy` from the runtime role (IAM console, or reset the action in AWS Budgets).

**Don't `cdk deploy` while the kill switch is engaged.** The policy is attached outside CloudFormation, and a deploy that changes the runtime role's managed policies could detach it and silently resume spending. Release the kill switch deliberately first, then deploy.
