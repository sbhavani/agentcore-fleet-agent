#!/usr/bin/env bash
# Creates (or updates) the fleet-driver-tools Lambda and prints its ARN.
#
# Usage:
#   ./deploy_lambda.sh <lambda-execution-role-arn> [region]
#
# The role needs the AWSLambdaBasicExecutionRole managed policy so the
# function can write logs.
set -euo pipefail

ROLE_ARN="${1:?usage: ./deploy_lambda.sh <lambda-execution-role-arn> [region]}"
REGION="${2:-us-west-2}"
FUNCTION_NAME="fleet-driver-tools"

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/../lambdas/driver_tools"
ZIP="/tmp/${FUNCTION_NAME}.zip"

(cd "$SRC" && rm -f "$ZIP" && zip -q "$ZIP" lambda_function.py)

if aws lambda get-function --function-name "$FUNCTION_NAME" --region "$REGION" >/dev/null 2>&1; then
  echo "Updating existing function $FUNCTION_NAME..."
  aws lambda update-function-code --function-name "$FUNCTION_NAME" \
    --zip-file "fileb://$ZIP" --region "$REGION" \
    --query 'FunctionArn' --output text
else
  echo "Creating function $FUNCTION_NAME..."
  aws lambda create-function --function-name "$FUNCTION_NAME" \
    --runtime python3.12 --role "$ROLE_ARN" \
    --handler lambda_function.lambda_handler \
    --zip-file "fileb://$ZIP" --timeout 15 \
    --region "$REGION" \
    --query 'FunctionArn' --output text
fi

echo
echo "Next: pass that ARN to"
echo "  agentcore add gateway-target --name driver-tools --type lambda-function-arn \\"
echo "    --lambda-arn <ARN> --tool-schema-file gateway/tools.json --gateway FleetTools"
