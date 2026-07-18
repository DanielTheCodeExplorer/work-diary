# Architecture

Work Diary has one browser client and two persistence runtimes:

- `static/` contains the installable browser application.
- `app.py` is the dependency-light local server backed by SQLite.
- `lambda_backend.py` is the AWS runtime backed by DynamoDB, S3, EventBridge
  Scheduler, Web Push, and optional Google/OpenAI integrations.
- `task_schedule.py` owns the canonical start/end date and time rules used by
  both backends.
- `integration_security.py` owns shared OAuth redirect and state-expiry rules.
- `Makefile` defines an allowlisted Lambda build so the cloud artifact contains
  only the three runtime modules and their pinned dependencies. Local data,
  browser assets, tests, and environment files are never packaged.

## Domain boundaries

Business rules should live in small shared modules that have no database or
network dependencies. Runtime modules translate validated values to and from
SQLite or AWS services. A behavior added to one backend must have a parity test
for the other backend before release.

The browser's quick **Date** is a convenience view for a one-day task: selecting
it sets both start and end dates. Advanced start/end controls remain independent.
Only `start_date`, `start_time`, `due_date`, and `due_time` are persisted.

## Current tenancy boundary

Each deployment is single-tenant. One password protects one shared dataset,
Google integration, and set of push subscriptions. Do not place unrelated users
in the same stack. A multi-user product must add managed identity, owner-scoped
partition keys, per-record authorization, and owner-scoped notifications before
customer onboarding.

The planned ChatGPT/MCP adapter must sit behind managed, owner-bound identity
and reuse the canonical task domain rules rather than calling the single-tenant
browser session directly. See [`MCP_INTEGRATION.md`](MCP_INTEGRATION.md).

## Release checks

Run these from a clean checkout:

```bash
python3 -m unittest discover -s tests -v
node --check static/app.js
node --check static/login.js
bash -n scripts/verify_cloudshell.sh
sam validate --lint
sam build
```

Generated `.aws-sam/` content, databases, verification bundles, logs, and local
environment files must never be committed or shared.
