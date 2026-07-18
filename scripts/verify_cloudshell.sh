#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${STACK_NAME:-work-diary-stack}"
API_BASE="${API_BASE:-}"
BUCKET="${BUCKET:-}"
FUNCTION_NAME="${FUNCTION_NAME:-WorkDiaryAPI}"
OUT_DIR="${OUT_DIR:-verify-$(date +%Y%m%d-%H%M%S)}"
LOG_GROUP="${LOG_GROUP:-/aws/lambda/${FUNCTION_NAME}}"

: "${BUCKET:?Set BUCKET to the frontend S3 bucket name.}"

mkdir -p "$OUT_DIR"

aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs" \
  --output json >"$OUT_DIR/cloudformation-outputs.json"

if [[ -z "$API_BASE" || "$API_BASE" == "None" ]]; then
  API_BASE="$(
    aws cloudformation describe-stacks \
      --stack-name "$STACK_NAME" \
      --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue | [0]" \
      --output text
  )"
fi

aws lambda get-function-configuration \
  --function-name "$FUNCTION_NAME" \
  --query '{Runtime:Runtime,LastModified:LastModified,Timeout:Timeout,MemorySize:MemorySize,EnvironmentKeys:keys(Environment.Variables)}' \
  --output json >"$OUT_DIR/lambda-config.json"

aws s3 ls "s3://${BUCKET}" --recursive >"$OUT_DIR/s3-list.txt"

aws s3api get-bucket-website \
  --bucket "$BUCKET" \
  >"$OUT_DIR/s3-website.json"

curl -s -D "$OUT_DIR/app-js-headers.txt" \
  "https://${BUCKET}.s3.eu-west-2.amazonaws.com/static/app.js" \
  -o "$OUT_DIR/app-js-body.txt" || true

if [[ -n "${APP_PASSWORD:-}" && -n "$API_BASE" ]]; then
  LOGIN_RESPONSE="$(
    curl -s -D "$OUT_DIR/login-headers.txt" \
      -H "Content-Type: application/json" \
      -X POST "${API_BASE}/api/login" \
      -d "{\"password\":\"${APP_PASSWORD}\"}"
  )"
  TOKEN="$(
    printf '%s' "$LOGIN_RESPONSE" | \
      python3 -c 'import json, sys; print(json.load(sys.stdin).get("token", ""))'
  )"
  if [[ -n "$TOKEN" ]]; then
    TASKS_RESPONSE="$(
      curl -s -D "$OUT_DIR/tasks-headers.txt" \
        -H "Authorization: Bearer ${TOKEN}" \
        "${API_BASE}/api/tasks" || true
    )"
    printf '%s' "$TASKS_RESPONSE" | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
    count = len(payload) if isinstance(payload, list) else 0
    print(json.dumps({"authenticated": True, "task_count": count}))
except (ValueError, TypeError):
    print(json.dumps({"authenticated": True, "task_count": None}))
' >"$OUT_DIR/tasks-summary.json"
  fi
fi

LATEST_STREAM="$(
  aws logs describe-log-streams \
    --log-group-name "$LOG_GROUP" \
    --order-by LastEventTime \
    --descending \
    --limit 1 \
    --query 'logStreams[0].logStreamName' \
    --output text
)"

if [[ "$LATEST_STREAM" != "None" && -n "$LATEST_STREAM" ]]; then
  aws logs get-log-events \
    --log-group-name "$LOG_GROUP" \
    --log-stream-name "$LATEST_STREAM" \
    --limit 200 \
    --output json >"$OUT_DIR/lambda-logs.json"
fi

zip -r "${OUT_DIR}.zip" "$OUT_DIR" >/dev/null
printf 'Wrote %s\n' "${OUT_DIR}.zip"
