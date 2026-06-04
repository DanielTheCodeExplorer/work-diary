# AWS Deployment Guide

## Prerequisites

1. **AWS CLI installed** ✓ (just done)
2. **AWS Account** with access to Lambda, DynamoDB, API Gateway, IAM, S3, and CloudFront

## Step 1: Configure AWS Credentials

Get your AWS Access Key ID and Secret Access Key from AWS console:
- Go to https://console.aws.amazon.com → IAM → Users → Your User → Security credentials
- Create an access key or use an existing one

Then run:

```bash
aws configure
```

When prompted, enter:
- AWS Access Key ID
- AWS Secret Access Key
- Default region: `eu-west-2`
- Default output format: `json`

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
4. Confirm changes: **y**
5. Allow SAM to create IAM roles: **y**

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

Upload the static files:

```bash
aws s3 sync static/ s3://work-diary-static-1780521024/ --delete
aws s3 sync static/ s3://work-diary-static-1780521024/static --delete
```

Create a CloudFront distribution with:

- Origin: `work-diary-static-1780521024.s3.eu-west-2.amazonaws.com`
- Origin access: Origin Access Control
- Viewer protocol policy: `Redirect HTTP to HTTPS`
- Default root object: `index.html`
- Price class: cheapest available option

After uploads or frontend changes, invalidate CloudFront:

```bash
aws cloudfront create-invalidation --distribution-id EILSICXWG9CEK --paths "/*"
```

## Step 5: Confirm Frontend API URL

`static/index.html` and `static/login.html` must point to your API Gateway
endpoint:

```javascript
window.API_BASE_URL = "https://xjs2kilr31.execute-api.eu-west-2.amazonaws.com";
```

If you create a new API Gateway deployment later, replace that URL with the new
endpoint from Step 3.

## Step 6: Access Your PWA

Open the CloudFront HTTPS URL on your phone and test:
1. Log in with your password
2. Try Planner, Diary, the bottom image-evidence button, Calendar, and Settings
3. Click "Add to Home Screen" to install as an app

---

## Cost Estimate

- CloudFront static site: `$0.00` if under the free tier limits
- S3 static files: about `$0.00-$0.01/month`
- S3 image evidence: about `$0.01-$0.03/month` for up to roughly 1 GB stored
- API Gateway HTTP API: `$0.00` in free tier, about `$0.01` for 10k calls outside free tier
- Lambda: `$0.00` at one-person usage
- DynamoDB pay-per-request: usually `<$0.01/month`
- CloudWatch logs: `$0.00` if logs stay tiny
- OpenAI extraction: `$0.00` if disabled; roughly `$0.05-$0.30/month` for hundreds of diary entries if enabled

**Total: about `$0.00-$0.10/month` without OpenAI, or about `$0.10-$0.50/month` with OpenAI extraction enabled.**

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
