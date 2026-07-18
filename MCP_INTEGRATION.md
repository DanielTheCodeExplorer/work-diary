# Private ChatGPT and MCP Integration

This document defines the planned integration boundary. It is a roadmap, not a
deployed MCP service.

## Goal

Let the owner ask ChatGPT to read, create, complete, and reschedule Work Diary
tasks while keeping Work Diary authoritative. The first release should be a
private, data-only ChatGPT app; a custom widget can be added later if normal
tool calls and conversational responses are not enough.

## Proposed boundary

Expose a dedicated HTTPS `/mcp` service that calls the same task domain logic as
the browser API. Do not reimplement date normalization, validation, completion,
or recurrence rules in tool handlers. Both the existing backends and the MCP
adapter should use `task_schedule.py` and owner-scoped task services.

The MCP endpoint must not reuse `APP_PASSWORD` or a browser session cookie.
Before connecting real task data, add an OAuth-compatible identity flow (or an
equivalent managed, owner-bound identity), validate every request server-side,
and authorize every record against that owner. Secrets belong in the deployment
secret store or environment, never in source control or tool responses.

## Initial tools

| Tool | Purpose | Impact annotations |
| --- | --- | --- |
| `list_tasks` | Read open, today, upcoming, overdue, or completed tasks | read-only, private target, non-destructive |
| `create_task` | Create one owner-scoped task | write, private target, non-destructive |
| `complete_task` | Mark one task complete and return its resulting state | write, private target, non-destructive |
| `reschedule_task` | Change a task's independent start/end schedule | write, private target, non-destructive |

Each tool needs an explicit input schema and an exact `outputSchema`. Responses
should include only fields needed for planning; never return credentials,
internal traces, or unrelated personal data. Mutations should accept an
idempotency key and the task's expected revision so retries cannot duplicate a
task or silently overwrite a newer edit.

For write actions, ChatGPT should confirm the task and important schedule fields
in the current conversation. The MCP server remains responsible for identity,
authorization, validation, and conflict handling regardless of client hints.

## Daily planning flow

1. ChatGPT calls `list_tasks` for open, overdue, and today's work.
2. It proposes an ordered plan without changing data.
3. The owner confirms selected changes.
4. ChatGPT calls `create_task`, `complete_task`, or `reschedule_task` for the
   confirmed changes.
5. Each mutation returns the saved canonical task so ChatGPT can report the
   result accurately.

Start and end values must remain independent. A start-only task must not acquire
an end date, and an end-only task must not acquire a start date. All-day and
timed tasks must use the same normalization rules as the current UI and APIs.

## Delivery stages

1. Add managed identity, owner IDs, owner-scoped database keys, and per-record
   authorization to the product data model.
2. Extract backend task operations behind a tested service interface shared by
   HTTP and MCP adapters.
3. Implement the four tools with schema, authorization, idempotency, revision
   checks, audit logging, rate limits, and contract tests.
4. Deploy the MCP endpoint over HTTPS and test it with MCP Inspector and a
   private developer-mode ChatGPT app.
5. Add privacy documentation, retention controls, monitoring, and abuse tests
   before any customer or public release.
6. Add an optional ChatGPT widget only if task review or confirmation materially
   benefits from an embedded interface.

## Release gate

The integration is not ready for real or multi-user data until tests prove:

- one owner can never read or mutate another owner's tasks;
- retries are safe and stale revisions produce conflicts rather than data loss;
- start/end date and time combinations round-trip without field swapping;
- mutation tools require authenticated, owner-scoped authorization;
- logs and tool responses contain no secrets or unnecessary personal data; and
- existing local and AWS task behavior remains in parity.

## Official references

- [Build an app](https://learn.chatgpt.com/docs/build-app#app-building-model)
- [Build your MCP server](https://developers.openai.com/apps-sdk/build/mcp-server)
- [Apps SDK tool descriptor reference](https://developers.openai.com/apps-sdk/reference#tool-descriptor-parameters)
