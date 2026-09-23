# AgentCore Hello Agent — learning Amazon Bedrock AgentCore

A learn-in-public project for [Amazon Bedrock AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html).
If you already know EC2, S3, Lambda, Fargate, and DynamoDB but haven't used
AgentCore, this repo is a hands-on first contact: one small agent, three tools,
one deploy command.

The sample tools use a small **fictional dataset** (driver compliance records)
purely as demo data. The point of the repo is the AWS plumbing — how a managed
agent loop reaches your existing Lambda functions — not the domain.

## The mental model in one line

**Lambda runs functions you wrote; an AgentCore Harness runs an agent whose
control flow the model decides at runtime. Gateway is how that agent reaches
your existing AWS services.**

## What you'll learn

| AgentCore concept | If you know… | Where it shows up here |
|---|---|---|
| Harness | Lambda, but config-only | `agentcore add harness` — model + prompt + tools, no orchestration code |
| Gateway | API Gateway for AI tools | Fronts one Lambda as three MCP tools |
| Lambda target | Plain Lambda | `lambdas/driver_tools/` — three tools in one function |
| Tool schema | API Gateway request models | `gateway/tools.json` |
| Session | — | Reusing a `--session-id` continues the same conversation |
| Observability | CloudWatch + X-Ray | Traces every model call and tool call automatically |

## Architecture

```text
you -> AgentCore Harness (the managed agent loop)
        |
        +-- AgentCore Gateway  (MCP tool surface, auth, audit)
        |      |
        |      +-- Lambda: fleet-driver-tools
        |             - get_driver_profile       (mock data lookup)
        |             - evaluate_driver_fitness  (deterministic rules engine)
        |             - notify_fleet_owner       (mock notification)
        |
        +-- Model: Claude Sonnet 4.6 on Bedrock (default)
```

One design rule baked into the demo: **the LLM never owns the decision.** A
deterministic Lambda rules engine evaluates the records; the agent only
orchestrates lookup → evaluation → notification. That separation is the
difference between a demo and something you'd put near production.

## Layout

| Path | What it is |
|---|---|
| `lambdas/driver_tools/lambda_function.py` | One Lambda hosting all three tools; dispatch by `bedrockAgentCoreToolName` |
| `gateway/tools.json` | MCP tool schemas (ToolDefinitions) registered on the Gateway target |
| `prompts/system_prompt.md` | System prompt for the harness — enforces "never decide fitness yourself" |
| `scripts/deploy_lambda.sh` | Zips and creates/updates the Lambda, prints its ARN |
| `scripts/invoke_harness.py` | boto3 `InvokeHarness` streaming example |

## Prerequisites

- AWS credentials configured, in a region where AgentCore harness is available.
- Node.js 20+ (for the AgentCore CLI) and Python 3.10+.
- Model access: Bedrock foundation models are enabled by default; the harness
  defaults to Claude Sonnet 4.6 if you don't pick a model.
- A Lambda execution role with `AWSLambdaBasicExecutionRole`, e.g.:

```bash
aws iam create-role --role-name fleet-lambda-role \
  --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name fleet-lambda-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
# then note the ARN:
aws iam get-role --role-name fleet-lambda-role --query 'Role.Arn' --output text
```

## Step 0 — Install the AgentCore CLI

```bash
npm install -g @aws/agentcore
```

## Step 1 — Deploy the Lambda tools

```bash
chmod +x scripts/deploy_lambda.sh
./scripts/deploy_lambda.sh arn:aws:iam::<account>:role/fleet-lambda-role us-west-2
# -> prints: arn:aws:lambda:us-west-2:<account>:function:fleet-driver-tools
```

## Step 2 — Scaffold the AgentCore project

```bash
agentcore create --project-name fleet-harness --no-agent
cd fleet-harness
```

`--no-agent` gives you an empty project. If you prefer wizards, run
`agentcore create` with no flags and pick **Harness** as the project type.

## Step 3 — Add the harness

Paste the system prompt from `prompts/system_prompt.md` into the command
(or reference it later in `app/fleet-compliance/harness.json`):

```bash
agentcore add harness \
  --name fleet-compliance \
  --model-provider bedrock \
  --system-prompt "$(cat ../prompts/system_prompt.md)"
```

## Step 4 — Add the Gateway and the Lambda target

```bash
# Gateway with no inbound auth — simplest for learning. Add AWS_IAM or
# CUSTOM_JWT (Cognito) later.
agentcore add gateway --name FleetTools --authorizer-type NONE

# Point the gateway at the Lambda from Step 1.
cp ../gateway/tools.json ./tools.json
agentcore add gateway-target --name driver-tools --type lambda-function-arn \
  --lambda-arn arn:aws:lambda:us-west-2:<account>:function:fleet-driver-tools \
  --tool-schema-file tools.json \
  --gateway FleetTools
```

Every tool on the gateway becomes available to the harness when you attach it.

## Step 5 — Attach the gateway to the harness

```bash
agentcore add tool --harness fleet-compliance --type agentcore_gateway \
  --name fleet-tools --gateway FleetTools
```

## Step 6 — Deploy

```bash
agentcore deploy        # synthesizes CDK; takes ~2–3 minutes
agentcore status        # shows harness ARN, gateway URL, resources
```

## Step 7 — Invoke

```bash
agentcore invoke --harness fleet-compliance \
  --session-id "$(uuidgen)" \
  "Is driver-12 fit to drive tomorrow?"
```

Session IDs must be at least 33 characters; reuse one to continue the same
conversation. Other useful flags: `--logs` (non-interactive, logs to stdout),
`--no-browser`, `--verbose` (raw streaming JSON events).

Try all four sample records — each exercises a different branch of the rules
engine, so the agent picks a different tool sequence for each:

| Prompt | Expected tool path | Expected status |
|---|---|---|
| `Is driver-47 fit to drive?` | evaluate_driver_fitness | `FIT`, no notification |
| `Is driver-12 fit to drive?` | evaluate → notify | `UNFIT_TO_DRIVE` (expired medical cert) |
| `What about driver-88?` | evaluate → notify | `UNFIT_TO_DRIVE` (active HOS violation) |
| `Check driver-31 and show me their record.` | get_driver_profile → evaluate → notify | `NEEDS_REVIEW` (missing records) |

To invoke programmatically instead:

```bash
pip install boto3
export HARNESS_ARN="arn:aws:bedrock-agentcore:us-west-2:<account>:harness/fleet-compliance-..."
python ../scripts/invoke_harness.py "Is driver-88 fit to drive?"
```

## What to look at while it runs

- **The tool loop**: watch the harness call `evaluate_driver_fitness`, then
  `notify_fleet_owner` — that's the model choosing the sequence at runtime,
  which is exactly what distinguishes an agent from a Lambda function.
- **Traces**: every model call, tool call, and result is captured in AgentCore
  Observability (OpenTelemetry-based, surfaced through CloudWatch).
- **Tool naming**: the gateway prefixes tools as `<target>___<tool>`, e.g.
  `driver-tools___get_driver_profile`. The Lambda strips the prefix by
  splitting on `___` — that's why one function can host three tools.

## Good next modifications (each is a small, instructive change)

1. **Real data**: replace the `DRIVERS` dict with a DynamoDB `get_item` —
   notice the agent code doesn't change at all, only the Lambda.
2. **Real notifications**: swap the mock in `notify_fleet_owner` for an SNS
   `publish` call.
3. **Guardrails**: add an AgentCore Policy rule so `notify_fleet_owner` can
   only be called when the evaluation status is `UNFIT_TO_DRIVE` or
   `NEEDS_REVIEW` — enforcement outside the prompt.
4. **Auth**: change the gateway authorizer from `NONE` to `CUSTOM_JWT` with a
   Cognito user pool.
5. **Human-in-the-loop**: add an `inline_function` tool (e.g.
   `approve_sidelining`) that pauses the harness and returns the call to your
   code — the documented pattern for approvals.

## Cleanup

```bash
agentcore remove all && agentcore deploy   # remove project resources
aws lambda delete-function --function-name fleet-driver-tools
```

## Troubleshooting

- **AccessDeniedException** — your IAM user/role needs `bedrock-agentcore:*`;
  the harness execution role and gateway permissions are wired by the CLI's
  CDK deployment.
- **Gateway not responding** — wait 30–60s after creation for DNS propagation;
  check `aws logs tail /aws/bedrock-agentcore/gateways/<gateway-id> --follow`.
- **Model access denied** — request access to the model in the Bedrock console.
- **Tool returns error JSON** — the Lambda must return valid JSON matching the
  tool's `outputSchema`; check the function's CloudWatch logs for the
  `tool=... event=...` line the handler prints.

## Key docs

- [Harness get started](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-get-started.html) — model default, CLI, session rules
- [Harness tools](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/harness-tools.html) — tool types (`agentcore_gateway`, `inline_function`, …)
- [Gateway quick start](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-quick-start.html) — `agentcore add gateway` / `add gateway-target`
- [Lambda targets](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-add-target-lambda.html) — tool schema and Lambda input/context format
- [What is AgentCore](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html) — all core services
