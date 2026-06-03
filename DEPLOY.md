# AWS Deployment Guide

## Prerequisites

1. **AWS CLI installed** ✓ (just done)
2. **AWS Account** with access to Lambda, DynamoDB, API Gateway, IAM

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
- Default region: `us-east-1` (or your preferred region)
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
1. Stack name: `work-diary` (or any name)
2. Region: pick your AWS region
3. Parameters:
   - AppPassword: `Boomwhat1000` (from your .env)
   - SessionSecret: `H7ceUiqKVYhkDgD79r2hVCPmxDZ14Jz49JArkmp4GEQ` (from your .env)
   - OpenAIApiKey: paste your OPENAI_API_KEY (or leave blank)
4. Confirm changes: **y**
5. Allow SAM to create IAM roles: **y**

## Step 3: Get Your API Endpoint

After deployment, SAM will output:

```
Outputs:
  ApiEndpoint  https://xxxxxxxx.execute-api.us-east-1.amazonaws.com
```

Copy this URL.

## Step 4: Deploy the Frontend

Use AWS Amplify or S3 to host the static files from `static/`:

### Option A: AWS Amplify (easiest)

```bash
npm install -g @aws-amplify/cli
amplify init
```

Or use the AWS Console to create a new Amplify app pointing to your GitHub repo.

### Option B: S3 + CloudFront (cheapest)

```bash
aws s3 mb s3://work-diary-static-$(date +%s)
aws s3 sync static/ s3://your-bucket-name --delete
```

Then enable static website hosting in the S3 bucket.

## Step 5: Update Frontend API URL

In `static/index.html` and `static/login.html`, find:

```javascript
window.API_BASE_URL = "";
```

Change to:

```javascript
window.API_BASE_URL = "https://xxxxxxxx.execute-api.us-east-1.amazonaws.com";
```

(Replace with your API endpoint from Step 3)

## Step 6: Access Your PWA

Open the frontend URL on your Xiaomi in Chrome and test:
1. Log in with your password
2. Try creating a task or entry
3. Click "Add to Home Screen" to install as an app

---

## Cost Estimate

- Lambda: ~$0/month (1M requests free, then $0.0000002 per request)
- DynamoDB: ~$0/month (on-demand, no read/write capacity charges; 25GB storage free)
- API Gateway: ~$0/month (1M requests free, then $0.50 per million)
- S3/Amplify: ~$0–$5/month depending on traffic

**Total: Likely under $1/month**

---

## Troubleshooting

### Error: "Stack already exists"
Add `--force` to skip existing stack:
```bash
sam deploy --guided --force
```

### Error: "Permission denied" when deploying
Make sure your AWS credentials are correct and your user has Lambda, DynamoDB, IAM, and API Gateway permissions.

### Lambda times out
Increase the timeout in `template.yaml` under `Globals.Function.Timeout`.

### DynamoDB not accessible
Check that Lambda role has the right DynamoDB permissions in the template.
