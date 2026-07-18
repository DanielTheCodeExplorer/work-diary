#!/usr/bin/env bash
set -euo pipefail

: "${WORK_DIARY_STATIC_BUCKET:?Set WORK_DIARY_STATIC_BUCKET to the frontend S3 bucket name.}"
: "${WORK_DIARY_DISTRIBUTION_ID:?Set WORK_DIARY_DISTRIBUTION_ID to the CloudFront distribution ID.}"
: "${WORK_DIARY_API_BASE_URL:?Set WORK_DIARY_API_BASE_URL to the deployed API origin.}"

DEPLOY_DIR="$(mktemp -d /tmp/work-diary-frontend.XXXXXX)"

cleanup() {
  rm -rf -- "$DEPLOY_DIR"
}
trap cleanup EXIT

cp -R static/. "$DEPLOY_DIR/"
python3 - "$WORK_DIARY_API_BASE_URL" "$DEPLOY_DIR/config.js" <<'PY'
import json
import pathlib
import sys

api_base_url = sys.argv[1].rstrip("/")
output_path = pathlib.Path(sys.argv[2])
output_path.write_text(
    "window.WORK_DIARY_CONFIG = Object.freeze({\n"
    f"  apiBaseUrl: {json.dumps(api_base_url)},\n"
    "});\n",
    encoding="utf-8",
)
PY

aws s3 sync "$DEPLOY_DIR/" "s3://${WORK_DIARY_STATIC_BUCKET}/" \
  --delete \
  --exclude "static/*"
aws s3 sync "$DEPLOY_DIR/" "s3://${WORK_DIARY_STATIC_BUCKET}/static/" --delete
aws s3 cp "$DEPLOY_DIR/config.js" "s3://${WORK_DIARY_STATIC_BUCKET}/config.js" \
  --cache-control "no-store" \
  --content-type "application/javascript"
aws cloudfront create-invalidation \
  --distribution-id "$WORK_DIARY_DISTRIBUTION_ID" \
  --paths "/*"
