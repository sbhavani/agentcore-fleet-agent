"""
Invoke the fleet-compliance harness with boto3 and stream the response.

Usage:
    export HARNESS_ARN="arn:aws:bedrock-agentcore:<region>:<account>:harness/..."
    python invoke_harness.py "Is driver-12 fit to drive?"
    python invoke_harness.py                      # uses a default prompt

Notes:
  - runtimeSessionId must be at least 33 characters. A UUID works.
  - Reuse the same session id across invocations to continue the conversation
    in the same environment (memory, files).
  - If you don't specify a model, the harness defaults to Claude Sonnet 4.6 on
    Amazon Bedrock.
"""

import os
import sys
import uuid

import boto3

REGION = os.environ.get("AWS_REGION", "us-west-2")
HARNESS_ARN = os.environ.get("HARNESS_ARN")
DEFAULT_PROMPT = "Is driver-47 fit to drive tomorrow?"

if not HARNESS_ARN:
    sys.exit("Set HARNESS_ARN first. Find it with: agentcore status")

prompt = " ".join(sys.argv[1:]) or DEFAULT_PROMPT

client = boto3.client("bedrock-agentcore", region_name=REGION)

response = client.invoke_harness(
    harnessArn=HARNESS_ARN,
    runtimeSessionId=str(uuid.uuid4()),
    messages=[{"role": "user", "content": [{"text": prompt}]}],
)

for event in response["stream"]:
    if "contentBlockDelta" in event:
        delta = event["contentBlockDelta"].get("delta", {})
        if "text" in delta:
            print(delta["text"], end="", flush=True)
    elif "runtimeClientError" in event:
        print(f"\nError: {event['runtimeClientError']['message']}")
