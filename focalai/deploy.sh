#!/usr/bin/env bash
# Idempotent deploy for FocalAI (Lambda + Function URL + IAM role).
# Requires: aws CLI configured, zip (or 7z), and env vars from .env.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# ---------- 1. Load .env if present ------------------------------------------
if [[ -f "$HERE/.env" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$HERE/.env"; set +a
elif [[ -f "$HERE/../.env" ]]; then
  set -a; source "$HERE/../.env"; set +a
fi

: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID missing}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY missing}"
export AWS_REGION="${AWS_REGION:-us-east-1}"
export AWS_DEFAULT_REGION="$AWS_REGION"

MODEL_ID="${BEDROCK_MODEL_ID:-amazon.nova-lite-v1:0}"
FN_NAME="${LAMBDA_FUNCTION_NAME:-focalai-task-prioritizer}"
ROLE_NAME="${FN_NAME}-role"
POLICY_NAME="${FN_NAME}-bedrock-invoke"
RUNTIME="python3.12"
HANDLER="lambda_function.lambda_handler"
TIMEOUT=25
MEMORY=512

echo "→ region: $AWS_REGION"
echo "→ function: $FN_NAME"
echo "→ model: $MODEL_ID"

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
MODEL_ARN="arn:aws:bedrock:${AWS_REGION}::foundation-model/${MODEL_ID}"

# ---------- 2. IAM role ------------------------------------------------------
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "→ creating IAM role $ROLE_NAME"
  cat > /tmp/focalai-trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
"Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
  aws iam create-role \
    --role-name "$ROLE_NAME" \
    --assume-role-policy-document file:///tmp/focalai-trust.json >/dev/null
  aws iam attach-role-policy \
    --role-name "$ROLE_NAME" \
    --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
  echo "→ waiting 10s for role propagation"; sleep 10
else
  echo "✓ role exists"
fi

cat > /tmp/focalai-inline.json <<JSON
{"Version":"2012-10-17","Statement":[{"Effect":"Allow",
"Action":"bedrock:InvokeModel","Resource":"${MODEL_ARN}"}]}
JSON
aws iam put-role-policy \
  --role-name "$ROLE_NAME" \
  --policy-name "$POLICY_NAME" \
  --policy-document file:///tmp/focalai-inline.json
echo "✓ bedrock invoke policy attached"

ROLE_ARN="$(aws iam get-role --role-name "$ROLE_NAME" --query Role.Arn --output text)"

# ---------- 3. Zip package ---------------------------------------------------
ZIP="$HERE/focalai.zip"
rm -f "$ZIP"
if command -v zip >/dev/null 2>&1; then
  ( cd "$HERE" && zip -q "$ZIP" lambda_function.py )
else
  # Windows fallback: use PowerShell Compress-Archive
  powershell.exe -NoProfile -Command \
    "Compress-Archive -Path '$HERE/lambda_function.py' -DestinationPath '$ZIP' -Force" \
    >/dev/null
fi
echo "✓ package built"

# ---------- 4. Lambda function ----------------------------------------------
ENV_JSON='Variables={BEDROCK_MODEL_ID='"$MODEL_ID"'}'

if aws lambda get-function --function-name "$FN_NAME" >/dev/null 2>&1; then
  echo "→ updating existing function"
  aws lambda update-function-code \
    --function-name "$FN_NAME" \
    --zip-file "fileb://$ZIP" >/dev/null
  aws lambda wait function-updated --function-name "$FN_NAME"
  aws lambda update-function-configuration \
    --function-name "$FN_NAME" \
    --timeout "$TIMEOUT" --memory-size "$MEMORY" \
    --environment "$ENV_JSON" \
    --runtime "$RUNTIME" --handler "$HANDLER" >/dev/null
  aws lambda wait function-updated --function-name "$FN_NAME"
else
  echo "→ creating function"
  aws lambda create-function \
    --function-name "$FN_NAME" \
    --runtime "$RUNTIME" --handler "$HANDLER" \
    --role "$ROLE_ARN" \
    --timeout "$TIMEOUT" --memory-size "$MEMORY" \
    --environment "$ENV_JSON" \
    --zip-file "fileb://$ZIP" >/dev/null
  aws lambda wait function-active --function-name "$FN_NAME"
fi
echo "✓ function deployed"

# ---------- 5. Function URL --------------------------------------------------
if aws lambda get-function-url-config --function-name "$FN_NAME" >/dev/null 2>&1; then
  URL="$(aws lambda get-function-url-config --function-name "$FN_NAME" --query FunctionUrl --output text)"
else
  URL="$(aws lambda create-function-url-config \
    --function-name "$FN_NAME" --auth-type NONE \
    --query FunctionUrl --output text)"
fi

# Public permission for Function URL (idempotent).
aws lambda add-permission \
  --function-name "$FN_NAME" \
  --statement-id FunctionURLAllowPublic \
  --action lambda:InvokeFunctionUrl \
  --principal '*' \
  --function-url-auth-type NONE >/dev/null 2>&1 || true

echo ""
echo "=================================================="
echo "  Function URL: $URL"
echo "=================================================="
