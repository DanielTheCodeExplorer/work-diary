# Private ChatGPT and MCP Integration

Work Diary exposes a private, data-only MCP server from the existing AWS Lambda
backend. Work Diary remains the source of truth and the MCP handlers call the
same task functions used by the browser API, preserving schedule validation,
recurrence, reminders, and Google sync.

## What is implemented

The public MCP URL is the deployed API Gateway origin plus `/mcp`, for example:

```text
https://abc123.execute-api.eu-west-2.amazonaws.com/mcp
```

The server implements stateless Streamable HTTP JSON-RPC for the request/response
operations ChatGPT needs. `GET /mcp` intentionally returns `405` because this
release does not provide an SSE stream.

| Tool | Purpose | Safety |
| --- | --- | --- |
| `search` | Standard read-only task search | Read-only |
| `fetch` | Standard read-only fetch by task ID | Read-only |
| `list_tasks` | List open, overdue, today, upcoming, completed, archived, or all tasks | Read-only |
| `create_task` | Create a task using existing validation and Google sync | Idempotent write |
| `complete_task` | Complete a task after checking its last-read revision | Idempotent write |
| `reschedule_task` | Change independent start/end fields after a revision check | Idempotent write |
| `archive_task` | Hide an already-completed task from planning views after a revision check | Reversible idempotent write |

Only planning fields are returned. Google access tokens, provider IDs, sync
hashes, internal traces, and unrelated diary data are excluded.

## Authentication

The MCP endpoint is an OAuth-protected resource. It provides protected-resource
metadata, authorization-server metadata, dynamic client registration,
authorization code flow with S256 PKCE and resource indicators, one-hour
audience-bound access tokens, thirty-day refresh tokens, and one-time
authorization-code records to block code replay.

Set two values that are separate from the normal browser login:

- `MCP_OWNER_PASSWORD`: entered only when approving ChatGPT access
- `MCP_SIGNING_SECRET`: a long random value used to sign OAuth clients and tokens

Generate the signing secret locally and never commit it:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'
```

The CloudFormation parameters are `McpOwnerPassword` and `McpSigningSecret`.
`sam deploy --guided` prompts for both.

## Deploy

```bash
sam build
sam deploy --guided
```

Use the existing `work-diary-stack` and `eu-west-2`. Preserve the existing
parameter values when prompted. After deployment, copy the `ApiEndpoint` stack
output and append `/mcp`.

Checks before deployment:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile lambda_backend.py mcp_integration.py
```

## Connect in ChatGPT

1. Open **Settings → Apps & Connectors → Advanced settings**.
2. Enable **Developer Mode**.
3. Create a new app and enter the HTTPS API Gateway URL ending in `/mcp`.
4. When redirected to Work Diary, enter `MCP_OWNER_PASSWORD` and approve access.
5. Refresh the app after future MCP tool-schema changes.

A useful first prompt is:

> List my overdue Work Diary tasks. Do not change anything yet. Group them into
> do today, reschedule, delegate, and drop, then ask me to confirm any changes.

## Daily planning flow

1. ChatGPT reads open, overdue, and today's tasks.
2. It proposes an ordered plan without changing data.
3. The owner confirms selected changes.
4. ChatGPT calls a mutation tool with a unique idempotency key and the task's
   last-read `updated_at` revision.
5. Work Diary returns the saved canonical task.

Start and end values remain independent. A start-only task does not acquire an
end date, and an end-only task does not acquire a start date.

Archiving is deliberately safer than deletion: ChatGPT can archive only tasks
that are already complete. Archived tasks can be restored, or reviewed and
permanently deleted from Work Diary's Archived view with an explicit
confirmation. The MCP integration does not expose permanent deletion.

## Current private-release boundary

This release is for Daniel's single-owner deployment. OAuth tokens are bound to
the configured owner and MCP resource, but the existing DynamoDB task records
predate tenant IDs. Do not expose this stack to unrelated users or treat it as a
multi-tenant product. A commercial release still requires managed identities,
owner IDs on every record, per-record authorization, refresh-token revocation,
rate limiting, audit logs, privacy controls, and abuse testing.

## Protocol references

- [OpenAI: Build an MCP server](https://developers.openai.com/apps-sdk/build/mcp-server)
- [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [MCP authorization](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
