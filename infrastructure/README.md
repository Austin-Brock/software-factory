# infrastructure

CDK app (Python) that deploys the planner to Bedrock AgentCore Runtime.

## What it creates (`SoftwareFactoryPlanner` stack, us-east-1)

| Resource | Notes |
| --- | --- |
| Container image | Built from the repo `Dockerfile` (ARM64), pushed to the CDK bootstrap ECR repo |
| AgentCore Runtime `software_factory_planner` | Public network (no VPC/NAT), 5 min idle timeout, 1 h max lifetime |
| Runtime execution role | Invoke only the configured model; pull its image; write its logs |
| Budget kill switch | At 100% of budget `monthly-account-cap`, AWS Budgets attaches a `Deny bedrock:*` policy to the runtime role |

| Linear receiver Lambda | Public Function URL; verifies `Linear-Signature` + timestamp, dedupes deliveries, hands off to the worker (5 s timeout) |
| Linear worker Lambda | Confirms the `factory:plan` label, invokes the planner, posts the plan as a comment; async retries off |
| DynamoDB `DeliveryDedupe` | Remembers webhook delivery IDs for 7 days (TTL) so Linear retries never double-plan |
| Log groups | 7-day retention for both Lambdas |

Everything is tagged `project=software-factory`. The budget itself was created outside CDK and must exist before deploying.

## Configuration (`cdk.json` context)

| Key | Default |
| --- | --- |
| `bedrockModelId` | `openai.gpt-oss-120b-1:0` |
| `plannerMaxTokens` | `4096` |
| `plannerMaxTicketChars` | `20000` |
| `budgetName` | `monthly-account-cap` |
| `linearTriggerLabel` | `factory:plan` |

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

## Linear setup (once)

CloudFormation can't create SecureString parameters, so both secrets are stored by hand. Copy each value, then run:

```bash
# Personal API key: Linear → Settings → Account → Security & access → Personal API keys
aws ssm put-parameter --profile software-factory --region us-east-1 \
  --name /software-factory/linear/api-key --type SecureString --value "$(pbpaste)"

# After `cdk deploy`, create a webhook: Linear → Settings → API → Webhooks
#   URL: the LinearWebhookUrl stack output · Team: the sandbox team · Events: Issues
# then store its signing secret:
aws ssm put-parameter --profile software-factory --region us-east-1 \
  --name /software-factory/linear/webhook-secret --type SecureString --value "$(pbpaste)"
```

Until the webhook secret exists, the receiver answers every request with 503. To rotate a value, rerun the command with `--overwrite`. Running Lambdas cache it, so redeploy or wait for a cold start.

Then add the `factory:plan` label to an issue in that team. A plan comment appears in about 15–30 s.

## Releasing the kill switch

If the budget action fires, the planner and the Linear worker lose access to Bedrock and AgentCore, and labeled tickets get a "Planner is paused" comment. After reviewing the spend, detach the `BudgetKillSwitchPolicy` from the runtime role (IAM console, or reset the action in AWS Budgets).

**Don't `cdk deploy` while the kill switch is engaged.** The policy is attached outside CloudFormation, and a deploy that changes the runtime role's managed policies could detach it and silently resume spending. Release the kill switch deliberately first, then deploy.
