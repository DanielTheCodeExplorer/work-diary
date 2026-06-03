# Work Diary MVP

A local-first diary app for quickly logging work, attaching evidence links, and shaping entries into CV bullets later.

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
OPENAI_MODEL=gpt-5-nano
OPENAI_REASONING_EFFORT=low
```

Only `OPENAI_API_KEY` is required. `gpt-5-nano` is the default low-cost model
for CV bullet drafting. The app reads this file from the Python backend when
you click `Draft bullet`; the key is never placed in browser code.

## AWS Serverless Deployment

This repository now includes `lambda_backend.py`, a Lambda-compatible
backend implementation that supports the same API routes and diary features.
The static client can be hosted separately in Amplify, S3, or any static
hosting service, while the backend runs in Lambda and stores the diary data in
DynamoDB.

Required environment variables for Lambda:

- `APP_PASSWORD`
- `SESSION_SECRET`
- `OPENAI_API_KEY` (optional; if missing, the app falls back to local draft
  generation)
- `OPENAI_MODEL` (optional)
- `OPENAI_REASONING_EFFORT` (optional)
- `TASKS_TABLE`
- `ENTRIES_TABLE`
- `EVIDENCE_TABLE`

The frontend now uses bearer token authentication with `localStorage`.
If you deploy the static site and API from the same domain, the current
`/api/*` routes work without additional configuration.

### Quick AWS deployment notes

1. Create DynamoDB tables named `WorkDiaryTasks`, `WorkDiaryEntries`, and
   `WorkDiaryEvidence` with a string primary key named `id`.
2. Create an AWS Lambda function using Python 3.12 and set the handler to
   `lambda_backend.lambda_handler`.
3. Set the Lambda environment variables listed above.
4. Host the contents of the `static/` folder using AWS Amplify or S3 + CloudFront.
5. If the frontend and API are on the same domain, use relative `/api/*` calls.
   If not, set `window.API_BASE_URL` in `index.html` and `login.html`.

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
- Three main sections: To do, Diary, and Projects.
- Dark planner-style to-do list with Inbox, Today, and Upcoming boxes plus full list views for each box.
- Planner tasks can include due date, time, reminder, repeat rule, priority, project/list, location, and notes.
- The inline task bar stays fast; detailed task fields live in the always-available plus drawer.
- Detailed diary entries with date, title, what I did, project, skills, outcome, tags, difficulty, reflection notes, and CV bullet draft.
- Multiple evidence links per diary entry.
- Project cards show linked tasks, diary entries, and evidence.
- Evidence data model prepared for future Google Drive API, AWS S3 upload, and file attachment metadata.

## Evidence Types

- Google Drive link
- GitHub link
- Website link
- Certificate link
- Screenshot link
- AWS S3 file link
- Uploaded file placeholder
