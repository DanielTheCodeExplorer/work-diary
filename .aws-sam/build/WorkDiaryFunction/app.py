#!/usr/bin/env python3
"""Local work diary MVP.

The app intentionally uses only Python's standard library so the MVP can run
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
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "work_diary.sqlite3"
UPLOADS_DIR = DATA_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
ENV_PATH = BASE_DIR / ".env"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.4-nano"
MAX_IMAGE_UPLOAD_BYTES = 3 * 1024 * 1024
SESSION_COOKIE_NAME = "work_diary_session"
DEFAULT_SESSION_SECONDS = 60 * 60 * 24 * 30
PUBLIC_API_PATHS = {"/api/login", "/api/logout"}
PUBLIC_PAGE_PATHS = {
    "/login",
    "/login.html",
    "/manifest.webmanifest",
    "/service-worker.js",
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


class ValidationError(ValueError):
    """Raised when API input is syntactically valid but incomplete."""


class OpenAIRequestError(RuntimeError):
    """Raised when the OpenAI API cannot return a usable draft."""


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
            project TEXT DEFAULT '',
            due_date TEXT DEFAULT '',
            due_time TEXT DEFAULT '',
            reminder_at TEXT DEFAULT '',
            repeat_rule TEXT NOT NULL DEFAULT 'none',
            repeat_interval_days INTEGER NOT NULL DEFAULT 1,
            repeat_until TEXT DEFAULT '',
            priority TEXT DEFAULT '',
            location TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            completed INTEGER NOT NULL DEFAULT 0,
            completed_at TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (completed IN (0, 1))
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
        """
    )
    ensure_task_columns(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority)")
    conn.commit()


def ensure_task_columns(conn: sqlite3.Connection) -> None:
    existing = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    columns = {
        "due_time": "TEXT DEFAULT ''",
        "reminder_at": "TEXT DEFAULT ''",
        "repeat_rule": "TEXT NOT NULL DEFAULT 'none'",
        "repeat_interval_days": "INTEGER NOT NULL DEFAULT 1",
        "repeat_until": "TEXT DEFAULT ''",
        "priority": "TEXT DEFAULT ''",
        "location": "TEXT DEFAULT ''",
        "notes": "TEXT DEFAULT ''",
    }
    for name, definition in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")


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


def required_text(data: Dict[str, Any], field: str, label: str) -> str:
    value = compact_text(data.get(field))
    if not value:
        raise ValidationError(f"{label} is required.")
    return value


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
    try:
        dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"{label} must use YYYY-MM-DDTHH:MM format.") from exc
    return text


def validate_task_priority(value: Any) -> str:
    priority = compact_text(value).lower()
    if priority not in TASK_PRIORITIES:
        raise ValidationError("Priority must be low, medium, or high.")
    return priority


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


def row_to_entry(row: sqlite3.Row, evidence_count: int = 0) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "entry_date": row["entry_date"],
        "title": row["title"],
        "what_i_did": row["what_i_did"],
        "quick_note": row["quick_note"],
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
        "project": row["project"],
        "due_date": row["due_date"],
        "due_time": row["due_time"],
        "reminder_at": row["reminder_at"],
        "repeat_rule": row["repeat_rule"],
        "repeat_interval_days": row["repeat_interval_days"],
        "repeat_until": row["repeat_until"],
        "priority": row["priority"],
        "location": row["location"],
        "notes": row["notes"],
        "completed": bool(row["completed"]),
        "completed_at": row["completed_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_task(conn: sqlite3.Connection, data: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO tasks (
            title, project, due_date, due_time, reminder_at, repeat_rule,
            repeat_interval_days, repeat_until, priority,
            location, notes, completed, completed_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            required_text(data, "title", "Task title"),
            compact_text(data.get("project")),
            validate_optional_date(data.get("due_date"), "Due date"),
            validate_optional_time(data.get("due_time"), "Due time"),
            validate_optional_datetime(data.get("reminder_at"), "Reminder"),
            validate_repeat_rule(data.get("repeat_rule")),
            validate_repeat_interval_days(data.get("repeat_interval_days")),
            validate_optional_date(data.get("repeat_until"), "Repeat stop date"),
            validate_task_priority(data.get("priority")),
            compact_text(data.get("location")),
            compact_text(data.get("notes")),
            1 if bool(data.get("completed")) else 0,
            timestamp if bool(data.get("completed")) else "",
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    return get_task(conn, int(cursor.lastrowid))


def get_task(conn: sqlite3.Connection, task_id: int) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise KeyError("Task not found.")
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
            CASE WHEN due_date = '' THEN 1 ELSE 0 END ASC,
            due_date ASC,
            due_time ASC,
            created_at DESC
        """
    ).fetchall()
    tasks = [row_to_task(row) for row in rows]
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
    completed = bool(merged.get("completed"))
    completed_at = current["completed_at"]
    if completed and not current["completed"]:
        completed_at = timestamp
    if not completed:
        completed_at = ""

    conn.execute(
        """
        UPDATE tasks
        SET title = ?,
            project = ?,
            due_date = ?,
            due_time = ?,
            reminder_at = ?,
            repeat_rule = ?,
            repeat_interval_days = ?,
            repeat_until = ?,
            priority = ?,
            location = ?,
            notes = ?,
            completed = ?,
            completed_at = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            required_text(merged, "title", "Task title"),
            compact_text(merged.get("project")),
            validate_optional_date(merged.get("due_date"), "Due date"),
            validate_optional_time(merged.get("due_time"), "Due time"),
            validate_optional_datetime(merged.get("reminder_at"), "Reminder"),
            validate_repeat_rule(merged.get("repeat_rule")),
            validate_repeat_interval_days(merged.get("repeat_interval_days")),
            validate_optional_date(merged.get("repeat_until"), "Repeat stop date"),
            validate_task_priority(merged.get("priority")),
            compact_text(merged.get("location")),
            compact_text(merged.get("notes")),
            1 if completed else 0,
            completed_at,
            timestamp,
            task_id,
        ),
    )
    conn.commit()
    if completed and not current["completed"] and validate_repeat_rule(merged.get("repeat_rule")) != "none":
        create_next_repeating_task(conn, {**merged, "completed": False})
    return get_task(conn, task_id)


def create_next_repeating_task(conn: sqlite3.Connection, task: Dict[str, Any]) -> None:
    repeat_rule = validate_repeat_rule(task.get("repeat_rule"))
    if repeat_rule == "none":
        return

    base_date = validate_optional_date(task.get("due_date"), "Due date")
    if base_date:
        next_due_date = next_repeat_date(base_date, repeat_rule, task.get("repeat_interval_days"))
    else:
        next_due_date = next_repeat_date(today_iso(), repeat_rule, task.get("repeat_interval_days"))

    repeat_until = validate_optional_date(task.get("repeat_until"), "Repeat stop date")
    if repeat_until and next_due_date > repeat_until:
        return

    next_reminder = next_repeat_datetime(
        task.get("reminder_at"),
        repeat_rule,
        base_date or today_iso(),
        next_due_date,
    )

    create_task(
        conn,
        {
            "title": task.get("title"),
            "project": task.get("project"),
            "due_date": next_due_date,
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
    cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    if cursor.rowcount == 0:
        raise KeyError("Task not found.")


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

    cursor = conn.execute(
        """
        INSERT INTO work_entries (
            entry_date, title, what_i_did, quick_note, project, skills_used,
            outcome, reflection_notes, tags, difficulty, source_mode,
            cv_bullet_draft, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            entry_date,
            title,
            what_i_did,
            compact_text(data.get("quick_note")),
            compact_text(data.get("project")),
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
        raise KeyError("Work entry not found.")
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

    conn.execute(
        """
        UPDATE work_entries
        SET entry_date = ?,
            title = ?,
            what_i_did = ?,
            quick_note = ?,
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
            compact_text(merged.get("project")),
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
        raise KeyError("Work entry not found.")


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
        raise KeyError("Evidence not found.")
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
        raise KeyError("Evidence not found.")


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
        if len(bullets) == 3:
            break
    return bullets or ["Documented work completed."]


ACHIEVEMENT_INSTRUCTIONS = """
You turn private work diary notes into achievement bullets for a career log.
Output 1 to 3 concise bullet sentences with no markdown.
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
            "Return 1 to 3 bullet sentences.",
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
        if len(bullets) == 3:
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


def get_options(conn: sqlite3.Connection) -> Dict[str, Any]:
    entries = list_entries(conn)
    tasks = list_tasks(conn, {})
    return build_options_payload(entries, tasks)


def build_options_payload(
    entries: Iterable[Dict[str, Any]], tasks: Iterable[Dict[str, Any]]
) -> Dict[str, Any]:
    entry_list = list(entries)
    task_list = list(tasks)
    projects = sorted(
        {
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
    tasks = list_tasks(conn, {})
    entries = list_entries(conn)
    evidence = list_evidence(conn)
    achievements = list_achievements(conn)
    return {
        "tasks": tasks,
        "entries": entries,
        "evidence": evidence,
        "achievements": achievements,
        "options": build_options_payload(entries, tasks),
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
            if method == "DELETE":
                self.send_json({"ok": True})
            else:
                self.send_json(result)
        except ValidationError as exc:
            self.send_json({"error": str(exc)}, status=400)
        except KeyError as exc:
            self.send_json({"error": str(exc).strip("'")}, status=404)
        except OpenAIRequestError as exc:
            self.send_json({"error": str(exc)}, status=502)
        except json.JSONDecodeError:
            self.send_json({"error": "Request body must be valid JSON."}, status=400)
        except Exception as exc:  # pragma: no cover - last-resort local dev guard.
            self.send_json({"error": f"Unexpected error: {exc}"}, status=500)

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
        if method == "GET" and path == "/api/achievements":
            return list_achievements(conn, flatten_query(query))
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
        raise KeyError("Route not found.")

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

    def send_common_headers(self, content_type: str = "application/json") -> None:
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS"
        )
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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
