# Work Diary

A local-first work tracker for planning tasks, journaling progress, attaching evidence links, and turning diary entries into career achievements.

> **Deployment boundary:** the current authentication and data model are designed
> for one person (or one trusted household/team deployment). Do not onboard
> unrelated customers into the same stack: tasks, integrations, and push
> subscriptions are not tenant-isolated yet. A commercial multi-user release
> requires managed identities and owner-scoped records first.

## Run

```bash
python3 app.py
```

Open `http://127.0.0.1:8000`.

The app creates a SQLite database at `data/work_diary.sqlite3`.

Run the complete dependency-free test suite with:

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
node --check static/login.js
```

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
- `GOOGLE_INTEGRATION_TABLE`
- `GOOGLE_CLIENT_ID` (optional until Google sync is enabled)
- `GOOGLE_CLIENT_SECRET` (optional until Google sync is enabled)
- `GOOGLE_REDIRECT_URI` (optional until Google sync is enabled)
- `APP_FRONTEND_URL` (optional; used after Google OAuth)

The deployed static pages set `window.API_BASE_URL` to the API Gateway endpoint
so `/api/*` calls go to Lambda from the CloudFront site.

## Google Calendar and Tasks Sync

Work Diary can automatically sync Planner tasks to Google after you connect
Google in Settings:

- Every task syncs to the default Google Tasks list (`My Tasks`).
- Google displays dated tasks on its Tasks calendar; Work Diary does not create duplicate Calendar events.
- Work Diary stays the source of truth; local task saves are not blocked if
  Google is temporarily unavailable.

To enable it, create a Google OAuth web client with the Tasks API enabled, then set:

```bash
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=https://your-api.example.com/api/integrations/google/callback
APP_FRONTEND_URL=https://your-frontend.example.com/
```

For local testing, `GOOGLE_REDIRECT_URI` should point at the local backend
callback, for example `http://127.0.0.1:8000/api/integrations/google/callback`.

After backend or template changes, deploy the existing SAM stack first:

```bash
sam build
sam deploy --guided
```

Use `work-diary-stack` as the stack name and `eu-west-2` as the region.

After frontend changes, inject the environment-specific API origin, upload both
static paths, and invalidate CloudFront with the deployment script:

```bash
export WORK_DIARY_STATIC_BUCKET=your-static-bucket
export WORK_DIARY_DISTRIBUTION_ID=your-cloudfront-distribution-id
export WORK_DIARY_API_BASE_URL=https://your-api.example.com
scripts/deploy_frontend.sh
```

The committed `static/config.js` is intentionally environment-neutral. The
deployment script generates a temporary production copy, so account-specific
URLs never need to be committed.

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
Replace its two `/absolute/path/to/work-diary` placeholders with the checkout's
actual absolute path before installing it.

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

## Product Scope

- Quick log mode for rough notes.
- Four mobile-first sections: Dashboard, Planner, Diary, and Achievements.
- Dashboard summary widgets for open tasks, due today, diary entries this week, achievements this week, and recent progress.
- Dark planner-style task list with Inbox, Today, and Upcoming boxes plus full list views for each box.
- Planner tasks can include start/end dates and times, a reminder, repeat rule, project/list, location, and notes.
- Planner tasks sync to Google Tasks, where Google displays dated tasks on its Tasks calendar.
- The bottom plus action is reserved for image evidence with optional notes.
- Diary entries support free-form journal notes plus detailed fields for date, title, what I did, project, skills, outcome, tags, difficulty, reflection notes, and CV bullet draft.
- Achievements stores newest-first career bullets generated from diary entries and completed-task logs.
- Multiple evidence links per diary entry.
- Evidence data model prepared for future Google Drive API, AWS S3 upload, and file attachment metadata.

## Planned ChatGPT Integration

The planned private ChatGPT app will use a dedicated authenticated MCP service
to read, create, complete, and reschedule owner-scoped tasks. It is intentionally
not implemented on top of the current shared password/session boundary. See
[`MCP_INTEGRATION.md`](MCP_INTEGRATION.md) for the proposed tools, security
requirements, daily-planning flow, and release gates.

## Evidence Types

- Google Drive link
- GitHub link
- Website link
- Certificate link
- Screenshot link
- Image evidence
- AWS S3 file link
- Uploaded file placeholder
