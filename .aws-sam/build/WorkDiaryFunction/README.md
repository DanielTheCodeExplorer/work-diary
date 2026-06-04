# Work Diary MVP

A local-first work tracker for planning tasks, journaling progress, attaching evidence links, and turning diary entries into career achievements.

## Run

```bash
python3 app.py
```

Open `http://127.0.0.1:8000`.

The app creates a SQLite database at `data/work_diary.sqlite3`.

## OpenAI API Key

Create a local `.env` file in the project root:

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-5.4-nano
OPENAI_REASONING_EFFORT=low
```

Only `OPENAI_API_KEY` is required. `gpt-5.4-nano` is the default low-cost model
for CV bullet drafting and achievement extraction. If the key is missing or an
OpenAI request fails, the backend uses deterministic local achievement
extraction; the key is never placed in browser code.

## AWS Serverless Deployment

This repository includes `lambda_backend.py`, a Lambda-compatible backend for
the deployed app. The static client is hosted from S3 through CloudFront, while
Lambda stores tasks, diary entries, evidence, and generated achievements in DynamoDB.

Required environment variables for Lambda:

- `APP_PASSWORD`
- `SESSION_SECRET`
- `OPENAI_API_KEY` (optional; if missing, the app falls back to local draft generation)
- `OPENAI_MODEL` (optional)
- `OPENAI_REASONING_EFFORT` (optional)
- `TASKS_TABLE`
- `ENTRIES_TABLE`
- `EVIDENCE_TABLE`
- `ACHIEVEMENTS_TABLE`

The deployed static pages set `window.API_BASE_URL` to the API Gateway endpoint
so `/api/*` calls go to Lambda from the CloudFront site.

### Current AWS URLs

- API Gateway: `https://xjs2kilr31.execute-api.eu-west-2.amazonaws.com`
- CloudFront: `https://d1ge7tepdgxhnw.cloudfront.net/`
- S3 bucket: `work-diary-static-1780521024`

After backend or template changes, deploy the existing SAM stack first:

```bash
sam build
sam deploy --guided
```

Use `work-diary-stack` as the stack name and `eu-west-2` as the region.

After frontend changes, upload and invalidate CloudFront:

```bash
aws s3 sync static/ s3://work-diary-static-1780521024/ --delete
aws s3 sync static/ s3://work-diary-static-1780521024/static --delete
aws cloudfront create-invalidation --distribution-id EILSICXWG9CEK --paths "/*"
```

## Password Login

Add these values to `.env` before exposing the app to other devices:

```bash
APP_PASSWORD=choose-a-long-private-password
SESSION_SECRET=generate-with-python-secrets-token-urlsafe
SESSION_MAX_AGE_SECONDS=2592000
```

Generate a session secret with:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

The app uses a signed, HTTP-only, secure session cookie. If either
`APP_PASSWORD` or `SESSION_SECRET` is missing, the login screen will tell you to
set them before the diary can be opened.

## Install On Your Devices

This app can be installed from the browser as a PWA after it is reachable over
HTTPS.

1. Install Tailscale on this Mac and on your phone or other devices.
2. Sign all devices into the same Tailscale account.
3. Start the diary on the Mac:

   ```bash
   python3 app.py
   ```

4. Expose the local app privately to your tailnet:

   ```bash
   tailscale serve --bg http://127.0.0.1:8000
   tailscale serve status
   ```

5. Open the HTTPS Tailscale URL shown by `tailscale serve status` on your phone.
6. Use the browser menu and choose `Add to Home Screen`.

Tailscale is not installed in this project folder. If `tailscale` is not found,
install the official Tailscale app first and enable its command-line tool.

## Start Automatically On This Mac

A launchd template is included at `deploy/uk.co.workdiary.app.plist.example`.

Install it with:

```bash
cp deploy/uk.co.workdiary.app.plist.example ~/Library/LaunchAgents/uk.co.workdiary.app.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/uk.co.workdiary.app.plist
launchctl kickstart -k gui/$(id -u)/uk.co.workdiary.app
```

Check logs with:

```bash
tail -f /tmp/work-diary.out.log /tmp/work-diary.err.log
```

Tailscale Serve with `--bg` is persistent in Tailscale itself. If you need to
turn it off:

```bash
tailscale serve reset
```

## Manual Database Backup

The diary data lives in `data/work_diary.sqlite3`.

Create a timestamped backup with:

```bash
mkdir -p backups
sqlite3 data/work_diary.sqlite3 ".backup backups/work_diary-$(date +%Y%m%d-%H%M%S).sqlite3"
```

## MVP Scope

- Quick log mode for rough notes.
- Four mobile-first sections: Dashboard, Planner, Diary, and Achievements.
- Dashboard summary widgets for open tasks, due today, diary entries this week, achievements this week, and recent progress.
- Dark planner-style task list with Inbox, Today, and Upcoming boxes plus full list views for each box.
- Planner tasks can include due date, time, reminder, repeat rule, priority, project/list, location, and notes.
- The bottom plus action is reserved for image evidence with optional notes.
- Diary entries support free-form journal notes plus detailed fields for date, title, what I did, project, skills, outcome, tags, difficulty, reflection notes, and CV bullet draft.
- Achievements stores newest-first career bullets generated from diary entries and completed-task logs.
- Multiple evidence links per diary entry.
- Evidence data model prepared for future Google Drive API, AWS S3 upload, and file attachment metadata.

## Evidence Types

- Google Drive link
- GitHub link
- Website link
- Certificate link
- Screenshot link
- Image evidence
- AWS S3 file link
- Uploaded file placeholder
