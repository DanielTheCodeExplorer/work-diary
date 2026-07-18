# AWS Deployment Guide

## Prerequisites

1. AWS CLI and AWS SAM CLI
2. An AWS account with access to Lambda, DynamoDB, API Gateway, IAM, S3,
   EventBridge Scheduler, and CloudFront

## Step 1: Configure AWS Credentials

Use short-lived credentials or your organisation's SSO profile, then verify the
active identity before deploying:

```bash
aws sts get-caller-identity
```

## Step 2: Deploy with SAM

From the project root:

```bash
sam build
```

Then deploy (this will prompt you for settings):

```bash
sam deploy --guided
```

When prompted:
1. Stack name: `work-diary-stack`
2. Region: `eu-west-2`
3. Parameters:
   - AppPassword: use your private login password
   - SessionSecret: generate a private value with `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`
   - OpenAIApiKey: paste your OpenAI API key, or leave blank for the free deterministic fallback
   - VapidPublicKey: paste your Web Push VAPID public key, or leave blank until enabling phone reminders
   - VapidPrivateKey: paste your Web Push VAPID private key, or leave blank until enabling phone reminders
   - VapidSubject: use a contact value like `mailto:your-email@example.com`
   - GoogleClientId: paste your Google OAuth web client ID, or leave blank until enabling Google sync
   - GoogleClientSecret: paste your Google OAuth web client secret, or leave blank until enabling Google sync
   - GoogleRedirectUri: use your API endpoint plus `/api/integrations/google/callback`
   - AppFrontendUrl: use your CloudFront frontend URL
4. Confirm changes: **y**
5. Allow SAM to create IAM roles: **y**

For later deploys, keep using the same stack:

```bash
sam build
sam deploy --stack-name work-diary-stack
```

Phone reminders need VAPID keys. One simple way to generate them is:

```bash
npx web-push generate-vapid-keys
```

Google Calendar and Tasks sync needs a Google Cloud OAuth web client with both
Google Calendar API and Google Tasks API enabled. Add the same
`GoogleRedirectUri` value to the OAuth client's authorised redirect URIs.

## Step 3: Get Your API Endpoint

After deployment, SAM will output:

```
Outputs:
  ApiEndpoint  https://xxxxxxxx.execute-api.eu-west-2.amazonaws.com
```

Copy this URL.

## Step 4: Deploy the Frontend

For phone install support, use S3 + CloudFront. The S3 website endpoint is HTTP
only, so it is useful for quick testing but not enough for "Add to Home Screen".

Set the environment-specific deployment values and run the frontend deployment
script. It generates `config.js` in a temporary directory, uploads both S3
paths, and invalidates CloudFront:

```bash
export WORK_DIARY_STATIC_BUCKET=your-static-bucket
export WORK_DIARY_DISTRIBUTION_ID=your-cloudfront-distribution-id
export WORK_DIARY_API_BASE_URL=https://your-api.example.com
scripts/deploy_frontend.sh
```

Create a CloudFront distribution with:

- Origin: your private S3 bucket's regional endpoint
- Origin access: Origin Access Control
- Viewer protocol policy: `Redirect HTTP to HTTPS`
- Default root object: `index.html`
- Price class: cheapest available option

## Step 5: Confirm Frontend API URL

Confirm that the generated production config contains your API Gateway origin:

```javascript
window.WORK_DIARY_CONFIG = Object.freeze({
  apiBaseUrl: "https://your-api.example.com",
});
```

Do not edit or commit an environment-specific URL in `static/config.js`; pass it
to `scripts/deploy_frontend.sh` instead.

## Step 6: Access Your PWA

Open the CloudFront HTTPS URL on your phone and test:
1. Log in with your password
2. Try Planner, Diary, the bottom image-evidence button, Calendar, and Settings
3. In Settings, tap "Enable phone reminders" and allow notifications
4. Tap "Send test reminder"
5. Click "Add to Home Screen" to install as an app

---

Review current AWS and OpenAI pricing and set billing alarms before onboarding
users; usage and regional prices change over time.

---

## Troubleshooting

### Error: "Stack already exists"
Use the existing stack name `work-diary-stack`. Do not create a second stack for this app.

### Error: "Permission denied" when deploying
Make sure your AWS credentials are correct and your user has Lambda, DynamoDB, IAM, and API Gateway permissions.

### Lambda times out
Increase the timeout in `template.yaml` under `Globals.Function.Timeout`.

### DynamoDB not accessible
Check that Lambda role has the right DynamoDB permissions in the template.
