#!/usr/bin/env python3
"""Local Work Diary server.

The app intentionally uses only Python's standard library so it can run
without a dependency install step.
"""

from __future__ import annotations

import datetime as dt
import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from integration_security import oauth_state_is_fresh, safe_return_url
from task_schedule import TaskSchedule, TaskScheduleValidationError


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "work_diary.sqlite3"
UPLOADS_DIR = DATA_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
ENV_PATH = BASE_DIR / ".env"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"
GOOGLE_TASKS_API_BASE = "https://tasks.googleapis.com/tasks/v1"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/tasks",
]
GOOGLE_PRIMARY_CALENDAR_ID = "primary"
GOOGLE_DEFAULT_TASKLIST_ID = "@default"
DEFAULT_OPENAI_MODEL = "gpt-5.4-nano"
MAX_IMAGE_UPLOAD_BYTES = 3 * 1024 * 1024
MAX_ACHIEVEMENT_BULLETS = 10
SESSION_COOKIE_NAME = "work_diary_session"
DEFAULT_SESSION_SECONDS = 60 * 60 * 24 * 30
PUBLIC_API_PATHS = {
    "/api/login",
    "/api/logout",
    "/api/integrations/google/callback",
}
PUBLIC_PAGE_PATHS = {
    "/login",
    "/login.html",
    "/manifest.webmanifest",
    "/service-worker.js",
    "/config.js",
    "/favicon.svg",
    "/apple-touch-icon.svg",
    "/apple-touch-icon.png",
}

mimetypes.add_type("application/manifest+json", ".webmanifest")

EVIDENCE_TYPES = {
    "google_drive": "Google Drive link",
    "github": "GitHub link",
    "website": "Website link",
    "certificate": "Certificate link",
    "screenshot": "Screenshot link",
    "image": "Image evidence",
    "aws_s3": "AWS S3 file link",
    "uploaded_file_placeholder": "Uploaded file placeholder",
}

DIFFICULTY_LEVELS = ["easy", "medium", "hard", "stretch"]
TASK_PRIORITIES = ["", "low", "medium", "high"]
TASK_REPEAT_RULES = ["none", "daily", "weekly", "monthly", "interval"]
PROJECT_STATUSES = ["planned", "active", "paused", "complete"]
DEFAULT_PROJECT_COLOR = "#5DD4C0"
MAX_PROJECT_SUGGESTIONS = 3
REMINDER_TIMEZONE = "Europe/London"
TASK_REMINDER_OFFSET_MINUTES = 10


class ValidationError(ValueError):
    """Raised when API input is syntactically valid but incomplete."""


class NotFoundError(KeyError):
    """Raised when a requested application resource does not exist."""


class OpenAIRequestError(RuntimeError):
    """Raised when the OpenAI API cannot return a usable draft."""


class GoogleIntegrationError(RuntimeError):
    """Raised when Google Calendar or Tasks cannot be synced."""


def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def today_iso() -> str:
    return dt.date.today().isoformat()


def get_connection(db_path: Any = DB_PATH) -> sqlite3.Connection:
    if db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS work_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_date TEXT NOT NULL,
            title TEXT NOT NULL,
            what_i_did TEXT NOT NULL,
            quick_note TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            project TEXT DEFAULT '',
            skills_used TEXT NOT NULL DEFAULT '[]',
            outcome TEXT DEFAULT '',
            reflection_notes TEXT DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            difficulty TEXT DEFAULT '',
            source_mode TEXT NOT NULL DEFAULT 'detailed',
            cv_bullet_draft TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (source_mode IN ('quick_log', 'detailed'))
        );

        CREATE TABLE IF NOT EXISTS evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_entry_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            evidence_url TEXT DEFAULT '',
            description TEXT DEFAULT '',
            provider TEXT DEFAULT '',
            provider_metadata TEXT NOT NULL DEFAULT '{}',
            storage_key TEXT DEFAULT '',
            attachment_status TEXT NOT NULL DEFAULT 'linked',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (work_entry_id)
                REFERENCES work_entries(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            project_id TEXT DEFAULT '',
            project TEXT DEFAULT '',
            start_date TEXT DEFAULT '',
            start_time TEXT DEFAULT '',
            due_date TEXT DEFAULT '',
            due_time TEXT DEFAULT '',
            reminder_at TEXT DEFAULT '',
            repeat_rule TEXT NOT NULL DEFAULT 'none',
            repeat_interval_days INTEGER NOT NULL DEFAULT 1,
            repeat_until TEXT DEFAULT '',
            priority TEXT DEFAULT '',
            location TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            project_order INTEGER NOT NULL DEFAULT 0,
            google_sync_target TEXT DEFAULT '',
            google_calendar_event_id TEXT DEFAULT '',
            google_calendar_event_link TEXT DEFAULT '',
            google_task_id TEXT DEFAULT '',
            google_task_link TEXT DEFAULT '',
            google_sync_hash TEXT DEFAULT '',
            google_synced_at TEXT DEFAULT '',
            google_sync_error TEXT DEFAULT '',
            completed INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0,
            archived_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (completed IN (0, 1)),
            CHECK (archived IN (0, 1))
        );

        CREATE TABLE IF NOT EXISTS achievements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_entry_id INTEGER NOT NULL,
            achieved_at TEXT NOT NULL,
            bullet TEXT NOT NULL,
            project TEXT DEFAULT '',
            skills_used TEXT NOT NULL DEFAULT '[]',
            tags TEXT NOT NULL DEFAULT '[]',
            source TEXT NOT NULL DEFAULT 'auto',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (source_entry_id)
                REFERENCES work_entries(id)
                ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            goal TEXT DEFAULT '',
            deadline TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            color TEXT NOT NULL DEFAULT '#5DD4C0',
            notes TEXT DEFAULT '',
            completed_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (status IN ('planned', 'active', 'paused', 'complete'))
        );

        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id TEXT PRIMARY KEY,
            endpoint TEXT NOT NULL UNIQUE,
            subscription_json TEXT NOT NULL,
            user_agent TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS google_integration (
            id TEXT PRIMARY KEY,
            access_token TEXT DEFAULT '',
            refresh_token TEXT DEFAULT '',
            token_expires_at INTEGER DEFAULT 0,
            scope TEXT DEFAULT '',
            calendar_id TEXT DEFAULT '',
            tasklist_id TEXT DEFAULT '',
            oauth_state TEXT DEFAULT '',
            oauth_state_created_at TEXT DEFAULT '',
            oauth_return_url TEXT DEFAULT '',
            connected_at TEXT DEFAULT '',
            last_sync_at TEXT DEFAULT '',
            last_error TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_work_entries_date
            ON work_entries(entry_date DESC);
        CREATE INDEX IF NOT EXISTS idx_work_entries_project
            ON work_entries(project);
        CREATE INDEX IF NOT EXISTS idx_evidence_work_entry
            ON evidence(work_entry_id);
        CREATE INDEX IF NOT EXISTS idx_evidence_type
            ON evidence(evidence_type);
        CREATE INDEX IF NOT EXISTS idx_tasks_completed
            ON tasks(completed);
        CREATE INDEX IF NOT EXISTS idx_tasks_due_date
            ON tasks(due_date);
        CREATE INDEX IF NOT EXISTS idx_achievements_date
            ON achievements(achieved_at DESC);
        CREATE INDEX IF NOT EXISTS idx_achievements_source_entry
            ON achievements(source_entry_id);
        CREATE INDEX IF NOT EXISTS idx_push_subscriptions_endpoint
            ON push_subscriptions(endpoint);
        CREATE INDEX IF NOT EXISTS idx_projects_status
            ON projects(status);
        CREATE INDEX IF NOT EXISTS idx_projects_deadline
            ON projects(deadline);
        """
    )
    ensure_task_columns(conn)
    ensure_entry_columns(conn)
    ensure_project_columns(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project_order ON tasks(project_id, project_order)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_start_date ON tasks(start_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_work_entries_project_id ON work_entries(project_id)")
    conn.commit()


def ensure_task_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    columns = {
        "project_id": "TEXT DEFAULT ''",
        "start_date": "TEXT DEFAULT ''",
        "start_time": "TEXT DEFAULT ''",
        "due_time": "TEXT DEFAULT ''",
        "reminder_at": "TEXT DEFAULT ''",
        "repeat_rule": "TEXT NOT NULL DEFAULT 'none'",
        "repeat_interval_days": "INTEGER NOT NULL DEFAULT 1",
        "repeat_until": "TEXT DEFAULT ''",
        "priority": "TEXT DEFAULT ''",
        "location": "TEXT DEFAULT ''",
        "notes": "TEXT DEFAULT ''",
        "project_order": "INTEGER NOT NULL DEFAULT 0",
        "google_sync_target": "TEXT DEFAULT ''",
        "google_calendar_event_id": "TEXT DEFAULT ''",
        "google_calendar_event_link": "TEXT DEFAULT ''",
        "google_task_id": "TEXT DEFAULT ''",
        "google_task_link": "TEXT DEFAULT ''",
        "google_sync_hash": "TEXT DEFAULT ''",
        "google_synced_at": "TEXT DEFAULT ''",
        "google_sync_error": "TEXT DEFAULT ''",
        "archived": "INTEGER NOT NULL DEFAULT 0",
        "archived_at": "TEXT DEFAULT ''",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")


def ensure_entry_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(work_entries)").fetchall()
    }
    columns = {
        "project_id": "TEXT DEFAULT ''",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE work_entries ADD COLUMN {name} {definition}")


def ensure_project_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(projects)").fetchall()
    }
    columns = {
        "completed_at": "TEXT DEFAULT ''",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {definition}")


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def read_env_file(path: Path = ENV_PATH) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def config_value(name: str, default: str = "") -> str:
    env_value = compact_text(os.environ.get(name))
    if env_value:
        return env_value
    return compact_text(read_env_file().get(name)) or default


def read_openai_config() -> Dict[str, str]:
    api_key = config_value("OPENAI_API_KEY")
    if api_key in {"put_your_api_key_here", "sk-your-key-here"}:
        api_key = ""
    return {
        "api_key": api_key,
        "model": config_value("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        "reasoning_effort": config_value("OPENAI_REASONING_EFFORT", "low"),
    }


def read_vapid_config() -> Dict[str, str]:
    return {
        "public_key": config_value("VAPID_PUBLIC_KEY"),
        "private_key": config_value("VAPID_PRIVATE_KEY"),
        "subject": config_value("VAPID_SUBJECT", "mailto:you@example.com"),
    }


def read_google_config() -> Dict[str, str]:
    app_base_url = config_value("APP_BASE_URL").rstrip("/")
    redirect_uri = config_value("GOOGLE_REDIRECT_URI")
    if not redirect_uri and app_base_url:
        redirect_uri = f"{app_base_url}/api/integrations/google/callback"
    return {
        "client_id": config_value("GOOGLE_CLIENT_ID"),
        "client_secret": config_value("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": redirect_uri,
        "frontend_url": config_value("APP_FRONTEND_URL") or app_base_url or "/",
    }


def google_client_is_configured(config: Optional[Dict[str, str]] = None) -> bool:
    config = config or read_google_config()
    return bool(config["client_id"] and config["client_secret"] and config["redirect_uri"])


def google_http_json(
    method: str,
    url: str,
    *,
    access_token: str = "",
    body: Optional[Dict[str, Any]] = None,
    form: Optional[Dict[str, str]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    headers: Dict[str, str] = {"Accept": "application/json"}
    data: Optional[bytes] = None
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        data = urlencode(form).encode("utf-8")
    elif body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=True).encode("utf-8")
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raw_error = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw_error)
            message = (
                payload.get("error_description")
                or payload.get("error", {}).get("message")
                or payload.get("error")
                or raw_error
            )
        except (AttributeError, json.JSONDecodeError):
            message = raw_error or str(exc)
        raise GoogleIntegrationError(f"Google API error ({exc.code}): {message}") from exc
    except URLError as exc:
        raise GoogleIntegrationError(f"Could not reach Google API: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GoogleIntegrationError("Google API returned invalid JSON.") from exc


def google_integration_defaults() -> Dict[str, Any]:
    return {
        "id": "default",
        "access_token": "",
        "refresh_token": "",
        "token_expires_at": 0,
        "scope": "",
        "calendar_id": "",
        "tasklist_id": "",
        "oauth_state": "",
        "oauth_state_created_at": "",
        "oauth_return_url": "",
        "connected_at": "",
        "last_sync_at": "",
        "last_error": "",
        "updated_at": now_iso(),
    }


def row_to_google_integration(row: sqlite3.Row) -> Dict[str, Any]:
    payload = google_integration_defaults()
    for key in payload:
        if key in row.keys():
            payload[key] = row[key]
    try:
        payload["token_expires_at"] = int(payload.get("token_expires_at") or 0)
    except (TypeError, ValueError):
        payload["token_expires_at"] = 0
    return payload


def get_google_integration(conn: sqlite3.Connection) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM google_integration WHERE id = ?",
        ("default",),
    ).fetchone()
    if row is None:
        payload = google_integration_defaults()
        conn.execute(
            """
            INSERT INTO google_integration (
                id, access_token, refresh_token, token_expires_at, scope, calendar_id,
                tasklist_id, oauth_state, oauth_state_created_at, oauth_return_url,
                connected_at, last_sync_at, last_error, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                payload["access_token"],
                payload["refresh_token"],
                payload["token_expires_at"],
                payload["scope"],
                payload["calendar_id"],
                payload["tasklist_id"],
                payload["oauth_state"],
                payload["oauth_state_created_at"],
                payload["oauth_return_url"],
                payload["connected_at"],
                payload["last_sync_at"],
                payload["last_error"],
                payload["updated_at"],
            ),
        )
        conn.commit()
        return payload
    return row_to_google_integration(row)


def save_google_integration(conn: sqlite3.Connection, updates: Dict[str, Any]) -> Dict[str, Any]:
    current = {**get_google_integration(conn), **updates}
    current["id"] = "default"
    current["updated_at"] = now_iso()
    conn.execute(
        """
        INSERT INTO google_integration (
            id, access_token, refresh_token, token_expires_at, scope, calendar_id,
            tasklist_id, oauth_state, oauth_state_created_at, oauth_return_url,
            connected_at, last_sync_at, last_error, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            access_token = excluded.access_token,
            refresh_token = excluded.refresh_token,
            token_expires_at = excluded.token_expires_at,
            scope = excluded.scope,
            calendar_id = excluded.calendar_id,
            tasklist_id = excluded.tasklist_id,
            oauth_state = excluded.oauth_state,
            oauth_state_created_at = excluded.oauth_state_created_at,
            oauth_return_url = excluded.oauth_return_url,
            connected_at = excluded.connected_at,
            last_sync_at = excluded.last_sync_at,
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (
            current["id"],
            compact_text(current.get("access_token")),
            compact_text(current.get("refresh_token")),
            int(current.get("token_expires_at") or 0),
            compact_text(current.get("scope")),
            compact_text(current.get("calendar_id")),
            compact_text(current.get("tasklist_id")),
            compact_text(current.get("oauth_state")),
            compact_text(current.get("oauth_state_created_at")),
            compact_text(current.get("oauth_return_url")),
            compact_text(current.get("connected_at")),
            compact_text(current.get("last_sync_at")),
            compact_text(current.get("last_error")),
            current["updated_at"],
        ),
    )
    conn.commit()
    return get_google_integration(conn)


def google_is_connected(integration: Dict[str, Any]) -> bool:
    return bool(integration.get("refresh_token") or integration.get("access_token"))


def google_failed_task_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM tasks WHERE google_sync_error <> ''"
    ).fetchone()
    return int(row["count"] or 0)


def google_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    config = read_google_config()
    integration = get_google_integration(conn)
    connected = google_is_connected(integration)
    granted_scopes = set(compact_text(integration.get("scope")).split())
    needs_reauthorization = connected and not set(GOOGLE_SCOPES).issubset(granted_scopes)
    ready = bool(
        connected
        and not needs_reauthorization
        and compact_text(integration.get("tasklist_id")) == GOOGLE_DEFAULT_TASKLIST_ID
    )
    return {
        "configured": google_client_is_configured(config),
        "connected": connected,
        "ready": ready,
        "needs_reauthorization": needs_reauthorization,
        "calendar_id": integration.get("calendar_id", ""),
        "tasklist_id": integration.get("tasklist_id", ""),
        "last_sync_at": integration.get("last_sync_at", ""),
        "last_error": integration.get("last_error", ""),
        "failed_task_count": google_failed_task_count(conn),
        "scopes": GOOGLE_SCOPES,
    }


def start_google_connect(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    config = read_google_config()
    if not google_client_is_configured(config):
        raise ValidationError(
            "Set GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI first."
        )
    state = secrets.token_urlsafe(32)
    return_url = safe_return_url(
        compact_text(data.get("return_url")), config["frontend_url"] or "/"
    )
    save_google_integration(
        conn,
        {
            "oauth_state": state,
            "oauth_state_created_at": now_iso(),
            "oauth_return_url": return_url,
            "last_error": "",
        },
    )
    params = {
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    }
    return {"auth_url": f"{GOOGLE_AUTH_URL}?{urlencode(params)}"}


def google_exchange_code(code: str, config: Dict[str, str]) -> Dict[str, Any]:
    return google_http_json(
        "POST",
        GOOGLE_TOKEN_URL,
        form={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": config["redirect_uri"],
        },
    )


def google_refresh_access_token(
    conn: sqlite3.Connection, integration: Dict[str, Any], config: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    config = config or read_google_config()
    refresh_token = compact_text(integration.get("refresh_token"))
    if not refresh_token:
        raise GoogleIntegrationError("Google is not connected.")
    payload = google_http_json(
        "POST",
        GOOGLE_TOKEN_URL,
        form={
            "client_id": config["client_id"],
            "client_secret": config["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    expires_in = int(payload.get("expires_in") or 3600)
    return save_google_integration(
        conn,
        {
            "access_token": payload.get("access_token", ""),
            "token_expires_at": int(time.time()) + max(60, expires_in) - 60,
            "scope": payload.get("scope", integration.get("scope", "")),
            "last_error": "",
        },
    )


def google_access_token(conn: sqlite3.Connection) -> tuple[str, Dict[str, Any]]:
    config = read_google_config()
    if not google_client_is_configured(config):
        raise GoogleIntegrationError("Google OAuth is not configured.")
    integration = get_google_integration(conn)
    if not google_is_connected(integration):
        raise GoogleIntegrationError("Google is not connected.")
    if integration.get("access_token") and int(integration.get("token_expires_at") or 0) > int(time.time()) + 30:
        return integration["access_token"], integration
    integration = google_refresh_access_token(conn, integration, config)
    return integration["access_token"], integration


def google_api(
    conn: sqlite3.Connection, method: str, url: str, body: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    token, _integration = google_access_token(conn)
    return google_http_json(method, url, access_token=token, body=body)


def ensure_google_calendar(conn: sqlite3.Connection, integration: Dict[str, Any]) -> str:
    calendar_id = compact_text(integration.get("calendar_id"))
    if calendar_id == GOOGLE_PRIMARY_CALENDAR_ID:
        return calendar_id
    save_google_integration(
        conn, {"calendar_id": GOOGLE_PRIMARY_CALENDAR_ID, "last_error": ""}
    )
    return GOOGLE_PRIMARY_CALENDAR_ID


def ensure_google_tasklist(conn: sqlite3.Connection, integration: Dict[str, Any]) -> str:
    tasklist_id = compact_text(integration.get("tasklist_id"))
    if tasklist_id == GOOGLE_DEFAULT_TASKLIST_ID:
        return tasklist_id
    save_google_integration(
        conn, {"tasklist_id": GOOGLE_DEFAULT_TASKLIST_ID, "last_error": ""}
    )
    return GOOGLE_DEFAULT_TASKLIST_ID


def ensure_google_destinations(conn: sqlite3.Connection) -> Dict[str, Any]:
    integration = get_google_integration(conn)
    ensure_google_tasklist(conn, integration)
    return get_google_integration(conn)


def google_callback_html(message: str, return_url: str = "/") -> Dict[str, Any]:
    safe_message = html_escape(message)
    safe_url = html_escape(return_url or "/")
    return {
        "_html": f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <meta http-equiv=\"refresh\" content=\"1; url={safe_url}\">
  <title>Google connected</title>
  <style>body{{font-family:system-ui,-apple-system,sans-serif;margin:2rem;line-height:1.5;background:#111;color:#f5f5f5}}a{{color:#5DD4C0}}</style>
</head>
<body>
  <h1>{safe_message}</h1>
  <p>Returning to Work Diary...</p>
  <p><a href=\"{safe_url}\">Open Work Diary</a></p>
</body>
</html>"""
    }


def complete_google_connect(conn: sqlite3.Connection, query: Dict[str, str]) -> Dict[str, Any]:
    integration = get_google_integration(conn)
    return_url = integration.get("oauth_return_url") or read_google_config()["frontend_url"] or "/"
    if query.get("error"):
        save_google_integration(conn, {"last_error": query["error"], "oauth_state": ""})
        return google_callback_html("Google connection cancelled.", return_url)
    state = compact_text(query.get("state"))
    expected_state = compact_text(integration.get("oauth_state"))
    state_is_valid = (
        state
        and expected_state
        and secrets.compare_digest(state, expected_state)
        and oauth_state_is_fresh(compact_text(integration.get("oauth_state_created_at")))
    )
    if not state_is_valid:
        save_google_integration(
            conn,
            {
                "last_error": "Google OAuth state was invalid or expired.",
                "oauth_state": "",
                "oauth_state_created_at": "",
            },
        )
        return google_callback_html("Google connection failed.", return_url)
    code = compact_text(query.get("code"))
    if not code:
        save_google_integration(conn, {"last_error": "Google OAuth code was missing."})
        return google_callback_html("Google connection failed.", return_url)
    config = read_google_config()
    token_payload = google_exchange_code(code, config)
    refresh_token = token_payload.get("refresh_token") or integration.get("refresh_token", "")
    if not refresh_token:
        save_google_integration(conn, {"last_error": "Google did not return a refresh token. Try connecting again."})
        return google_callback_html("Google connection failed.", return_url)
    expires_in = int(token_payload.get("expires_in") or 3600)
    save_google_integration(
        conn,
        {
            "access_token": token_payload.get("access_token", ""),
            "refresh_token": refresh_token,
            "token_expires_at": int(time.time()) + max(60, expires_in) - 60,
            "scope": token_payload.get("scope", " ".join(GOOGLE_SCOPES)),
            "oauth_state": "",
            "oauth_state_created_at": "",
            "connected_at": now_iso(),
            "last_error": "",
        },
    )
    ensure_google_destinations(conn)
    retry_google_sync(conn, include_all=True)
    return google_callback_html("Google connected.", return_url)


def html_escape(value: Any) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def google_task_sync_target(task: Dict[str, Any]) -> str:
    return "google_task"


def google_task_hash(task: Dict[str, Any]) -> str:
    target = google_task_sync_target(task)
    payload = {
        "target": target,
        "title": compact_text(task.get("title")),
        "project": compact_text(task.get("project")),
        "start_date": compact_text(task.get("start_date")),
        "start_time": compact_text(task.get("start_time")),
        "due_date": compact_text(task.get("due_date")),
        "due_time": compact_text(task.get("due_time")),
        "priority": compact_text(task.get("priority")),
        "location": compact_text(task.get("location")),
        "notes": compact_text(task.get("notes")),
        "completed": bool(task.get("completed")),
        "completed_at": compact_text(task.get("completed_at")),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def google_calendar_event_id_for_task(task: Dict[str, Any]) -> str:
    existing = compact_text(task.get("google_calendar_event_id"))
    if existing:
        return existing
    return "a" + hashlib.sha256(f"work-diary-task:{task.get('id')}".encode("utf-8")).hexdigest()[:31]


def google_task_notes(task: Dict[str, Any]) -> str:
    parts = []
    if compact_text(task.get("project")):
        parts.append(f"Project: {compact_text(task.get('project'))}")
    if compact_text(task.get("priority")):
        parts.append(f"Priority: {compact_text(task.get('priority')).title()}")
    if compact_text(task.get("start_time")):
        parts.append(f"Start time in Work Diary: {compact_text(task.get('start_time'))}")
    if compact_text(task.get("due_time")):
        parts.append(f"Due time in Work Diary: {compact_text(task.get('due_time'))}")
    if compact_text(task.get("notes")):
        parts.append(compact_text(task.get("notes")))
    parts.append(f"Work Diary task ID: {task.get('id')}")
    return "\n\n".join(parts)


def google_calendar_event_body(task: Dict[str, Any], event_id: str) -> Dict[str, Any]:
    schedule = validate_task_schedule(task)
    due_date = schedule.end_date
    start_date = schedule.start_date
    body: Dict[str, Any] = {
        "id": event_id,
        "summary": compact_text(task.get("title")) or "Work Diary task",
        "description": google_task_notes(task),
        "location": compact_text(task.get("location")),
        "extendedProperties": {
            "private": {
                "workDiaryTaskId": str(task.get("id")),
                "workDiarySync": "true",
            }
        },
    }
    timed_bounds = schedule.timed_bounds()
    if timed_bounds:
        start, end = timed_bounds
        body["start"] = {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": REMINDER_TIMEZONE}
        body["end"] = {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": REMINDER_TIMEZONE}
    else:
        start_day = start_date or due_date
        final_day = due_date or start_day
        end_day = (dt.date.fromisoformat(final_day) + dt.timedelta(days=1)).isoformat()
        body["start"] = {"date": start_day}
        body["end"] = {"date": end_day}
    if not body["location"]:
        body.pop("location")
    return body


def google_task_body(task: Dict[str, Any]) -> Dict[str, Any]:
    body = {
        "title": compact_text(task.get("title")) or "Work Diary task",
        "notes": google_task_notes(task),
        "status": "completed" if task.get("completed") else "needsAction",
    }
    if task.get("completed"):
        completed_at = compact_text(task.get("completed_at")) or now_iso()
        body["completed"] = completed_at
    task_date = compact_text(task.get("due_date") or task.get("start_date"))
    if task_date:
        body["due"] = f"{task_date}T00:00:00.000Z"
    return body


def clear_task_google_fields(conn: sqlite3.Connection, task_id: Any, error: str = "") -> None:
    conn.execute(
        """
        UPDATE tasks
        SET google_sync_target = '',
            google_calendar_event_id = '',
            google_calendar_event_link = '',
            google_task_id = '',
            google_task_link = '',
            google_sync_hash = '',
            google_synced_at = '',
            google_sync_error = ?
        WHERE id = ?
        """,
        (compact_text(error), task_id),
    )
    conn.commit()


def save_task_google_sync_state(
    conn: sqlite3.Connection,
    task_id: Any,
    *,
    target: str,
    sync_hash: str,
    calendar_event_id: Optional[str] = None,
    calendar_event_link: Optional[str] = None,
    google_task_id: Optional[str] = None,
    google_task_link: Optional[str] = None,
    error: str = "",
) -> None:
    timestamp = now_iso()
    current_task = get_task(conn, int(task_id))
    calendar_event_id = current_task.get("google_calendar_event_id", "") if calendar_event_id is None else calendar_event_id
    calendar_event_link = current_task.get("google_calendar_event_link", "") if calendar_event_link is None else calendar_event_link
    google_task_id = current_task.get("google_task_id", "") if google_task_id is None else google_task_id
    google_task_link = current_task.get("google_task_link", "") if google_task_link is None else google_task_link
    conn.execute(
        """
        UPDATE tasks
        SET google_sync_target = ?,
            google_calendar_event_id = ?,
            google_calendar_event_link = ?,
            google_task_id = ?,
            google_task_link = ?,
            google_sync_hash = ?,
            google_synced_at = ?,
            google_sync_error = ?
        WHERE id = ?
        """,
        (
            compact_text(target),
            compact_text(calendar_event_id),
            compact_text(calendar_event_link),
            compact_text(google_task_id),
            compact_text(google_task_link),
            compact_text(sync_hash),
            timestamp if not error else compact_text(get_task(conn, int(task_id)).get("google_synced_at")),
            compact_text(error),
            task_id,
        ),
    )
    conn.commit()
    if error:
        save_google_integration(conn, {"last_error": error})
    else:
        save_google_integration(conn, {"last_sync_at": timestamp, "last_error": ""})


def save_task_google_error(conn: sqlite3.Connection, task_id: Any, error: Exception) -> None:
    message = compact_text(str(error))[:500]
    task = get_task(conn, int(task_id))
    save_task_google_sync_state(
        conn,
        task_id,
        target=compact_text(task.get("google_sync_target")) or google_task_sync_target(task),
        sync_hash=compact_text(task.get("google_sync_hash")),
        calendar_event_id=compact_text(task.get("google_calendar_event_id")),
        calendar_event_link=compact_text(task.get("google_calendar_event_link")),
        google_task_id=compact_text(task.get("google_task_id")),
        google_task_link=compact_text(task.get("google_task_link")),
        error=message,
    )


def delete_google_calendar_event(conn: sqlite3.Connection, task: Dict[str, Any]) -> None:
    event_id = compact_text(task.get("google_calendar_event_id"))
    if not event_id:
        return
    calendar_id = ensure_google_calendar(conn, get_google_integration(conn))
    try:
        google_api(
            conn,
            "DELETE",
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}",
        )
    except GoogleIntegrationError as exc:
        if "(404)" not in str(exc) and "(410)" not in str(exc):
            raise


def delete_google_task(conn: sqlite3.Connection, task: Dict[str, Any]) -> None:
    task_id = compact_text(task.get("google_task_id"))
    if not task_id:
        return
    tasklist_id = ensure_google_tasklist(conn, get_google_integration(conn))
    try:
        google_api(
            conn,
            "DELETE",
            f"{GOOGLE_TASKS_API_BASE}/lists/{quote(tasklist_id, safe='')}/tasks/{quote(task_id, safe='')}",
        )
    except GoogleIntegrationError as exc:
        if "(404)" not in str(exc) and "(410)" not in str(exc):
            raise


def cleanup_previous_google_target(
    conn: sqlite3.Connection, previous: Dict[str, Any], current_target: str
) -> None:
    previous_target = compact_text(previous.get("google_sync_target")) or google_task_sync_target(previous)
    if previous_target == current_target:
        return
    if previous_target == "calendar_event":
        delete_google_calendar_event(conn, previous)
    if previous_target == "google_task":
        delete_google_task(conn, previous)


def sync_calendar_event_for_task(conn: sqlite3.Connection, task: Dict[str, Any], sync_hash: str) -> None:
    if task.get("completed"):
        delete_google_calendar_event(conn, task)
        save_task_google_sync_state(
            conn,
            task["id"],
            target="calendar_and_task",
            sync_hash=sync_hash,
            calendar_event_id="",
            calendar_event_link="",
        )
        return
    calendar_id = ensure_google_calendar(conn, get_google_integration(conn))
    event_id = google_calendar_event_id_for_task(task)
    body = google_calendar_event_body(task, event_id)
    event_url = f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
    if compact_text(task.get("google_calendar_event_id")):
        try:
            payload = google_api(conn, "PUT", event_url, body)
        except GoogleIntegrationError as exc:
            if "(404)" not in str(exc) and "(410)" not in str(exc):
                raise
            payload = google_api(
                conn,
                "POST",
                f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events",
                body,
            )
    else:
        payload = google_api(
            conn,
            "POST",
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events",
            body,
        )
    save_task_google_sync_state(
        conn,
        task["id"],
        target="calendar_and_task",
        sync_hash=sync_hash,
        calendar_event_id=event_id,
        calendar_event_link=compact_text(payload.get("htmlLink")),
    )


def sync_google_task_for_task(
    conn: sqlite3.Connection,
    task: Dict[str, Any],
    sync_hash: str,
    *,
    target: str = "google_task",
) -> None:
    tasklist_id = ensure_google_tasklist(conn, get_google_integration(conn))
    body = google_task_body(task)
    google_task_id = compact_text(task.get("google_task_id"))
    if task.get("completed") and not google_task_id:
        save_task_google_sync_state(conn, task["id"], target=target, sync_hash=sync_hash)
        return
    if google_task_id:
        try:
            payload = google_api(
                conn,
                "PATCH",
                f"{GOOGLE_TASKS_API_BASE}/lists/{quote(tasklist_id, safe='')}/tasks/{quote(google_task_id, safe='')}",
                body,
            )
        except GoogleIntegrationError as exc:
            if "(404)" not in str(exc) and "(410)" not in str(exc):
                raise
            payload = google_api(
                conn,
                "POST",
                f"{GOOGLE_TASKS_API_BASE}/lists/{quote(tasklist_id, safe='')}/tasks",
                body,
            )
    else:
        payload = google_api(
            conn,
            "POST",
            f"{GOOGLE_TASKS_API_BASE}/lists/{quote(tasklist_id, safe='')}/tasks",
            body,
        )
    save_task_google_sync_state(
        conn,
        task["id"],
        target=target,
        sync_hash=sync_hash,
        google_task_id=compact_text(payload.get("id")),
        google_task_link=compact_text(payload.get("webViewLink")),
    )


def sync_task_to_google(
    conn: sqlite3.Connection, task: Dict[str, Any], previous: Optional[Dict[str, Any]] = None
) -> None:
    if not google_is_connected(get_google_integration(conn)):
        return
    target = google_task_sync_target(task)
    sync_hash = google_task_hash(task)
    if (
        not compact_text(task.get("google_sync_error"))
        and compact_text(task.get("google_sync_hash")) == sync_hash
        and compact_text(task.get("google_sync_target")) == target
    ):
        return
    if compact_text(task.get("google_calendar_event_id")):
        delete_google_calendar_event(conn, task)
        save_task_google_sync_state(
            conn,
            task["id"],
            target="google_task",
            sync_hash=sync_hash,
            calendar_event_id="",
            calendar_event_link="",
        )
        task = get_task(conn, int(task["id"]))
    sync_google_task_for_task(conn, task, sync_hash, target="google_task")


def auto_sync_task_after_save(
    conn: sqlite3.Connection, task: Dict[str, Any], previous: Optional[Dict[str, Any]] = None
) -> None:
    try:
        sync_task_to_google(conn, task, previous)
        retry_google_sync(conn, include_all=False, exclude_task_id=task.get("id"))
    except Exception as exc:
        save_task_google_error(conn, task["id"], exc)


def auto_delete_google_for_task(conn: sqlite3.Connection, task: Dict[str, Any]) -> None:
    if not google_is_connected(get_google_integration(conn)):
        return
    try:
        target = compact_text(task.get("google_sync_target")) or google_task_sync_target(task)
        if target in {"calendar_event", "calendar_and_task"}:
            delete_google_calendar_event(conn, task)
        if target in {"google_task", "calendar_and_task"}:
            delete_google_task(conn, task)
        save_google_integration(conn, {"last_sync_at": now_iso(), "last_error": ""})
    except Exception as exc:
        save_google_integration(conn, {"last_error": compact_text(str(exc))[:500]})


def retry_google_sync(
    conn: sqlite3.Connection, *, include_all: bool = False, exclude_task_id: Any = None
) -> Dict[str, Any]:
    if not google_is_connected(get_google_integration(conn)):
        return {"ok": False, "synced": 0, "failed": 0, "skipped": 0}
    if include_all:
        rows = conn.execute("SELECT * FROM tasks").fetchall()
    else:
        rows = conn.execute("SELECT * FROM tasks WHERE google_sync_error <> ''").fetchall()
    synced = 0
    failed = 0
    skipped = 0
    for row in rows:
        task = row_to_task(row)
        if exclude_task_id is not None and str(task["id"]) == str(exclude_task_id):
            skipped += 1
            continue
        try:
            before_hash = compact_text(task.get("google_sync_hash"))
            sync_task_to_google(conn, task)
            after = get_task(conn, int(task["id"]))
            if compact_text(after.get("google_sync_hash")) != before_hash or not compact_text(after.get("google_sync_error")):
                synced += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            save_task_google_error(conn, task["id"], exc)
    return {"ok": failed == 0, "synced": synced, "failed": failed, "skipped": skipped}


def disconnect_google(conn: sqlite3.Connection) -> Dict[str, Any]:
    save_google_integration(
        conn,
        {
            "access_token": "",
            "refresh_token": "",
            "token_expires_at": 0,
            "scope": "",
            "oauth_state": "",
            "oauth_state_created_at": "",
            "oauth_return_url": "",
            "connected_at": "",
            "last_sync_at": "",
            "last_error": "",
        },
    )
    conn.execute(
        """
        UPDATE tasks
        SET google_sync_target = '',
            google_calendar_event_id = '',
            google_calendar_event_link = '',
            google_task_id = '',
            google_task_link = '',
            google_sync_hash = '',
            google_synced_at = '',
            google_sync_error = ''
        """
    )
    conn.commit()
    return google_status(conn)


def push_subscription_id(endpoint: Any) -> str:
    text = compact_text(endpoint)
    if not text:
        raise ValidationError("Push subscription endpoint is required.")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_push_subscription(data: Dict[str, Any]) -> Dict[str, Any]:
    subscription = data.get("subscription") if isinstance(data.get("subscription"), dict) else data
    if not isinstance(subscription, dict):
        raise ValidationError("Push subscription must be an object.")
    endpoint = compact_text(subscription.get("endpoint"))
    keys = subscription.get("keys")
    if not endpoint or not isinstance(keys, dict) or not keys.get("p256dh") or not keys.get("auth"):
        raise ValidationError("Push subscription is incomplete.")
    return {
        "endpoint": endpoint,
        "expirationTime": subscription.get("expirationTime"),
        "keys": {
            "p256dh": compact_text(keys.get("p256dh")),
            "auth": compact_text(keys.get("auth")),
        },
    }


def row_to_push_subscription(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        subscription = json.loads(row["subscription_json"])
    except json.JSONDecodeError:
        subscription = {"endpoint": row["endpoint"], "keys": {}}
    return {
        "id": row["id"],
        "endpoint": row["endpoint"],
        "subscription": subscription,
        "user_agent": row["user_agent"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_push_subscriptions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM push_subscriptions ORDER BY updated_at DESC"
    ).fetchall()
    return [row_to_push_subscription(row) for row in rows]


def save_push_subscription(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    subscription = normalize_push_subscription(data)
    timestamp = now_iso()
    subscription_id = push_subscription_id(subscription["endpoint"])
    existing = conn.execute(
        "SELECT created_at FROM push_subscriptions WHERE id = ?",
        (subscription_id,),
    ).fetchone()
    created_at = existing["created_at"] if existing else timestamp
    conn.execute(
        """
        INSERT INTO push_subscriptions (
            id, endpoint, subscription_json, user_agent, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            endpoint = excluded.endpoint,
            subscription_json = excluded.subscription_json,
            user_agent = excluded.user_agent,
            updated_at = excluded.updated_at
        """,
        (
            subscription_id,
            subscription["endpoint"],
            json.dumps(subscription, separators=(",", ":")),
            compact_text(data.get("user_agent")),
            created_at,
            timestamp,
        ),
    )
    conn.commit()
    return {"ok": True, "id": subscription_id}


def delete_push_subscription(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = compact_text(data.get("endpoint"))
    if not endpoint and isinstance(data.get("subscription"), dict):
        endpoint = compact_text(data["subscription"].get("endpoint"))
    if not endpoint:
        return {"ok": True, "deleted": 0}
    cursor = conn.execute(
        "DELETE FROM push_subscriptions WHERE id = ? OR endpoint = ?",
        (push_subscription_id(endpoint), endpoint),
    )
    conn.commit()
    return {"ok": True, "deleted": cursor.rowcount}


def send_push_payload(conn: sqlite3.Connection, payload: Dict[str, Any]) -> Dict[str, Any]:
    config = read_vapid_config()
    subscriptions = list_push_subscriptions(conn)
    if not config["public_key"] or not config["private_key"]:
        return {"ok": True, "sent": 0, "skipped": "VAPID keys are not configured."}
    return {"ok": True, "sent": 0, "skipped": "Local Web Push delivery is disabled.", "subscriptions": len(subscriptions)}


def get_push_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    config = read_vapid_config()
    subscriptions = list_push_subscriptions(conn)
    public_configured = bool(config["public_key"])
    private_configured = bool(config["private_key"])
    return {
        "ok": True,
        "publicKeyConfigured": public_configured,
        "privateKeyConfigured": private_configured,
        "webpushInstalled": False,
        "configured": False,
        "subscriptionCount": len(subscriptions),
        "scheduleGroup": "local",
    }


def task_due_datetime(task: Dict[str, Any], timezone_name: str = REMINDER_TIMEZONE) -> Optional[dt.datetime]:
    due_date = compact_text(task.get("due_date"))
    due_time = compact_text(task.get("due_time"))
    if not due_date or not due_time:
        return None
    try:
        local_date = dt.date.fromisoformat(due_date)
        local_time = dt.time.fromisoformat(due_time)
    except ValueError:
        return None
    return dt.datetime.combine(local_date, local_time, tzinfo=ZoneInfo(timezone_name))


def task_reminder_datetime(task: Dict[str, Any], timezone_name: str = REMINDER_TIMEZONE) -> Optional[dt.datetime]:
    reminder_at = compact_text(task.get("reminder_at"))
    if reminder_at:
        try:
            reminder = dt.datetime.fromisoformat(reminder_at)
        except ValueError:
            return None
        timezone = ZoneInfo(timezone_name)
        return reminder.replace(tzinfo=timezone) if reminder.tzinfo is None else reminder.astimezone(timezone)
    due_at = task_due_datetime(task, timezone_name)
    if not due_at:
        return None
    return due_at - dt.timedelta(minutes=TASK_REMINDER_OFFSET_MINUTES)


def task_is_active_on(task: Dict[str, Any], date_value: str) -> bool:
    if task.get("completed"):
        return False
    start_date = compact_text(task.get("start_date"))
    due_date = compact_text(task.get("due_date"))
    if start_date and due_date:
        return start_date <= date_value
    if due_date:
        return due_date <= date_value
    if start_date:
        return start_date <= date_value
    return False


def open_tasks_due_on(tasks: Iterable[Dict[str, Any]], date_value: str) -> List[Dict[str, Any]]:
    active_today = [
        task
        for task in tasks
        if task_is_active_on(task, date_value)
    ]
    return sorted(
        active_today,
        key=lambda task: (
            compact_text(task.get("due_date")) or "9999-12-31",
            compact_text(task.get("due_time")) or "23:59",
            compact_text(task.get("title")).lower(),
        ),
    )


def build_daily_summary_payload(tasks: Iterable[Dict[str, Any]], date_value: Optional[str] = None) -> Dict[str, Any]:
    local_today = date_value or dt.datetime.now(ZoneInfo(REMINDER_TIMEZONE)).date().isoformat()
    due_today = open_tasks_due_on(tasks, local_today)
    if not due_today:
        body = "No tasks to do today."
    else:
        titles = [compact_text(task.get("title")) for task in due_today[:3]]
        extra = len(due_today) - len(titles)
        body = ", ".join(titles)
        if extra > 0:
            body = f"{body}, +{extra} more"
        body = f"{len(due_today)} tasks to do: {body}"
    return {
        "title": "Today's tasks",
        "body": body,
        "url": "/?view=planner",
        "tag": f"work-diary-daily-{local_today}",
    }


def build_test_push_payload() -> Dict[str, Any]:
    return {
        "title": "Work Diary test",
        "body": "Test reminder is working.",
        "url": "/?view=planner",
        "tag": "work-diary-test",
    }


def build_task_reminder_payload(task: Dict[str, Any]) -> Dict[str, Any]:
    due_time = compact_text(task.get("due_time"))
    title = compact_text(task.get("title")) or "Task"
    return {
        "title": "Task due soon",
        "body": f"{title} is due at {due_time}." if due_time else title,
        "url": "/?view=planner",
        "tag": f"work-diary-task-{task.get('id')}",
        "task_id": task.get("id"),
    }


def schedule_task_reminder(task: Dict[str, Any]) -> None:
    return None


def delete_task_reminder_schedule(task_id: Any) -> None:
    return None


def should_schedule_task_reminder(task: Dict[str, Any], now_local: Optional[dt.datetime] = None) -> bool:
    if not task.get("id") or task.get("completed"):
        return False
    reminder_at = task_reminder_datetime(task)
    if not reminder_at:
        return False
    current_time = now_local or dt.datetime.now(ZoneInfo(REMINDER_TIMEZONE))
    return reminder_at > current_time


def sync_task_reminder_schedules(
    conn: sqlite3.Connection, tasks: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    scheduled = 0
    skipped = 0
    failed = 0
    task_list = tasks if tasks is not None else list_tasks(conn, {})
    now_local = dt.datetime.now(ZoneInfo(REMINDER_TIMEZONE))

    for task in task_list:
        if not should_schedule_task_reminder(task, now_local):
            skipped += 1
            continue
        try:
            schedule_task_reminder(task)
            scheduled += 1
        except Exception:
            failed += 1

    return {"ok": failed == 0, "scheduled": scheduled, "skipped": skipped, "failed": failed}


def read_auth_config() -> Dict[str, Any]:
    password = config_value("APP_PASSWORD")
    if password in {"change-me", "change_this_password", "your-password-here"}:
        password = ""

    session_secret = config_value("SESSION_SECRET")
    if session_secret in {"change-me", "change_this_session_secret"}:
        session_secret = ""

    try:
        session_seconds = int(config_value("SESSION_MAX_AGE_SECONDS", str(DEFAULT_SESSION_SECONDS)))
    except ValueError:
        session_seconds = DEFAULT_SESSION_SECONDS

    return {
        "password": password,
        "session_secret": session_secret,
        "session_seconds": max(300, session_seconds),
    }


def auth_is_configured(config: Optional[Dict[str, Any]] = None) -> bool:
    config = config or read_auth_config()
    return bool(config["password"] and config["session_secret"])


def password_matches(submitted: Any, config: Optional[Dict[str, Any]] = None) -> bool:
    config = config or read_auth_config()
    if not auth_is_configured(config):
        return False
    return secrets.compare_digest(str(submitted or ""), config["password"])


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def sign_session_payload(payload: str, secret: str) -> str:
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return b64url_encode(signature)


def make_session_token(
    secret: str, max_age_seconds: int = DEFAULT_SESSION_SECONDS, now: Optional[int] = None
) -> str:
    issued_at = int(now or time.time())
    payload = b64url_encode(
        response_body({"iat": issued_at, "exp": issued_at + max_age_seconds})
    )
    return f"{payload}.{sign_session_payload(payload, secret)}"


def validate_session_token(
    token: str, secret: str, now: Optional[int] = None
) -> bool:
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return False

    expected_signature = sign_session_payload(payload, secret)
    if not secrets.compare_digest(signature, expected_signature):
        return False

    try:
        claims = json.loads(b64url_decode(payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return False

    expires_at = claims.get("exp")
    if not isinstance(expires_at, int):
        return False
    return expires_at >= int(now or time.time())


def cookie_value(cookie_header: str, name: str) -> str:
    cookie = SimpleCookie()
    cookie.load(cookie_header or "")
    morsel = cookie.get(name)
    return morsel.value if morsel else ""


def is_authenticated_cookie_header(
    cookie_header: str, config: Optional[Dict[str, Any]] = None
) -> bool:
    config = config or read_auth_config()
    if not auth_is_configured(config):
        return False
    token = cookie_value(cookie_header, SESSION_COOKIE_NAME)
    return validate_session_token(token, config["session_secret"])


def build_session_cookie(config: Optional[Dict[str, Any]] = None) -> str:
    config = config or read_auth_config()
    token = make_session_token(config["session_secret"], config["session_seconds"])
    return (
        f"{SESSION_COOKIE_NAME}={token}; Path=/; Max-Age={config['session_seconds']}; "
        "HttpOnly; SameSite=Lax; Secure"
    )


def build_clear_session_cookie() -> str:
    return (
        f"{SESSION_COOKIE_NAME}=; Path=/; Max-Age=0; "
        "HttpOnly; SameSite=Lax; Secure"
    )


def is_public_path(path: str) -> bool:
    return (
        path in PUBLIC_API_PATHS
        or path in PUBLIC_PAGE_PATHS
        or path.startswith("/static/")
    )


def path_requires_auth(path: str) -> bool:
    return not is_public_path(path)


def required_text(
    data: Dict[str, Any], field: str, label: str, max_length: int = 20_000
) -> str:
    value = compact_text(data.get(field))
    if not value:
        raise ValidationError(f"{label} is required.")
    if len(value) > max_length:
        raise ValidationError(f"{label} must be {max_length:,} characters or fewer.")
    return value


def limited_text(value: Any, label: str, max_length: int) -> str:
    text = compact_text(value)
    if len(text) > max_length:
        raise ValidationError(f"{label} must be {max_length:,} characters or fewer.")
    return text


def normalize_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = re.split(r"[,;\n]", value)
    else:
        raw_items = [value]

    seen = set()
    items: List[str] = []
    for item in raw_items:
        text = compact_text(item)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            items.append(text)
    return items


def json_list(value: Any) -> str:
    return json.dumps(normalize_list(value), ensure_ascii=True)


def parse_json_list(value: str) -> List[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return normalize_list(parsed)


def parse_json_object(value: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def validate_entry_date(value: Any) -> str:
    text = compact_text(value)
    if not text:
        raise ValidationError("Date is required.")
    try:
        dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError("Date must use YYYY-MM-DD format.") from exc
    return text


def validate_optional_date(value: Any, label: str) -> str:
    text = compact_text(value)
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValidationError(f"{label} must use YYYY-MM-DD format.")
    try:
        dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"{label} must use YYYY-MM-DD format.") from exc
    return text


def validate_optional_time(value: Any, label: str) -> str:
    text = compact_text(value)
    if not text:
        return ""
    if not re.match(r"^\d{2}:\d{2}$", text):
        raise ValidationError(f"{label} must use HH:MM format.")
    try:
        dt.time.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"{label} must use HH:MM format.") from exc
    return text


def validate_optional_datetime(value: Any, label: str) -> str:
    text = compact_text(value)
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text):
        raise ValidationError(f"{label} must use YYYY-MM-DDTHH:MM format.")
    try:
        dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"{label} must use YYYY-MM-DDTHH:MM format.") from exc
    return text


def validate_task_date_range(data: Dict[str, Any]) -> tuple[str, str]:
    schedule = validate_task_schedule(data)
    return schedule.start_date, schedule.end_date


def validate_task_time_range(
    data: Dict[str, Any], start_date: str, due_date: str
) -> tuple[str, str]:
    schedule = validate_task_schedule(
        {**data, "start_date": start_date, "due_date": due_date}
    )
    return schedule.start_time, schedule.end_time


def validate_task_schedule(data: Dict[str, Any]) -> TaskSchedule:
    try:
        return TaskSchedule.from_mapping(data)
    except TaskScheduleValidationError as exc:
        raise ValidationError(str(exc)) from exc


def validate_task_priority(value: Any) -> str:
    priority = compact_text(value).lower()
    if priority not in TASK_PRIORITIES:
        raise ValidationError("Priority must be low, medium, or high.")
    return priority


def validate_boolean(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return False
    if isinstance(value, int) and value in (0, 1):
        return value == 1
    raise ValidationError(f"{label} must be a JSON boolean.")


def validate_project_order(value: Any) -> int:
    if value in {None, ""}:
        return 0
    try:
        order = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Project task order must be a number.") from exc
    if order < 0:
        raise ValidationError("Project task order cannot be negative.")
    return order


def validate_repeat_rule(value: Any) -> str:
    repeat_rule = compact_text(value).lower() or "none"
    if repeat_rule not in TASK_REPEAT_RULES:
        raise ValidationError("Repeat must be none, interval, daily, weekly, or monthly.")
    return repeat_rule


def validate_repeat_interval_days(value: Any) -> int:
    text = compact_text(value)
    if not text:
        return 1
    try:
        days = int(text)
    except ValueError as exc:
        raise ValidationError("Repeat wait days must be a whole number.") from exc
    if days < 1 or days > 3650:
        raise ValidationError("Repeat wait days must be between 1 and 3650.")
    return days


def validate_difficulty(value: Any) -> str:
    text = compact_text(value).lower()
    if text and text not in DIFFICULTY_LEVELS:
        raise ValidationError(
            "Difficulty must be easy, medium, hard, or stretch."
        )
    return text


def validate_source_mode(value: Any) -> str:
    text = compact_text(value) or "detailed"
    if text not in {"quick_log", "detailed"}:
        raise ValidationError("Source mode must be quick_log or detailed.")
    return text


def infer_title_from_note(note: str) -> str:
    first_sentence = re.split(r"(?<=[.!?])\s+", note.strip(), maxsplit=1)[0]
    cleaned = re.sub(
        r"^(today|yesterday)\s+i\s+(worked on|worked with|did|fixed|built)\s+",
        "",
        first_sentence,
        flags=re.IGNORECASE,
    )
    cleaned = compact_text(cleaned)
    if len(cleaned) <= 72:
        return cleaned or "Quick work log"
    words = cleaned.split()
    title = ""
    for word in words:
        candidate = f"{title} {word}".strip()
        if len(candidate) > 72:
            break
        title = candidate
    return title or cleaned[:72].rstrip()


def validate_project_status(value: Any) -> str:
    status = compact_text(value).lower() or "active"
    if status not in PROJECT_STATUSES:
        raise ValidationError("Project status is not supported.")
    return status


def validate_project_color(value: Any) -> str:
    color = compact_text(value) or DEFAULT_PROJECT_COLOR
    if not re.match(r"^#[0-9a-fA-F]{6}$", color):
        raise ValidationError("Project colour must be a hex colour like #5DD4C0.")
    return color.upper()


def row_to_project(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "goal": row["goal"],
        "deadline": row["deadline"],
        "status": row["status"],
        "color": row["color"],
        "notes": row["notes"],
        "completed_at": row["completed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def project_sort_key(project: Dict[str, Any]) -> Any:
    status_rank = {"active": 0, "planned": 1, "paused": 2, "complete": 3}
    return (
        status_rank.get(project.get("status"), 4),
        1 if not project.get("deadline") else 0,
        project.get("deadline") or "",
        project.get("name", "").lower(),
    )


def list_projects(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    ensure_projects_from_existing_labels(conn)
    rows = conn.execute("SELECT * FROM projects").fetchall()
    projects = [row_to_project(row) for row in rows]
    return sorted(projects, key=project_sort_key)


def get_project(conn: sqlite3.Connection, project_id: Any) -> Dict[str, Any]:
    text_id = compact_text(project_id)
    if not text_id:
        raise NotFoundError("Project not found.")
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (text_id,)).fetchone()
    if row is None:
        raise NotFoundError("Project not found.")
    return row_to_project(row)


def find_project_by_name(
    conn: sqlite3.Connection, name: Any
) -> Optional[Dict[str, Any]]:
    project_name = compact_text(name)
    if not project_name:
        return None
    row = conn.execute(
        "SELECT * FROM projects WHERE lower(name) = lower(?)",
        (project_name,),
    ).fetchone()
    return row_to_project(row) if row else None


def create_project(
    conn: sqlite3.Connection, data: Dict[str, Any], *, auto_created: bool = False
) -> Dict[str, Any]:
    timestamp = now_iso()
    name = required_text(data, "name", "Project name")
    existing = find_project_by_name(conn, name)
    if existing:
        return existing
    cursor = conn.execute(
        """
        INSERT INTO projects (
            name, goal, deadline, status, color, notes, completed_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            compact_text(data.get("goal")),
            validate_optional_date(data.get("deadline"), "Project deadline"),
            validate_project_status(data.get("status") or ("active" if auto_created else "planned")),
            validate_project_color(data.get("color")),
            compact_text(data.get("notes")),
            "",
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    return get_project(conn, int(cursor.lastrowid))


def update_project(
    conn: sqlite3.Connection, project_id: Any, data: Dict[str, Any]
) -> Dict[str, Any]:
    current = get_project(conn, project_id)
    merged = {**current, **data}
    timestamp = now_iso()
    name = required_text(merged, "name", "Project name")
    status = validate_project_status(merged.get("status"))
    completed_at = current["completed_at"]
    if status != "complete":
        completed_at = ""
    elif not completed_at:
        completed_at = timestamp
    conn.execute(
        """
        UPDATE projects
        SET name = ?,
            goal = ?,
            deadline = ?,
            status = ?,
            color = ?,
            notes = ?,
            completed_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            name,
            compact_text(merged.get("goal")),
            validate_optional_date(merged.get("deadline"), "Project deadline"),
            status,
            validate_project_color(merged.get("color")),
            compact_text(merged.get("notes")),
            completed_at,
            timestamp,
            current["id"],
        ),
    )
    conn.execute(
        "UPDATE tasks SET project = ?, updated_at = ? WHERE project_id = ?",
        (name, timestamp, str(current["id"])),
    )
    conn.execute(
        "UPDATE work_entries SET project = ?, updated_at = ? WHERE project_id = ?",
        (name, timestamp, str(current["id"])),
    )
    conn.execute(
        """
        UPDATE achievements
        SET project = ?, updated_at = ?
        WHERE source_entry_id IN (
            SELECT id FROM work_entries WHERE project_id = ?
        )
        """,
        (name, timestamp, str(current["id"])),
    )
    conn.commit()
    return get_project(conn, current["id"])


def find_project_completion_entry(
    conn: sqlite3.Connection, project_id: Any
) -> Optional[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM work_entries
        WHERE project_id = ?
        ORDER BY created_at DESC
        """,
        (str(project_id),),
    ).fetchall()
    for row in rows:
        entry = row_to_entry(row)
        if "project_completion" in entry["tags"]:
            return entry
    return None


def project_completion_note(
    conn: sqlite3.Connection, project: Dict[str, Any]
) -> str:
    tasks = [
        task
        for task in list_tasks(conn)
        if str(task.get("project_id") or "") == str(project["id"])
    ]
    completed_titles = [task["title"] for task in tasks if task["completed"]][:5]
    open_count = len([task for task in tasks if not task["completed"]])
    parts = [f"Completed project {project['name']}."]
    if project.get("goal"):
        parts.append(f"Goal: {project['goal']}.")
    if completed_titles:
        parts.append(f"Completed work included: {', '.join(completed_titles)}.")
    if open_count:
        parts.append(f"{open_count} linked planner task{'s' if open_count != 1 else ''} still remained open at completion.")
    return " ".join(parts)


def complete_project(conn: sqlite3.Connection, project_id: Any) -> Dict[str, Any]:
    current = get_project(conn, project_id)
    timestamp = now_iso()
    existing_entry = find_project_completion_entry(conn, current["id"])
    completed_at = current["completed_at"] or timestamp

    if current["status"] != "complete" or current["completed_at"] != completed_at:
        conn.execute(
            """
            UPDATE projects
            SET status = 'complete',
                completed_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (completed_at, timestamp, current["id"]),
        )
        conn.commit()

    updated = get_project(conn, current["id"])
    if not existing_entry:
        create_quick_log(
            conn,
            {
                "entry_date": completed_at[:10],
                "title": f"Completed project: {updated['name']}",
                "note": project_completion_note(conn, updated),
                "project_id": updated["id"],
                "project": updated["name"],
                "tags": ["project_completion", "project"],
            },
        )
    return get_project(conn, current["id"])


def delete_project(conn: sqlite3.Connection, project_id: Any) -> None:
    current = get_project(conn, project_id)
    timestamp = now_iso()
    linked_entry_ids = [
        row["id"]
        for row in conn.execute(
            "SELECT id FROM work_entries WHERE project_id = ?",
            (str(current["id"]),),
        ).fetchall()
    ]
    conn.execute(
        "UPDATE tasks SET project_id = '', project = '', updated_at = ? WHERE project_id = ?",
        (timestamp, str(current["id"])),
    )
    conn.execute(
        "UPDATE work_entries SET project_id = '', project = '', updated_at = ? WHERE project_id = ?",
        (timestamp, str(current["id"])),
    )
    for entry_id in linked_entry_ids:
        conn.execute(
            "UPDATE achievements SET project = '', updated_at = ? WHERE source_entry_id = ?",
            (timestamp, entry_id),
        )
    cursor = conn.execute("DELETE FROM projects WHERE id = ?", (current["id"],))
    conn.commit()
    if cursor.rowcount == 0:
        raise NotFoundError("Project not found.")


def ensure_project_for_name(
    conn: sqlite3.Connection, name: Any
) -> Optional[Dict[str, Any]]:
    project_name = compact_text(name)
    if not project_name:
        return None
    existing = find_project_by_name(conn, project_name)
    if existing:
        return existing
    return create_project(conn, {"name": project_name, "status": "active"}, auto_created=True)


def ensure_projects_from_existing_labels(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"].lower(): row["id"]
        for row in conn.execute("SELECT id, name FROM projects").fetchall()
    }
    labels = [
        row["project"]
        for row in conn.execute(
            """
            SELECT DISTINCT project FROM tasks WHERE project <> ''
            UNION
            SELECT DISTINCT project FROM work_entries WHERE project <> ''
            """
        ).fetchall()
        if compact_text(row["project"])
    ]
    for label in labels:
        name = compact_text(label)
        key = name.lower()
        project_id = existing.get(key)
        if project_id is None:
            project = create_project(
                conn,
                {"name": name, "status": "active", "color": DEFAULT_PROJECT_COLOR},
                auto_created=True,
            )
            project_id = project["id"]
            existing[key] = project_id
        project_id_text = str(project_id)
        conn.execute(
            """
            UPDATE tasks
            SET project_id = ?
            WHERE project_id = '' AND lower(project) = lower(?)
            """,
            (project_id_text, name),
        )
        conn.execute(
            """
            UPDATE work_entries
            SET project_id = ?
            WHERE project_id = '' AND lower(project) = lower(?)
            """,
            (project_id_text, name),
        )
    if labels:
        conn.commit()


def resolve_project_fields(
    conn: sqlite3.Connection, data: Dict[str, Any]
) -> tuple[str, str]:
    project_id = compact_text(data.get("project_id"))
    project_name = compact_text(data.get("project"))
    if project_id:
        project = get_project(conn, project_id)
        return str(project["id"]), project["name"]
    if project_name:
        project = ensure_project_for_name(conn, project_name)
        if project:
            return str(project["id"]), project["name"]
    return "", ""


def row_to_entry(row: sqlite3.Row, evidence_count: int = 0) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "entry_date": row["entry_date"],
        "title": row["title"],
        "what_i_did": row["what_i_did"],
        "quick_note": row["quick_note"],
        "project_id": row["project_id"],
        "project": row["project"],
        "skills_used": parse_json_list(row["skills_used"]),
        "outcome": row["outcome"],
        "reflection_notes": row["reflection_notes"],
        "tags": parse_json_list(row["tags"]),
        "difficulty": row["difficulty"],
        "source_mode": row["source_mode"],
        "cv_bullet_draft": row["cv_bullet_draft"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "evidence_count": evidence_count,
    }


def row_to_evidence(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "work_entry_id": row["work_entry_id"],
        "title": row["title"],
        "evidence_type": row["evidence_type"],
        "evidence_type_label": EVIDENCE_TYPES.get(
            row["evidence_type"], row["evidence_type"]
        ),
        "evidence_url": row["evidence_url"],
        "description": row["description"],
        "provider": row["provider"],
        "provider_metadata": parse_json_object(row["provider_metadata"]),
        "storage_key": row["storage_key"],
        "attachment_status": row["attachment_status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_achievement(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "source_entry_id": row["source_entry_id"],
        "achieved_at": row["achieved_at"],
        "bullet": row["bullet"],
        "project": row["project"],
        "skills_used": parse_json_list(row["skills_used"]),
        "tags": parse_json_list(row["tags"]),
        "source": row["source"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def row_to_task(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "project_id": row["project_id"],
        "project": row["project"],
        "start_date": row["start_date"],
        "start_time": row["start_time"],
        "due_date": row["due_date"],
        "due_time": row["due_time"],
        "reminder_at": row["reminder_at"],
        "repeat_rule": row["repeat_rule"],
        "repeat_interval_days": row["repeat_interval_days"],
        "repeat_until": row["repeat_until"],
        "priority": row["priority"],
        "location": row["location"],
        "notes": row["notes"],
        "project_order": int(row["project_order"] or 0),
        "google_sync_target": row["google_sync_target"],
        "google_calendar_event_id": row["google_calendar_event_id"],
        "google_calendar_event_link": row["google_calendar_event_link"],
        "google_task_id": row["google_task_id"],
        "google_task_link": row["google_task_link"],
        "google_sync_hash": row["google_sync_hash"],
        "google_synced_at": row["google_synced_at"],
        "google_sync_error": row["google_sync_error"],
        "completed": bool(row["completed"]),
        "completed_at": row["completed_at"],
        "archived": bool(row["archived"]),
        "archived_at": row["archived_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def next_project_task_order(conn: sqlite3.Connection, project_id: str) -> int:
    if not compact_text(project_id):
        return 0
    row = conn.execute(
        "SELECT COALESCE(MAX(project_order), 0) AS max_order FROM tasks WHERE project_id = ?",
        (str(project_id),),
    ).fetchone()
    return int(row["max_order"] or 0) + 10


def create_task(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_iso()
    completed = validate_boolean(data.get("completed"), "Completed")
    archived = False
    schedule = validate_task_schedule(data)
    start_date, due_date = schedule.start_date, schedule.end_date
    start_time, due_time = schedule.start_time, schedule.end_time
    project_id, project_name = resolve_project_fields(conn, data)
    project_order = validate_project_order(data.get("project_order"))
    if project_id and not project_order:
        project_order = next_project_task_order(conn, project_id)
    cursor = conn.execute(
        """
        INSERT INTO tasks (
            title, project_id, project, start_date, start_time, due_date, due_time, reminder_at, repeat_rule,
            repeat_interval_days, repeat_until, priority,
            location, notes, project_order,
            google_sync_target, google_calendar_event_id, google_calendar_event_link,
            google_task_id, google_task_link, google_sync_hash, google_synced_at, google_sync_error,
            completed, completed_at, archived, archived_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            required_text(data, "title", "Task title", 240),
            project_id,
            project_name,
            start_date,
            start_time,
            due_date,
            due_time,
            validate_optional_datetime(data.get("reminder_at"), "Reminder"),
            validate_repeat_rule(data.get("repeat_rule")),
            validate_repeat_interval_days(data.get("repeat_interval_days")),
            validate_optional_date(data.get("repeat_until"), "Repeat stop date"),
            validate_task_priority(data.get("priority")),
            limited_text(data.get("location"), "Location", 500),
            limited_text(data.get("notes"), "Notes", 20_000),
            project_order,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            "",
            1 if completed else 0,
            timestamp if completed else "",
            1 if archived else 0,
            timestamp if archived else "",
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    task = get_task(conn, int(cursor.lastrowid))
    schedule_task_reminder(task)
    auto_sync_task_after_save(conn, task)
    return task


def get_task(conn: sqlite3.Connection, task_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise NotFoundError("Task not found.")
    return row_to_task(row)


def list_tasks(
    conn: sqlite3.Connection, filters: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    filters = filters or {}
    rows = conn.execute(
        """
        SELECT *
        FROM tasks
        ORDER BY
            completed ASC,
            CASE WHEN COALESCE(NULLIF(start_date, ''), NULLIF(due_date, ''), '') = '' THEN 1 ELSE 0 END ASC,
            COALESCE(NULLIF(start_date, ''), NULLIF(due_date, ''), '') ASC,
            start_time ASC,
            due_date ASC,
            due_time ASC,
            created_at DESC
        """
    ).fetchall()
    tasks = [row_to_task(row) for row in rows]
    archived_filter = compact_text(filters.get("archived")).lower()
    if archived_filter in {"true", "1", "yes"}:
        tasks = [task for task in tasks if task["archived"]]
    elif archived_filter in {"false", "0", "no"}:
        tasks = [task for task in tasks if not task["archived"]]
    completed_filter = compact_text(filters.get("completed")).lower()
    if completed_filter in {"true", "1", "yes"}:
        return [task for task in tasks if task["completed"]]
    if completed_filter in {"false", "0", "no"}:
        return [task for task in tasks if not task["completed"]]
    return tasks


def update_task(
    conn: sqlite3.Connection, task_id: int, data: Dict[str, Any]
) -> Dict[str, Any]:
    current = get_task(conn, task_id)
    merged = {**current, **data}
    timestamp = now_iso()
    completed = validate_boolean(merged.get("completed"), "Completed")
    completed_at = current["completed_at"]
    if completed and not current["completed"]:
        completed_at = timestamp
    if not completed:
        completed_at = ""
    archived = validate_boolean(merged.get("archived"), "Archived")
    if archived and not completed:
        raise ValidationError("Only completed tasks can be archived.")
    archived_at = current.get("archived_at", "")
    if archived and not current.get("archived"):
        archived_at = timestamp
    if not archived:
        archived_at = ""
    schedule = validate_task_schedule(merged)
    start_date, due_date = schedule.start_date, schedule.end_date
    start_time, due_time = schedule.start_time, schedule.end_time
    project_id, project_name = resolve_project_fields(conn, merged)
    project_order = validate_project_order(merged.get("project_order"))
    if str(project_id or "") != str(current.get("project_id") or ""):
        project_order = 0
    if project_id and not project_order:
        project_order = next_project_task_order(conn, project_id)

    conn.execute(
        """
        UPDATE tasks
        SET title = ?,
            project_id = ?,
            project = ?,
            start_date = ?,
            start_time = ?,
            due_date = ?,
            due_time = ?,
            reminder_at = ?,
            repeat_rule = ?,
            repeat_interval_days = ?,
            repeat_until = ?,
            priority = ?,
            location = ?,
            notes = ?,
            project_order = ?,
            google_sync_target = ?,
            google_calendar_event_id = ?,
            google_calendar_event_link = ?,
            google_task_id = ?,
            google_task_link = ?,
            google_sync_hash = ?,
            google_synced_at = ?,
            google_sync_error = ?,
            completed = ?,
            completed_at = ?,
            archived = ?,
            archived_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            required_text(merged, "title", "Task title", 240),
            project_id,
            project_name,
            start_date,
            start_time,
            due_date,
            due_time,
            validate_optional_datetime(merged.get("reminder_at"), "Reminder"),
            validate_repeat_rule(merged.get("repeat_rule")),
            validate_repeat_interval_days(merged.get("repeat_interval_days")),
            validate_optional_date(merged.get("repeat_until"), "Repeat stop date"),
            validate_task_priority(merged.get("priority")),
            limited_text(merged.get("location"), "Location", 500),
            limited_text(merged.get("notes"), "Notes", 20_000),
            project_order,
            compact_text(current.get("google_sync_target")),
            compact_text(current.get("google_calendar_event_id")),
            compact_text(current.get("google_calendar_event_link")),
            compact_text(current.get("google_task_id")),
            compact_text(current.get("google_task_link")),
            compact_text(current.get("google_sync_hash")),
            compact_text(current.get("google_synced_at")),
            compact_text(current.get("google_sync_error")),
            1 if completed else 0,
            completed_at,
            1 if archived else 0,
            archived_at,
            timestamp,
            task_id,
        ),
    )
    conn.commit()
    updated = get_task(conn, task_id)
    if updated["completed"] or updated["archived"] or not task_reminder_datetime(updated):
        delete_task_reminder_schedule(task_id)
    else:
        schedule_task_reminder(updated)
    auto_sync_task_after_save(conn, updated, current)
    if completed and not current["completed"] and validate_repeat_rule(merged.get("repeat_rule")) != "none":
        create_next_repeating_task(conn, {**merged, "completed": False})
    return updated


def project_open_tasks(conn: sqlite3.Connection, project_id: Any) -> List[Dict[str, Any]]:
    project = get_project(conn, project_id)
    return [
        task
        for task in list_tasks(conn)
        if not task["completed"] and not task.get("archived") and str(task.get("project_id") or "") == str(project["id"])
    ]


def reorder_project_tasks(
    conn: sqlite3.Connection, project_id: Any, data: Dict[str, Any]
) -> Dict[str, Any]:
    project = get_project(conn, project_id)
    raw_ids = data.get("task_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValidationError("Task order must include task_ids.")
    task_ids = [compact_text(task_id) for task_id in raw_ids]
    if any(not task_id for task_id in task_ids) or len(task_ids) != len(set(task_ids)):
        raise ValidationError("Task order contains invalid task ids.")

    tasks = project_open_tasks(conn, project["id"])
    task_map = {str(task["id"]): task for task in tasks}
    invalid_ids = [task_id for task_id in task_ids if task_id not in task_map]
    if invalid_ids:
        raise ValidationError("Only open tasks linked to this project can be reordered.")
    if set(task_ids) != set(task_map):
        raise ValidationError("Task order must include every open task in the project.")

    timestamp = now_iso()
    for index, task_id in enumerate(task_ids, start=1):
        conn.execute(
            "UPDATE tasks SET project_order = ?, updated_at = ? WHERE id = ?",
            (index * 10, timestamp, task_id),
        )
    conn.commit()
    return {"ok": True, "tasks": project_open_tasks(conn, project["id"])}


def create_next_repeating_task(conn: sqlite3.Connection, task: Dict[str, Any]) -> None:
    repeat_rule = validate_repeat_rule(task.get("repeat_rule"))
    if repeat_rule == "none":
        return

    start_date = validate_optional_date(task.get("start_date"), "Start date")
    base_date = validate_optional_date(task.get("due_date"), "Due date")
    repeat_base_date = base_date or start_date
    if repeat_base_date:
        next_due_date = next_repeat_date(repeat_base_date, repeat_rule, task.get("repeat_interval_days"))
    else:
        next_due_date = next_repeat_date(today_iso(), repeat_rule, task.get("repeat_interval_days"))
    next_start_date = next_repeat_start_date(
        start_date,
        repeat_base_date or today_iso(),
        next_due_date,
    )

    repeat_until = validate_optional_date(task.get("repeat_until"), "Repeat stop date")
    if repeat_until and next_due_date > repeat_until:
        return

    next_reminder = next_repeat_datetime(
        task.get("reminder_at"),
        repeat_rule,
        repeat_base_date or today_iso(),
        next_due_date,
    )

    create_task(
        conn,
        {
            "title": task.get("title"),
            "project_id": task.get("project_id"),
            "project": task.get("project"),
            "start_date": next_start_date,
            "start_time": task.get("start_time"),
            "due_date": next_due_date if base_date else "",
            "due_time": task.get("due_time"),
            "reminder_at": next_reminder,
            "repeat_rule": repeat_rule,
            "repeat_interval_days": task.get("repeat_interval_days"),
            "repeat_until": repeat_until,
            "priority": task.get("priority"),
            "location": task.get("location"),
            "notes": task.get("notes"),
        },
    )


def next_repeat_date(base_date: str, repeat_rule: str, repeat_interval_days: Any = None) -> str:
    date_value = dt.date.fromisoformat(base_date)
    if repeat_rule == "interval":
        return (date_value + dt.timedelta(days=validate_repeat_interval_days(repeat_interval_days))).isoformat()
    if repeat_rule == "daily":
        return (date_value + dt.timedelta(days=1)).isoformat()
    if repeat_rule == "weekly":
        return (date_value + dt.timedelta(days=7)).isoformat()
    if repeat_rule == "monthly":
        return add_month(date_value).isoformat()
    return base_date


def next_repeat_start_date(start_date: str, old_base_date: str, next_base_date: str) -> str:
    if not start_date:
        return ""
    try:
        start = dt.date.fromisoformat(start_date)
        old_base = dt.date.fromisoformat(old_base_date)
        next_base = dt.date.fromisoformat(next_base_date)
    except ValueError:
        return ""
    return (start + (next_base - old_base)).isoformat()


def add_month(date_value: dt.date) -> dt.date:
    month = date_value.month + 1
    year = date_value.year
    if month == 13:
        month = 1
        year += 1
    month_lengths = [31, 29 if is_leap_year(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    day = min(date_value.day, month_lengths[month - 1])
    return dt.date(year, month, day)


def is_leap_year(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def next_repeat_datetime(
    reminder_at: Any, repeat_rule: str, old_due_date: str, next_due_date: str
) -> str:
    reminder = validate_optional_datetime(reminder_at, "Reminder")
    if not reminder:
        return ""
    try:
        reminder_dt = dt.datetime.fromisoformat(reminder)
        old_due = dt.date.fromisoformat(old_due_date)
        next_due = dt.date.fromisoformat(next_due_date)
    except ValueError:
        return ""
    delta = next_due - old_due
    return (reminder_dt + delta).strftime("%Y-%m-%dT%H:%M")


def delete_task(conn: sqlite3.Connection, task_id: int) -> None:
    task = get_task(conn, task_id)
    auto_delete_google_for_task(conn, task)
    cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    if cursor.rowcount == 0:
        raise NotFoundError("Task not found.")
    delete_task_reminder_schedule(task_id)


def list_evidence_for_entry(
    conn: sqlite3.Connection, entry_id: int
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM evidence WHERE work_entry_id = ? ORDER BY created_at DESC",
        (entry_id,),
    ).fetchall()
    return [row_to_evidence(row) for row in rows]


def create_entry(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_iso()
    entry_date = validate_entry_date(data.get("entry_date"))
    title = required_text(data, "title", "Title")
    what_i_did = required_text(data, "what_i_did", "What I did")
    source_mode = validate_source_mode(data.get("source_mode"))
    difficulty = validate_difficulty(data.get("difficulty"))
    project_id, project_name = resolve_project_fields(conn, data)

    cursor = conn.execute(
        """
        INSERT INTO work_entries (
            entry_date, title, what_i_did, quick_note, project_id, project, skills_used,
            outcome, reflection_notes, tags, difficulty, source_mode,
            cv_bullet_draft, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_date,
            title,
            what_i_did,
            compact_text(data.get("quick_note")),
            project_id,
            project_name,
            json_list(data.get("skills_used")),
            compact_text(data.get("outcome")),
            compact_text(data.get("reflection_notes")),
            json_list(data.get("tags")),
            difficulty,
            source_mode,
            compact_text(data.get("cv_bullet_draft")),
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    entry = get_entry(conn, int(cursor.lastrowid))
    replace_achievements_for_entry(conn, entry)
    return entry


def create_quick_log(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    note = required_text(data, "note", "Quick log")
    payload = {
        "entry_date": compact_text(data.get("entry_date")) or today_iso(),
        "title": compact_text(data.get("title")) or infer_title_from_note(note),
        "what_i_did": note,
        "quick_note": note,
        "source_mode": "quick_log",
        "project_id": data.get("project_id", ""),
        "project": data.get("project", ""),
        "skills_used": data.get("skills_used", []),
        "tags": data.get("tags", []),
    }
    return create_entry(conn, payload)


def get_entry(conn: sqlite3.Connection, entry_id: int) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT w.*, COUNT(e.id) AS evidence_count
        FROM work_entries w
        LEFT JOIN evidence e ON e.work_entry_id = w.id
        WHERE w.id = ?
        GROUP BY w.id
        """,
        (entry_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError("Work entry not found.")
    return row_to_entry(row, evidence_count=row["evidence_count"])


def list_entries(
    conn: sqlite3.Connection, filters: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    filters = filters or {}
    rows = conn.execute(
        """
        SELECT w.*, COUNT(e.id) AS evidence_count
        FROM work_entries w
        LEFT JOIN evidence e ON e.work_entry_id = w.id
        GROUP BY w.id
        ORDER BY w.entry_date DESC, w.created_at DESC
        """
    ).fetchall()
    entries = [row_to_entry(row, evidence_count=row["evidence_count"]) for row in rows]
    return [entry for entry in entries if entry_matches(entry, filters)]


def entry_matches(entry: Dict[str, Any], filters: Dict[str, str]) -> bool:
    search = compact_text(filters.get("search")).lower()
    project = compact_text(filters.get("project")).lower()
    skill = compact_text(filters.get("skill")).lower()
    tag = compact_text(filters.get("tag")).lower()

    if search:
        haystack = " ".join(
            [
                entry["title"],
                entry["what_i_did"],
                entry["project"],
                entry["outcome"],
                entry["reflection_notes"],
                " ".join(entry["skills_used"]),
                " ".join(entry["tags"]),
            ]
        ).lower()
        if search not in haystack:
            return False
    if project and entry["project"].lower() != project:
        return False
    if skill and skill not in {item.lower() for item in entry["skills_used"]}:
        return False
    if tag and tag not in {item.lower() for item in entry["tags"]}:
        return False
    return True


def list_achievements(
    conn: sqlite3.Connection, filters: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    filters = filters or {}
    rows = conn.execute(
        """
        SELECT *
        FROM achievements
        ORDER BY achieved_at DESC, created_at DESC
        """
    ).fetchall()
    achievements = [row_to_achievement(row) for row in rows]
    return [
        achievement
        for achievement in achievements
        if achievement_matches(achievement, filters)
    ]


def achievement_matches(item: Dict[str, Any], filters: Dict[str, str]) -> bool:
    search = compact_text(filters.get("search")).lower()
    project = compact_text(filters.get("project")).lower()
    skill = compact_text(filters.get("skill")).lower()
    tag = compact_text(filters.get("tag")).lower()
    source_entry_id = compact_text(filters.get("source_entry_id"))

    if source_entry_id and str(item["source_entry_id"]) != source_entry_id:
        return False
    if search:
        haystack = " ".join(
            [
                item["bullet"],
                item["project"],
                item["source"],
                " ".join(item["skills_used"]),
                " ".join(item["tags"]),
            ]
        ).lower()
        if search not in haystack:
            return False
    if project and item["project"].lower() != project:
        return False
    if skill and skill not in {value.lower() for value in item["skills_used"]}:
        return False
    if tag and tag not in {value.lower() for value in item["tags"]}:
        return False
    return True


def replace_achievements_for_entry(
    conn: sqlite3.Connection, entry: Dict[str, Any], source: str = "auto"
) -> List[Dict[str, Any]]:
    timestamp = now_iso()
    bullets = extract_achievement_bullets(entry)
    conn.execute(
        "DELETE FROM achievements WHERE source_entry_id = ? AND source = ?",
        (entry["id"], source),
    )
    for bullet in bullets:
        conn.execute(
            """
            INSERT INTO achievements (
                source_entry_id, achieved_at, bullet, project, skills_used,
                tags, source, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["id"],
                entry["entry_date"],
                bullet,
                entry["project"],
                json_list(entry["skills_used"]),
                json_list(entry["tags"]),
                source,
                timestamp,
                timestamp,
            ),
        )
    conn.commit()
    return list_achievements(conn, {"source_entry_id": str(entry["id"])})


def update_entry(
    conn: sqlite3.Connection, entry_id: int, data: Dict[str, Any]
) -> Dict[str, Any]:
    current = get_entry(conn, entry_id)
    merged = {**current, **data}
    refresh_achievements = any(
        key in data
        for key in {
            "entry_date",
            "title",
            "what_i_did",
            "quick_note",
            "project_id",
            "project",
            "skills_used",
            "outcome",
            "reflection_notes",
            "tags",
            "difficulty",
        }
    )
    timestamp = now_iso()

    entry_date = validate_entry_date(merged.get("entry_date"))
    title = required_text(merged, "title", "Title")
    what_i_did = required_text(merged, "what_i_did", "What I did")
    source_mode = validate_source_mode(merged.get("source_mode"))
    difficulty = validate_difficulty(merged.get("difficulty"))
    project_id, project_name = resolve_project_fields(conn, merged)

    conn.execute(
        """
        UPDATE work_entries
        SET entry_date = ?,
            title = ?,
            what_i_did = ?,
            quick_note = ?,
            project_id = ?,
            project = ?,
            skills_used = ?,
            outcome = ?,
            reflection_notes = ?,
            tags = ?,
            difficulty = ?,
            source_mode = ?,
            cv_bullet_draft = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            entry_date,
            title,
            what_i_did,
            compact_text(merged.get("quick_note")),
            project_id,
            project_name,
            json_list(merged.get("skills_used")),
            compact_text(merged.get("outcome")),
            compact_text(merged.get("reflection_notes")),
            json_list(merged.get("tags")),
            difficulty,
            source_mode,
            compact_text(merged.get("cv_bullet_draft")),
            timestamp,
            entry_id,
        ),
    )
    conn.commit()
    entry = get_entry(conn, entry_id)
    if refresh_achievements:
        replace_achievements_for_entry(conn, entry)
    return entry


def delete_entry(conn: sqlite3.Connection, entry_id: int) -> None:
    conn.execute("DELETE FROM achievements WHERE source_entry_id = ?", (entry_id,))
    cursor = conn.execute("DELETE FROM work_entries WHERE id = ?", (entry_id,))
    conn.commit()
    if cursor.rowcount == 0:
        raise NotFoundError("Work entry not found.")


def validate_url(value: Any, evidence_type: str) -> str:
    text = compact_text(value)
    if evidence_type in {"uploaded_file_placeholder", "image"}:
        return text
    if not text:
        raise ValidationError("Evidence URL is required.")
    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        raise ValidationError("Evidence URL must start with http:// or https://.")
    return text


def validate_evidence_type(value: Any) -> str:
    evidence_type = compact_text(value)
    if evidence_type not in EVIDENCE_TYPES:
        raise ValidationError("Evidence type is not supported.")
    return evidence_type


def create_evidence(
    conn: sqlite3.Connection, data: Dict[str, Any]
) -> Dict[str, Any]:
    timestamp = now_iso()
    try:
        work_entry_id = int(data.get("work_entry_id"))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Related work entry ID is required.") from exc

    get_entry(conn, work_entry_id)
    evidence_type = validate_evidence_type(data.get("evidence_type"))
    evidence_url = validate_url(data.get("evidence_url"), evidence_type)
    provider_metadata = data.get("provider_metadata") or {}
    if not isinstance(provider_metadata, dict):
        raise ValidationError("Provider metadata must be an object.")

    cursor = conn.execute(
        """
        INSERT INTO evidence (
            work_entry_id, title, evidence_type, evidence_url, description,
            provider, provider_metadata, storage_key, attachment_status,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            work_entry_id,
            required_text(data, "title", "Evidence title"),
            evidence_type,
            evidence_url,
            compact_text(data.get("description")),
            compact_text(data.get("provider")),
            json.dumps(provider_metadata, ensure_ascii=True),
            compact_text(data.get("storage_key")),
            compact_text(data.get("attachment_status")) or "linked",
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    return get_evidence(conn, int(cursor.lastrowid))


def decode_image_data_url(value: Any) -> tuple[bytes, str]:
    text = str(value or "")
    match = re.match(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", text, re.DOTALL)
    if not match:
        raise ValidationError("Image must be sent as a base64 data URL.")
    content_type = match.group(1).lower()
    if content_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValidationError("Image must be JPEG, PNG, or WebP.")
    try:
        raw = base64.b64decode(match.group(2), validate=True)
    except binascii.Error as exc:
        raise ValidationError("Image data is not valid base64.") from exc
    if len(raw) > MAX_IMAGE_UPLOAD_BYTES:
        raise ValidationError("Image is too large after compression.")
    return raw, content_type


def safe_upload_filename(value: Any, content_type: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", compact_text(value)).strip("-._")
    if not stem:
        stem = "photo"
    extension = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(content_type, ".jpg")
    if not stem.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        stem += extension
    return stem[:96]


def create_image_evidence(
    conn: sqlite3.Connection, data: Dict[str, Any]
) -> Dict[str, Any]:
    raw_image, content_type = decode_image_data_url(data.get("data_url"))
    filename = safe_upload_filename(data.get("filename"), content_type)
    comment = compact_text(data.get("comment"))
    work_entry_id = compact_text(data.get("work_entry_id"))
    if work_entry_id:
        entry = get_entry(conn, int(work_entry_id))
    else:
        entry = create_quick_log(
            conn,
            {
                "note": comment or "Photo evidence added.",
                "entry_date": compact_text(data.get("entry_date")) or today_iso(),
                "project": data.get("project", ""),
                "tags": ["evidence"],
            },
        )

    object_name = f"{entry['id']}-{secrets.token_hex(8)}-{filename}"
    object_path = UPLOADS_DIR / object_name
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    object_path.write_bytes(raw_image)
    evidence = create_evidence(
        conn,
        {
            "work_entry_id": entry["id"],
            "title": compact_text(data.get("title")) or filename,
            "evidence_type": "image",
            "evidence_url": f"/uploads/{object_name}",
            "description": comment,
            "provider": "local",
            "provider_metadata": {
                "original_filename": filename,
                "content_type": content_type,
                "size_bytes": len(raw_image),
            },
            "storage_key": object_name,
            "attachment_status": "uploaded",
        },
    )
    return {"entry": entry, "evidence": evidence}


def get_evidence(conn: sqlite3.Connection, evidence_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM evidence WHERE id = ?", (evidence_id,)).fetchone()
    if row is None:
        raise NotFoundError("Evidence not found.")
    return row_to_evidence(row)


def list_evidence(
    conn: sqlite3.Connection, filters: Optional[Dict[str, str]] = None
) -> List[Dict[str, Any]]:
    filters = filters or {}
    rows = conn.execute(
        """
        SELECT
            e.*,
            w.title AS entry_title,
            w.entry_date,
            w.project,
            w.skills_used,
            w.tags
        FROM evidence e
        JOIN work_entries w ON w.id = e.work_entry_id
        ORDER BY w.entry_date DESC, e.created_at DESC
        """
    ).fetchall()

    evidence_items: List[Dict[str, Any]] = []
    for row in rows:
        item = row_to_evidence(row)
        item["entry_title"] = row["entry_title"]
        item["entry_date"] = row["entry_date"]
        item["project"] = row["project"]
        item["entry_skills_used"] = parse_json_list(row["skills_used"])
        item["entry_tags"] = parse_json_list(row["tags"])
        if evidence_matches(item, filters):
            evidence_items.append(item)
    return evidence_items


def evidence_matches(item: Dict[str, Any], filters: Dict[str, str]) -> bool:
    entry_id = compact_text(filters.get("entry_id"))
    project = compact_text(filters.get("project")).lower()
    skill = compact_text(filters.get("skill")).lower()
    evidence_type = compact_text(filters.get("evidence_type"))

    if entry_id and str(item["work_entry_id"]) != entry_id:
        return False
    if project and item["project"].lower() != project:
        return False
    if skill and skill not in {value.lower() for value in item["entry_skills_used"]}:
        return False
    if evidence_type and item["evidence_type"] != evidence_type:
        return False
    return True


def update_evidence(
    conn: sqlite3.Connection, evidence_id: int, data: Dict[str, Any]
) -> Dict[str, Any]:
    current = get_evidence(conn, evidence_id)
    merged = {**current, **data}
    evidence_type = validate_evidence_type(merged.get("evidence_type"))
    evidence_url = validate_url(merged.get("evidence_url"), evidence_type)
    timestamp = now_iso()
    provider_metadata = merged.get("provider_metadata") or {}
    if not isinstance(provider_metadata, dict):
        raise ValidationError("Provider metadata must be an object.")

    conn.execute(
        """
        UPDATE evidence
        SET title = ?,
            evidence_type = ?,
            evidence_url = ?,
            description = ?,
            provider = ?,
            provider_metadata = ?,
            storage_key = ?,
            attachment_status = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            required_text(merged, "title", "Evidence title"),
            evidence_type,
            evidence_url,
            compact_text(merged.get("description")),
            compact_text(merged.get("provider")),
            json.dumps(provider_metadata, ensure_ascii=True),
            compact_text(merged.get("storage_key")),
            compact_text(merged.get("attachment_status")) or "linked",
            timestamp,
            evidence_id,
        ),
    )
    conn.commit()
    return get_evidence(conn, evidence_id)


def delete_evidence(conn: sqlite3.Connection, evidence_id: int) -> None:
    cursor = conn.execute("DELETE FROM evidence WHERE id = ?", (evidence_id,))
    conn.commit()
    if cursor.rowcount == 0:
        raise NotFoundError("Evidence not found.")


def build_cv_bullet(entry: Dict[str, Any]) -> str:
    subject = compact_text(entry.get("title")).rstrip(".")
    work = compact_text(entry.get("what_i_did")).rstrip(".")
    outcome = compact_text(entry.get("outcome")).rstrip(".")
    skills = normalize_list(entry.get("skills_used"))

    parts = [work or subject]
    if skills:
        parts.append("using " + ", ".join(skills[:4]))
    if outcome:
        if len(outcome) > 1 and outcome[:2].isupper():
            outcome_phrase = outcome
        else:
            outcome_phrase = outcome[0].lower() + outcome[1:]
        parts.append("resulting in " + outcome_phrase)

    bullet = "; ".join(parts)
    if not bullet:
        bullet = subject or "Documented work completed"
    return bullet[0].upper() + bullet[1:] + "."


def clean_achievement_bullet(value: Any, max_length: int = 190) -> str:
    text = compact_text(value).strip().strip('"')
    text = re.sub(r"^(\d+[\.)]\s*|[-*\u2022]\s*)", "", text).strip()
    if not text:
        return ""
    text = re.sub(r"^(today|yesterday)\s+i\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^i\s+", "", text, flags=re.IGNORECASE)
    text = compact_text(text)
    if len(text) > max_length:
        words = text.split()
        shortened = ""
        for word in words:
            candidate = f"{shortened} {word}".strip()
            if len(candidate) > max_length - 1:
                break
            shortened = candidate
        text = shortened or text[: max_length - 1].rstrip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    return text


def achievement_sentence_candidates(value: Any) -> List[str]:
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|[\n;]+", text)
    return [clean_achievement_bullet(part) for part in parts if compact_text(part)]


def fallback_achievement_bullets(entry: Dict[str, Any]) -> List[str]:
    title = compact_text(entry.get("title")).rstrip(".")
    outcome = compact_text(entry.get("outcome")).rstrip(".")
    skills = normalize_list(entry.get("skills_used"))
    candidates: List[str] = []

    if outcome and title:
        candidates.append(clean_achievement_bullet(f"Delivered {title}; {outcome}"))
    candidates.append(clean_achievement_bullet(build_cv_bullet(entry)))
    candidates.extend(achievement_sentence_candidates(entry.get("what_i_did")))
    if title:
        if skills:
            candidates.append(
                clean_achievement_bullet(
                    f"Completed {title} using {', '.join(skills[:4])}"
                )
            )
        else:
            candidates.append(clean_achievement_bullet(f"Completed {title}"))

    bullets: List[str] = []
    seen = set()
    for candidate in candidates:
        key = candidate.lower()
        if candidate and key not in seen:
            seen.add(key)
            bullets.append(candidate)
        if len(bullets) == MAX_ACHIEVEMENT_BULLETS:
            break
    return bullets or ["Documented work completed."]


ACHIEVEMENT_INSTRUCTIONS = """
You turn private work diary notes into achievement bullets for a career log.
Output 1 to 10 concise bullet sentences with no markdown.
Each bullet must be truthful, specific, and understandable without extra context.
Do not invent numbers, employers, titles, dates, or outcomes.
Prefer UK English spelling.
""".strip()


def build_achievement_llm_input(entry: Dict[str, Any]) -> str:
    payload = {
        "task": "Extract concise achievements from this work diary entry.",
        "entry": {
            "date": entry["entry_date"],
            "title": entry["title"],
            "what_i_did": entry["what_i_did"],
            "project": entry["project"],
            "skills_used": entry["skills_used"],
            "outcome": entry["outcome"],
            "tags": entry["tags"],
            "difficulty": entry["difficulty"],
            "reflection_notes": entry["reflection_notes"],
        },
        "success_criteria": [
            "Return 1 to 10 bullet sentences.",
            "No markdown bullet markers.",
            "Keep each bullet concise.",
            "Do not claim impact that is not present in the entry.",
        ],
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def clean_achievement_lines(value: Any) -> List[str]:
    bullets: List[str] = []
    seen = set()
    for line in str(value or "").splitlines():
        cleaned = clean_achievement_bullet(line)
        key = cleaned.lower()
        if cleaned and key not in seen:
            seen.add(key)
            bullets.append(cleaned)
        if len(bullets) == MAX_ACHIEVEMENT_BULLETS:
            break
    return bullets


def extract_openai_lines(payload: Dict[str, Any]) -> List[str]:
    direct = clean_achievement_lines(payload.get("output_text"))
    if direct:
        return direct

    text_parts: List[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text_parts.append(str(content.get("text", "")))
    return clean_achievement_lines("\n".join(text_parts))


def generate_llm_achievements(
    entry: Dict[str, Any],
    config: Dict[str, str],
) -> List[str]:
    body: Dict[str, Any] = {
        "model": config["model"],
        "instructions": ACHIEVEMENT_INSTRUCTIONS,
        "input": build_achievement_llm_input(entry),
        "text": {"verbosity": "low"},
    }
    if config.get("reasoning_effort") and model_supports_reasoning(config["model"]):
        body["reasoning"] = {"effort": config["reasoning_effort"]}

    payload = post_openai_response(body, config["api_key"])
    bullets = extract_openai_lines(payload)
    if not bullets:
        raise OpenAIRequestError("OpenAI did not return achievement bullets.")
    return bullets


def extract_achievement_bullets(
    entry: Dict[str, Any],
    config: Optional[Dict[str, str]] = None,
) -> List[str]:
    config = config or read_openai_config()
    if config.get("api_key"):
        try:
            return generate_llm_achievements(entry, config)
        except Exception:
            return fallback_achievement_bullets(entry)
    return fallback_achievement_bullets(entry)


CV_BULLET_INSTRUCTIONS = """
You turn private work diary notes into one CV bullet.
Output exactly one concise bullet sentence with no markdown.
Use a strong active verb and preserve technical detail.
Stay truthful: do not invent metrics, employers, job titles, scope, dates, or outcomes.
If the entry has no measurable result, use a credible qualitative result from the provided outcome or work performed.
Prefer UK English spelling.
""".strip()


def build_llm_input(
    entry: Dict[str, Any], evidence_items: List[Dict[str, Any]]
) -> str:
    payload = {
        "task": "Draft one CV bullet from this work diary entry.",
        "entry": {
            "date": entry["entry_date"],
            "title": entry["title"],
            "what_i_did": entry["what_i_did"],
            "project": entry["project"],
            "skills_used": entry["skills_used"],
            "outcome": entry["outcome"],
            "tags": entry["tags"],
            "difficulty": entry["difficulty"],
            "reflection_notes": entry["reflection_notes"],
        },
        "evidence": [
            {
                "title": item["title"],
                "type": item["evidence_type_label"],
                "url": item["evidence_url"],
                "description": item["description"],
            }
            for item in evidence_items
        ],
        "success_criteria": [
            "One sentence only.",
            "No markdown bullet marker.",
            "Keep it suitable for a CV.",
            "Do not claim numbers or impact that are not in the entry.",
        ],
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def post_openai_response(body: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    request = Request(
        OPENAI_RESPONSES_URL,
        data=response_body(body),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=45) as response:
            raw_body = response.read().decode("utf-8")
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise OpenAIRequestError(parse_openai_error(error_body, exc.code)) from exc
    except URLError as exc:
        raise OpenAIRequestError(f"Could not reach OpenAI: {exc.reason}") from exc

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise OpenAIRequestError("OpenAI returned a non-JSON response.") from exc
    if not isinstance(payload, dict):
        raise OpenAIRequestError("OpenAI returned an unexpected response shape.")
    return payload


def parse_openai_error(error_body: str, status: int) -> str:
    try:
        payload = json.loads(error_body)
    except json.JSONDecodeError:
        return f"OpenAI request failed with HTTP {status}."
    message = payload.get("error", {}).get("message") if isinstance(payload, dict) else ""
    return compact_text(message) or f"OpenAI request failed with HTTP {status}."


def extract_openai_text(payload: Dict[str, Any]) -> str:
    direct_text = clean_cv_bullet_text(payload.get("output_text"))
    if direct_text:
        return direct_text

    text_parts: List[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text_parts.append(str(content.get("text", "")))
    return clean_cv_bullet_text("\n".join(text_parts))


def clean_cv_bullet_text(value: Any) -> str:
    lines = [line.strip().strip('"') for line in str(value or "").splitlines()]
    for line in lines:
        if not line:
            continue
        cleaned = re.sub(r"^(\d+[\.)]\s*|[-*\u2022]\s*)", "", line).strip()
        if cleaned:
            return compact_text(cleaned)
    return ""


def generate_llm_cv_bullet(
    entry: Dict[str, Any],
    evidence_items: List[Dict[str, Any]],
    config: Dict[str, str],
) -> str:
    body: Dict[str, Any] = {
        "model": config["model"],
        "instructions": CV_BULLET_INSTRUCTIONS,
        "input": build_llm_input(entry, evidence_items),
        "text": {"verbosity": "low"},
    }
    if config.get("reasoning_effort") and model_supports_reasoning(config["model"]):
        body["reasoning"] = {"effort": config["reasoning_effort"]}

    payload = post_openai_response(body, config["api_key"])
    text = extract_openai_text(payload)
    if not text:
        raise OpenAIRequestError("OpenAI did not return any text.")
    return text


def model_supports_reasoning(model: str) -> bool:
    normalized = compact_text(model).lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))


def draft_cv_bullet(conn: sqlite3.Connection, entry_id: int) -> Dict[str, Any]:
    entry = get_entry(conn, entry_id)
    evidence_items = list_evidence_for_entry(conn, entry_id)
    config = read_openai_config()
    if config["api_key"]:
        bullet = generate_llm_cv_bullet(entry, evidence_items, config)
    else:
        bullet = build_cv_bullet(entry)
    return update_entry(conn, entry_id, {"cv_bullet_draft": bullet})


def item_matches_project(item: Dict[str, Any], project: Dict[str, Any]) -> bool:
    project_id = str(project.get("id", ""))
    project_name = compact_text(project.get("name")).lower()
    return (
        compact_text(item.get("project_id")) == project_id
        or compact_text(item.get("project")).lower() == project_name
    )


def project_context(
    conn: sqlite3.Connection, project: Dict[str, Any]
) -> Dict[str, Any]:
    tasks = [task for task in list_tasks(conn, {}) if item_matches_project(task, project)]
    entries = [entry for entry in list_entries(conn) if item_matches_project(entry, project)]
    achievements = [
        item
        for item in list_achievements(conn)
        if compact_text(item.get("project")).lower() == compact_text(project.get("name")).lower()
    ]
    entry_ids = {str(entry["id"]) for entry in entries}
    evidence = [
        item
        for item in list_evidence(conn)
        if str(item.get("work_entry_id")) in entry_ids
    ]
    return {
        "open_tasks": [task for task in tasks if not task["completed"]][:12],
        "completed_tasks": [task for task in tasks if task["completed"]][:8],
        "cv_notes": entries[:8],
        "achievements": achievements[:8],
        "evidence": evidence[:8],
    }


def short_task_title(value: Any) -> str:
    title = compact_text(value)
    if len(title) <= 72:
        return title or "Plan next project step"
    words = title.split()
    shortened = ""
    for word in words:
        candidate = f"{shortened} {word}".strip()
        if len(candidate) > 72:
            break
        shortened = candidate
    return shortened or title[:72].rstrip()


def clean_project_suggestion(item: Dict[str, Any]) -> Optional[Dict[str, str]]:
    title = short_task_title(item.get("title"))
    guidance = compact_text(item.get("guidance") or item.get("notes") or item.get("description"))
    if not guidance:
        guidance = f"Work on {title.lower()} and record what changed afterwards."
    if len(guidance) > 420:
        guidance = guidance[:417].rstrip() + "..."
    if not title:
        return None
    return {
        "title": title,
        "guidance": guidance,
        "notes": guidance,
    }


def fallback_project_suggestions(
    project: Dict[str, Any], context: Optional[Dict[str, Any]] = None
) -> List[Dict[str, str]]:
    context = context or {}
    goal = compact_text(project.get("goal"))
    deadline = compact_text(project.get("deadline"))
    open_tasks = context.get("open_tasks") or []
    completed_tasks = context.get("completed_tasks") or []
    suggestions = [
        {
            "title": "Define the next project milestone",
            "guidance": (
                f"Write the next concrete milestone for {project['name']}"
                + (f" based on the goal: {goal}." if goal else ".")
            ),
        },
        {
            "title": "Break the project into small tasks",
            "guidance": (
                "List the smallest useful actions you can finish next, then add only the first one or two to Planner."
            ),
        },
        {
            "title": "Capture project evidence",
            "guidance": (
                "Save a screenshot, file, or note that proves what changed so the CV log has useful detail later."
            ),
        },
    ]
    if deadline:
        suggestions.insert(
            0,
            {
                "title": "Review the project deadline plan",
                "guidance": f"Check what must happen before {deadline} and add the next unblocker to Planner.",
            },
        )
    if open_tasks:
        suggestions.append(
            {
                "title": "Review open project tasks",
                "guidance": f"Look at the {len(open_tasks)} open project task{'s' if len(open_tasks) != 1 else ''} and choose the most urgent next action.",
            }
        )
    if completed_tasks:
        suggestions.append(
            {
                "title": "Turn recent progress into a CV note",
                "guidance": "Summarise the completed project work while it is fresh so achievements stay accurate.",
            }
        )
    cleaned: List[Dict[str, str]] = []
    seen = set()
    for item in suggestions:
        suggestion = clean_project_suggestion(item)
        if not suggestion:
            continue
        key = suggestion["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(suggestion)
        if len(cleaned) == MAX_PROJECT_SUGGESTIONS:
            break
    return cleaned


PROJECT_SUGGESTION_INSTRUCTIONS = """
You help plan one private work project.
Return JSON only: an array of 1 to 3 objects.
Each object must have "title" and "guidance".
Titles must be short task names suitable for a planner.
Guidance should be one or two concise sentences explaining the next step.
Do not invent dates, completed work, employers, metrics, or external facts.
Suggested tasks must not include dates or times.
Prefer UK English spelling.
""".strip()


def build_project_suggestion_llm_input(
    project: Dict[str, Any], context: Dict[str, Any]
) -> str:
    payload = {
        "task": "Suggest next planner tasks for this project.",
        "project": {
            "name": project["name"],
            "goal": project["goal"],
            "deadline": project["deadline"],
            "status": project["status"],
            "notes": project["notes"],
        },
        "context": {
            "open_tasks": [
                {"title": task["title"], "due_date": task["due_date"], "notes": task["notes"]}
                for task in context.get("open_tasks", [])[:8]
            ],
            "completed_tasks": [
                {"title": task["title"], "completed_at": task["completed_at"]}
                for task in context.get("completed_tasks", [])[:5]
            ],
            "cv_notes": [
                {"title": entry["title"], "date": entry["entry_date"], "summary": entry["what_i_did"]}
                for entry in context.get("cv_notes", [])[:5]
            ],
            "achievements": [
                {"date": item["achieved_at"], "bullet": item["bullet"]}
                for item in context.get("achievements", [])[:5]
            ],
            "evidence": [
                {"title": item["title"], "type": item["evidence_type_label"], "description": item["description"]}
                for item in context.get("evidence", [])[:5]
            ],
        },
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def extract_openai_output_text(payload: Dict[str, Any]) -> str:
    direct = compact_text(payload.get("output_text"))
    if direct:
        return direct
    text_parts: List[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text_parts.append(str(content.get("text", "")))
    return compact_text("\n".join(text_parts))


def extract_project_suggestions_from_payload(
    payload: Dict[str, Any]
) -> List[Dict[str, str]]:
    text = extract_openai_output_text(payload)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OpenAIRequestError("OpenAI returned non-JSON project suggestions.") from exc
    if not isinstance(parsed, list):
        raise OpenAIRequestError("OpenAI project suggestions must be a JSON array.")
    suggestions: List[Dict[str, str]] = []
    seen = set()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        suggestion = clean_project_suggestion(item)
        if not suggestion:
            continue
        key = suggestion["title"].lower()
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(suggestion)
        if len(suggestions) == MAX_PROJECT_SUGGESTIONS:
            break
    if not suggestions:
        raise OpenAIRequestError("OpenAI returned no usable project suggestions.")
    return suggestions


def generate_llm_project_suggestions(
    project: Dict[str, Any], context: Dict[str, Any], config: Dict[str, str]
) -> List[Dict[str, str]]:
    body: Dict[str, Any] = {
        "model": config["model"],
        "instructions": PROJECT_SUGGESTION_INSTRUCTIONS,
        "input": build_project_suggestion_llm_input(project, context),
        "text": {"verbosity": "low"},
    }
    if config.get("reasoning_effort") and model_supports_reasoning(config["model"]):
        body["reasoning"] = {"effort": config["reasoning_effort"]}
    payload = post_openai_response(body, config["api_key"])
    return extract_project_suggestions_from_payload(payload)


def suggest_project_next_steps(
    conn: sqlite3.Connection, project_id: Any
) -> List[Dict[str, str]]:
    project = get_project(conn, project_id)
    context = project_context(conn, project)
    config = read_openai_config()
    if config.get("api_key"):
        try:
            return generate_llm_project_suggestions(project, context, config)
        except Exception:
            return fallback_project_suggestions(project, context)
    return fallback_project_suggestions(project, context)


def get_options(conn: sqlite3.Connection) -> Dict[str, Any]:
    entries = list_entries(conn)
    tasks = list_tasks(conn, {})
    projects = list_projects(conn)
    return build_options_payload(entries, tasks, projects)


def build_options_payload(
    entries: Iterable[Dict[str, Any]],
    tasks: Iterable[Dict[str, Any]],
    projects: Optional[Iterable[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    entry_list = list(entries)
    task_list = list(tasks)
    project_list = list(projects or [])
    projects = sorted(
        {
            *{project["name"] for project in project_list if project.get("name")},
            *{entry["project"] for entry in entry_list if entry["project"]},
            *{task["project"] for task in task_list if task["project"]},
        }
    )
    skills = sorted({skill for entry in entry_list for skill in entry["skills_used"]})
    tags = sorted({tag for entry in entry_list for tag in entry["tags"]})
    return {
        "projects": projects,
        "skills": skills,
        "tags": tags,
        "difficulty_levels": DIFFICULTY_LEVELS,
        "evidence_types": [
            {"value": value, "label": label}
            for value, label in EVIDENCE_TYPES.items()
        ],
    }


def get_bootstrap(conn: sqlite3.Connection) -> Dict[str, Any]:
    ensure_projects_from_existing_labels(conn)
    try:
        retry_google_sync(conn, include_all=False)
    except Exception:
        pass
    tasks = list_tasks(conn, {})
    entries = list_entries(conn)
    evidence = list_evidence(conn)
    achievements = list_achievements(conn)
    projects = list_projects(conn)
    return {
        "tasks": tasks,
        "entries": entries,
        "evidence": evidence,
        "achievements": achievements,
        "projects": projects,
        "options": build_options_payload(entries, tasks, projects),
        "google_integration": google_status(conn),
    }


def response_body(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=True).encode("utf-8")


class WorkDiaryHandler(BaseHTTPRequestHandler):
    server_version = "WorkDiary/0.1"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_common_headers()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if self.redirect_authenticated_login(parsed.path):
            return
        if not self.authorize_request(parsed.path):
            return
        if parsed.path.startswith("/api/"):
            self.handle_api("GET", parsed.path, parse_qs(parsed.query))
            return
        self.serve_static(parsed.path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self.authorize_request(parsed.path):
            return
        self.handle_api("POST", parsed.path, parse_qs(parsed.query))

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if not self.authorize_request(parsed.path):
            return
        self.handle_api("PUT", parsed.path, parse_qs(parsed.query))

    def do_DELETE(self) -> None:
        parsed = urlparse(self.path)
        if not self.authorize_request(parsed.path):
            return
        self.handle_api("DELETE", parsed.path, parse_qs(parsed.query))

    def redirect_authenticated_login(self, path: str) -> bool:
        if path in {"/login", "/login.html"} and self.is_authenticated_request():
            self.redirect("/")
            return True
        return False

    def authorize_request(self, path: str) -> bool:
        if not path_requires_auth(path):
            return True
        if self.is_authenticated_request():
            return True
        if path.startswith("/api/"):
            self.send_json({"error": "Login required."}, status=401)
        else:
            self.redirect("/login.html")
        return False

    def is_authenticated_request(self) -> bool:
        return is_authenticated_cookie_header(self.headers.get("Cookie", ""))

    def handle_api(
        self, method: str, path: str, query: Dict[str, List[str]]
    ) -> None:
        try:
            payload = self.read_json_body() if method in {"POST", "PUT"} else {}
            with get_connection() as conn:
                init_db(conn)
                result = self.route_api(method, path, query, payload, conn)
            if isinstance(result, dict) and "_html" in result:
                self.send_html(result["_html"])
                return
            if method == "DELETE":
                self.send_json({"ok": True})
            else:
                self.send_json(result)
        except ValidationError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except NotFoundError as exc:
            self.send_json({"error": str(exc).strip("'")}, status=404)
        except OpenAIRequestError as exc:
            self.send_json({"error": str(exc)}, status=502)
        except GoogleIntegrationError as exc:
            self.send_json({"error": str(exc)}, status=502)
        except json.JSONDecodeError:
            self.send_json({"error": "Request body must be valid JSON."}, status=400)
        except Exception as exc:  # pragma: no cover - last-resort local dev guard.
            self.log_error("Unhandled API error: %s", exc)
            self.send_json({"error": "Unexpected server error."}, status=500)

    def route_api(
        self,
        method: str,
        path: str,
        query: Dict[str, List[str]],
        payload: Dict[str, Any],
        conn: sqlite3.Connection,
    ) -> Any:
        entry_match = re.match(r"^/api/entries/(\d+)$", path)
        bullet_match = re.match(r"^/api/entries/(\d+)/draft-bullet$", path)
        evidence_match = re.match(r"^/api/evidence/(\d+)$", path)
        task_match = re.match(r"^/api/tasks/(\d+)$", path)
        project_match = re.match(r"^/api/projects/(\d+)$", path)
        project_complete_match = re.match(r"^/api/projects/(\d+)/complete$", path)
        project_reorder_match = re.match(r"^/api/projects/(\d+)/tasks/reorder$", path)
        project_suggestions_match = re.match(
            r"^/api/projects/(\d+)/suggestions$", path
        )

        if method == "POST" and path == "/api/login":
            config = read_auth_config()
            if not auth_is_configured(config):
                raise ValidationError("Set APP_PASSWORD and SESSION_SECRET in .env first.")
            if not password_matches(payload.get("password"), config):
                raise ValidationError("Incorrect password.")
            return {
                "ok": True,
                "_headers": {"Set-Cookie": build_session_cookie(config)},
            }
        if method == "POST" and path == "/api/logout":
            return {
                "ok": True,
                "_headers": {"Set-Cookie": build_clear_session_cookie()},
            }
        if method == "GET" and path == "/api/health":
            return {"ok": True}
        if method == "GET" and path == "/api/bootstrap":
            return get_bootstrap(conn)
        if method == "GET" and path == "/api/options":
            return get_options(conn)
        if method == "GET" and path == "/api/integrations/google/status":
            return google_status(conn)
        if method == "POST" and path == "/api/integrations/google/connect":
            return start_google_connect(conn, payload)
        if method == "GET" and path == "/api/integrations/google/callback":
            return complete_google_connect(conn, flatten_query(query))
        if method == "POST" and path == "/api/integrations/google/retry":
            result = retry_google_sync(conn, include_all=True)
            return {**result, "status": google_status(conn)}
        if method == "POST" and path == "/api/integrations/google/disconnect":
            return disconnect_google(conn)
        if method == "GET" and path == "/api/achievements":
            return list_achievements(conn, flatten_query(query))
        if method == "GET" and path == "/api/projects":
            return list_projects(conn)
        if method == "POST" and path == "/api/projects":
            return create_project(conn, payload)
        if method == "POST" and project_complete_match:
            return complete_project(conn, int(project_complete_match.group(1)))
        if method == "POST" and project_reorder_match:
            return reorder_project_tasks(conn, int(project_reorder_match.group(1)), payload)
        if method == "GET" and project_match:
            return get_project(conn, int(project_match.group(1)))
        if method == "PUT" and project_match:
            return update_project(conn, int(project_match.group(1)), payload)
        if method == "DELETE" and project_match:
            delete_project(conn, int(project_match.group(1)))
            return {"ok": True}
        if method == "POST" and project_suggestions_match:
            return suggest_project_next_steps(conn, int(project_suggestions_match.group(1)))
        if method == "GET" and path == "/api/push/public-key":
            config = read_vapid_config()
            return {
                "publicKey": config["public_key"],
                "configured": False,
                "privateKeyConfigured": bool(config["private_key"]),
                "webpushInstalled": False,
            }
        if method == "GET" and path == "/api/push/status":
            return get_push_status(conn)
        if method == "POST" and path == "/api/push/subscribe":
            return save_push_subscription(conn, payload)
        if method == "POST" and path == "/api/push/unsubscribe":
            return delete_push_subscription(conn, payload)
        if method == "POST" and path == "/api/push/test":
            return send_push_payload(conn, build_test_push_payload())
        if method == "POST" and path == "/api/reminders/sync":
            return sync_task_reminder_schedules(conn)
        if method == "GET" and path == "/api/tasks":
            return list_tasks(conn, flatten_query(query))
        if method == "POST" and path == "/api/tasks":
            return create_task(conn, payload)
        if method == "GET" and task_match:
            return get_task(conn, int(task_match.group(1)))
        if method == "PUT" and task_match:
            return update_task(conn, int(task_match.group(1)), payload)
        if method == "DELETE" and task_match:
            delete_task(conn, int(task_match.group(1)))
            return {"ok": True}
        if method == "GET" and path == "/api/entries":
            return list_entries(conn, flatten_query(query))
        if method == "POST" and path == "/api/entries":
            return create_entry(conn, payload)
        if method == "POST" and path == "/api/quick-logs":
            return create_quick_log(conn, payload)
        if method == "POST" and path == "/api/image-evidence":
            return create_image_evidence(conn, payload)
        if method == "GET" and entry_match:
            return get_entry(conn, int(entry_match.group(1)))
        if method == "PUT" and entry_match:
            return update_entry(conn, int(entry_match.group(1)), payload)
        if method == "DELETE" and entry_match:
            delete_entry(conn, int(entry_match.group(1)))
            return {"ok": True}
        if method == "POST" and bullet_match:
            return draft_cv_bullet(conn, int(bullet_match.group(1)))
        if method == "GET" and path == "/api/evidence":
            return list_evidence(conn, flatten_query(query))
        if method == "POST" and path == "/api/evidence":
            return create_evidence(conn, payload)
        if method == "PUT" and evidence_match:
            return update_evidence(conn, int(evidence_match.group(1)), payload)
        if method == "DELETE" and evidence_match:
            delete_evidence(conn, int(evidence_match.group(1)))
            return {"ok": True}
        raise NotFoundError("Route not found.")

    def read_json_body(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValidationError("Request body must be a JSON object.")
        return data

    def send_json(self, payload: Any, status: int = 200) -> None:
        extra_headers = {}
        if isinstance(payload, dict):
            extra_headers = payload.pop("_headers", {})
        body = response_body(payload)
        self.send_response(status)
        self.send_common_headers(content_type="application/json")
        for name, value in extra_headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_common_headers(content_type="text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_common_headers(self, content_type: str = "application/json") -> None:
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Content-Type-Options", "nosniff")

    def redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

    def serve_static(self, path: str) -> None:
        if path in {"", "/"}:
            file_path = STATIC_DIR / "index.html"
        elif path in {"/login", "/login.html"}:
            file_path = STATIC_DIR / "login.html"
        elif path in {
            "/manifest.webmanifest",
            "/service-worker.js",
            "/favicon.svg",
            "/apple-touch-icon.svg",
            "/apple-touch-icon.png",
        }:
            file_path = STATIC_DIR / path.removeprefix("/")
        elif path.startswith("/static/"):
            requested = path.removeprefix("/static/")
            file_path = (STATIC_DIR / requested).resolve()
            if STATIC_DIR.resolve() not in file_path.parents:
                self.send_error(404)
                return
        elif path.startswith("/uploads/"):
            requested = path.removeprefix("/uploads/")
            file_path = (UPLOADS_DIR / requested).resolve()
            if UPLOADS_DIR.resolve() not in file_path.parents:
                self.send_error(404)
                return
        else:
            self.send_error(404)
            return

        if not file_path.exists() or not file_path.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "text/plain"
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_common_headers(content_type=content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Iterable[Any]) -> None:
        return


def flatten_query(query: Dict[str, List[str]]) -> Dict[str, str]:
    return {key: values[-1] for key, values in query.items() if values}


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    with get_connection() as conn:
        init_db(conn)
    server = ThreadingHTTPServer((host, port), WorkDiaryHandler)
    print(f"Work diary running at http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    run()
