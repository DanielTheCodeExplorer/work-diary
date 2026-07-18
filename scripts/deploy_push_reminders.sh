#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

DEFAULT_SUBJECT="mailto:your-email@example.com"

cleanup() {
  stty echo 2>/dev/null || true
  unset VAPID_PRIVATE_KEY
}
trap cleanup EXIT

printf "VAPID public key: "
read -r VAPID_PUBLIC_KEY

printf "VAPID private key: "
stty -echo
read -r VAPID_PRIVATE_KEY
stty echo
printf "\n"

printf "VAPID subject [%s]: " "$DEFAULT_SUBJECT"
read -r VAPID_SUBJECT
VAPID_SUBJECT="${VAPID_SUBJECT:-$DEFAULT_SUBJECT}"

sam build
sam deploy \
  --stack-name work-diary-stack \
  --parameter-overrides \
    VapidPublicKey="$VAPID_PUBLIC_KEY" \
    VapidPrivateKey="$VAPID_PRIVATE_KEY" \
    VapidSubject="$VAPID_SUBJECT"

aws lambda get-function-configuration \
  --function-name WorkDiaryAPI \
  --region eu-west-2 \
  --query '{hasPublic: Environment.Variables.VAPID_PUBLIC_KEY != null && Environment.Variables.VAPID_PUBLIC_KEY != ``, hasPrivate: Environment.Variables.VAPID_PRIVATE_KEY != null && Environment.Variables.VAPID_PRIVATE_KEY != ``, subject: Environment.Variables.VAPID_SUBJECT}' \
  --output json
