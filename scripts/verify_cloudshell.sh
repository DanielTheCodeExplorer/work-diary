#!/usr/bin/env bash
set -euo pipefail

STACK_NAME="${STACK_NAME:-work-diary-stack}"
API_BASE="${API_BASE:-}"
BUCKET="${BUCKET:-work-diary-static-1780521024}"
FUNCTION_NAME="${FUNCTION_NAME:-WorkDiaryAPI}"
OUT_DIR="${OUT_DIR:-verify-$(date +%Y%m%d-%H%M%S)}"
LOG_GROUP="${LOG_GROUP:-/aws/lambda/${FUNCTION_NAME}}"

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
  --query 'Environment.Variables' \
  --output json >"$OUT_DIR/lambda-env.json"

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
  printf '%s\n' "$LOGIN_RESPONSE" >"$OUT_DIR/login-body.json"

  TOKEN="$(
    python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("token", ""))' \
      "$OUT_DIR/login-body.json"
  )"
  if [[ -n "$TOKEN" ]]; then
    curl -s -D "$OUT_DIR/tasks-headers.txt" \
      -H "Authorization: Bearer ${TOKEN}" \
      "${API_BASE}/api/tasks" \
      -o "$OUT_DIR/tasks-body.json" || true
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
