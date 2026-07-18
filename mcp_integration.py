"""Dependency-free helpers for Work Diary's private MCP/OAuth surface."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional


MCP_PROTOCOL_VERSION = "2025-06-18"
ACCESS_TOKEN_SECONDS = 60 * 60
REFRESH_TOKEN_SECONDS = 30 * 24 * 60 * 60
AUTH_CODE_SECONDS = 10 * 60


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def sign_claims(claims: Dict[str, Any], secret: str) -> str:
    payload = _b64encode(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = _b64encode(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_claims(
    token: str,
    secret: str,
    *,
    token_type: str,
    audience: Optional[str] = None,
    now: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    try:
        payload, signature = token.split(".", 1)
        expected = _b64encode(
            hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return None
        claims = json.loads(_b64decode(payload).decode("utf-8"))
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    current = int(time.time() if now is None else now)
    if claims.get("typ") != token_type or int(claims.get("exp") or 0) <= current:
        return None
    if audience is not None and claims.get("aud") != audience:
        return None
    return claims


def pkce_s256(verifier: str) -> str:
    return _b64encode(hashlib.sha256(verifier.encode("ascii")).digest())


def oauth_server_metadata(origin: str) -> Dict[str, Any]:
    return {
        "issuer": origin,
        "authorization_endpoint": f"{origin}/oauth/authorize",
        "token_endpoint": f"{origin}/oauth/token",
        "registration_endpoint": f"{origin}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["work_diary.tasks"],
    }


def protected_resource_metadata(origin: str) -> Dict[str, Any]:
    resource = f"{origin}/mcp"
    return {
        "resource": resource,
        "authorization_servers": [origin],
        "scopes_supported": ["work_diary.tasks"],
        "bearer_methods_supported": ["header"],
    }


def tool_descriptors() -> list[Dict[str, Any]]:
    readonly = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
    write = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
    return [
        {
            "name": "search",
            "title": "Search Work Diary tasks",
            "description": "Use this when you need to find Work Diary tasks by words, project, or task state.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
                "additionalProperties": False,
            },
            "annotations": readonly,
        },
        {
            "name": "fetch",
            "title": "Fetch a Work Diary task",
            "description": "Use this when you need the current full planning details for one task ID.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
                "additionalProperties": False,
            },
            "annotations": readonly,
        },
        {
            "name": "list_tasks",
            "title": "List Work Diary tasks",
            "description": "Use this when planning a day or reviewing open, overdue, today, upcoming, completed, or archived tasks.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["open", "overdue", "today", "upcoming", "completed", "archived", "all"],
                        "default": "open",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                },
                "additionalProperties": False,
            },
            "annotations": readonly,
        },
        {
            "name": "create_task",
            "title": "Create a Work Diary task",
            "description": "Use this when the user has confirmed a new task, including any schedule, project, reminder, recurrence, location, or notes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 240},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD or empty."},
                    "start_time": {"type": "string", "description": "HH:MM or empty."},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD or empty."},
                    "due_time": {"type": "string", "description": "HH:MM or empty."},
                    "project": {"type": "string"},
                    "location": {"type": "string", "maxLength": 500},
                    "notes": {"type": "string"},
                    "reminder_at": {"type": "string", "description": "YYYY-MM-DDTHH:MM in Work Diary's Europe/London timezone, or empty."},
                    "repeat_rule": {"type": "string", "enum": ["none", "daily", "weekly", "monthly", "interval"], "default": "none"},
                    "repeat_interval_days": {"type": "integer", "minimum": 1, "maximum": 3650, "default": 1},
                    "repeat_until": {"type": "string", "description": "YYYY-MM-DD or empty."},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["title", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "update_task_details",
            "title": "Update Work Diary task details",
            "description": "Use this when the user has confirmed changes to a task's title, project, location, or notes. An empty project removes the task from its project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "title": {"type": "string", "minLength": 1, "maxLength": 240},
                    "project": {"type": "string"},
                    "location": {"type": "string", "maxLength": 500},
                    "notes": {"type": "string"},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["task_id", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "complete_task",
            "title": "Complete a Work Diary task",
            "description": "Use this when the user has confirmed that a specific current task is complete.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["task_id", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "reopen_task",
            "title": "Reopen a completed Work Diary task",
            "description": "Use this when the user has confirmed that a completed task should become open again. Its schedule and other details are preserved.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["task_id", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "archive_task",
            "title": "Archive a Work Diary task",
            "description": "Use this when the user has confirmed that an open or completed task should leave normal planning views. This is reversible in Work Diary and does not permanently delete the task.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["task_id", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "restore_task",
            "title": "Restore an archived Work Diary task",
            "description": "Use this when the user has confirmed that an archived task should return to normal planning views. This preserves whether the task was open or completed before it was archived.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["task_id", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "reschedule_task",
            "title": "Reschedule a Work Diary task",
            "description": "Use this when the user has confirmed new start or end schedule fields for one task.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "start_date": {"type": "string", "description": "YYYY-MM-DD or empty."},
                    "start_time": {"type": "string", "description": "HH:MM or empty."},
                    "due_date": {"type": "string", "description": "YYYY-MM-DD or empty."},
                    "due_time": {"type": "string", "description": "HH:MM or empty."},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["task_id", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "set_task_reminder",
            "title": "Set or clear a Work Diary task reminder",
            "description": "Use this when the user has confirmed a reminder time for one task, or confirmed that its reminder should be cleared.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "reminder_at": {"type": "string", "description": "YYYY-MM-DDTHH:MM in Work Diary's Europe/London timezone, or empty to clear."},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["task_id", "reminder_at", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "set_task_recurrence",
            "title": "Set or clear Work Diary task recurrence",
            "description": "Use this when the user has confirmed a task's repeat rule, interval, or stop date. Use repeat_rule none to stop future repeats.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "repeat_rule": {"type": "string", "enum": ["none", "daily", "weekly", "monthly", "interval"]},
                    "repeat_interval_days": {"type": "integer", "minimum": 1, "maximum": 3650, "default": 1},
                    "repeat_until": {"type": "string", "description": "YYYY-MM-DD or empty."},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["task_id", "repeat_rule", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "list_projects",
            "title": "List Work Diary projects",
            "description": "Use this when planning work across projects or when a task needs to be assigned to an existing project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["all", "planned", "active", "paused", "complete"], "default": "all"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
                },
                "additionalProperties": False,
            },
            "annotations": readonly,
        },
        {
            "name": "create_project",
            "title": "Create a Work Diary project",
            "description": "Use this when the user has confirmed a new project for organising tasks and calendar work.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "goal": {"type": "string"},
                    "deadline": {"type": "string", "description": "YYYY-MM-DD or empty."},
                    "status": {"type": "string", "enum": ["planned", "active", "paused"], "default": "planned"},
                    "color": {"type": "string", "description": "Six-digit hex colour such as #5DD4C0."},
                    "notes": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["name", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "update_project",
            "title": "Update Work Diary project details",
            "description": "Use this when the user has confirmed changes to a project's name, goal, deadline, colour, or notes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "name": {"type": "string", "minLength": 1},
                    "goal": {"type": "string"},
                    "deadline": {"type": "string", "description": "YYYY-MM-DD or empty."},
                    "color": {"type": "string", "description": "Six-digit hex colour such as #5DD4C0."},
                    "notes": {"type": "string"},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["project_id", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "set_project_status",
            "title": "Change a Work Diary project status",
            "description": "Use this when the user has confirmed that a project should be planned, active, paused, completed, or reopened. Completing a project records its completion in Work Diary.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "status": {"type": "string", "enum": ["planned", "active", "paused", "complete"]},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["project_id", "status", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
        {
            "name": "reorder_project_tasks",
            "title": "Reorder open tasks in a Work Diary project",
            "description": "Use this when the user has confirmed the complete order of every open, unarchived task in one project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "task_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "uniqueItems": True},
                    "expected_updated_at": {"type": "string"},
                    "idempotency_key": {"type": "string", "minLength": 8, "maxLength": 200},
                },
                "required": ["project_id", "task_ids", "expected_updated_at", "idempotency_key"],
                "additionalProperties": False,
            },
            "annotations": {**write, "idempotentHint": True},
        },
    ]


def jsonrpc_result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def jsonrpc_error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
