import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs
from urllib.request import Request, urlopen

import boto3

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.4-nano"
MAX_IMAGE_UPLOAD_BYTES = 3 * 1024 * 1024
SESSION_COOKIE_NAME = "work_diary_session"
PUBLIC_API_PATHS = {"/api/login", "/api/logout", "/api/health"}
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
TASK_REPEAT_RULES = ["none", "daily", "weekly", "monthly", "interval"]
TASK_PRIORITIES = ["", "low", "medium", "high"]

DYNAMODB = boto3.resource("dynamodb")
S3_CLIENT = boto3.client("s3")
TASKS_TABLE = os.environ.get("TASKS_TABLE", "WorkDiaryTasks")
ENTRIES_TABLE = os.environ.get("ENTRIES_TABLE", "WorkDiaryEntries")
EVIDENCE_TABLE = os.environ.get("EVIDENCE_TABLE", "WorkDiaryEvidence")
ACHIEVEMENTS_TABLE = os.environ.get("ACHIEVEMENTS_TABLE", "WorkDiaryAchievements")
UPLOADS_BUCKET = os.environ.get("UPLOADS_BUCKET", "")
UPLOADS_PREFIX = os.environ.get("UPLOADS_PREFIX", "uploads/")

tasks_table = DYNAMODB.Table(TASKS_TABLE)
entries_table = DYNAMODB.Table(ENTRIES_TABLE)
evidence_table = DYNAMODB.Table(EVIDENCE_TABLE)
achievements_table = DYNAMODB.Table(ACHIEVEMENTS_TABLE)


def now_iso() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


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


def json_list(value: Any) -> List[str]:
    return normalize_list(value)


def parse_json_object(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def required_text(data: Dict[str, Any], field: str, label: str) -> str:
    value = compact_text(data.get(field))
    if not value:
        raise ValidationError(f"{label} is required.")
    return value


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
        raise ValidationError("Difficulty must be easy, medium, hard, or stretch.")
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


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def sign_session_payload(payload: str, secret: str) -> str:
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).digest()
    return b64url_encode(signature)


def make_session_token(secret: str, max_age_seconds: int = 2592000, now: Optional[int] = None) -> str:
    issued_at = int(now or time.time())
    payload = b64url_encode(
        json.dumps({"iat": issued_at, "exp": issued_at + max_age_seconds}).encode("utf-8")
    )
    return f"{payload}.{sign_session_payload(payload, secret)}"


def validate_session_token(token: str, secret: str, now: Optional[int] = None) -> bool:
    try:
        payload, signature = token.split(".", 1)
    except ValueError:
        return False
    if not secrets.compare_digest(signature, sign_session_payload(payload, secret)):
        return False
    try:
        claims = json.loads(b64url_decode(payload).decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return False
    expires_at = claims.get("exp")
    if not isinstance(expires_at, int):
        return False
    return expires_at >= int(now or time.time())


def get_config_value(name: str, default: str = "") -> str:
    return compact_text(os.environ.get(name, default))


def read_openai_config() -> Dict[str, str]:
    api_key = get_config_value("OPENAI_API_KEY")
    if api_key in {"put_your_api_key_here", "sk-your-key-here"}:
        api_key = ""
    return {
        "api_key": api_key,
        "model": get_config_value("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        "reasoning_effort": get_config_value("OPENAI_REASONING_EFFORT", "low"),
    }


def auth_is_configured() -> bool:
    return bool(get_config_value("APP_PASSWORD") and get_config_value("SESSION_SECRET"))


def password_matches(submitted: Any) -> bool:
    password = get_config_value("APP_PASSWORD")
    if not password:
        return False
    return secrets.compare_digest(str(submitted or ""), password)


def get_authorization_token(headers: Dict[str, str]) -> str:
    auth_header = headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return ""


def is_authenticated(headers: Dict[str, str]) -> bool:
    token = get_authorization_token(headers)
    if not token:
        return False
    secret = get_config_value("SESSION_SECRET")
    if not secret:
        return False
    return validate_session_token(token, secret)


def response(status: int, payload: Any = None, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    headers = {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    }
    if extra_headers:
        headers.update(extra_headers)
    body = json.dumps({} if payload is None else payload).encode("utf-8") if payload is not None else b""
    return {"statusCode": status, "headers": headers, "body": body.decode("utf-8")}


def parse_json_body(event: Dict[str, Any]) -> Dict[str, Any]:
    body = event.get("body")
    if not body:
        return {}
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        raise ValidationError("Request body must be valid JSON.")
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object.")
    return data


def parse_query(event: Dict[str, Any]) -> Dict[str, str]:
    query_string = event.get("rawQueryString", "") or ""
    return {key: values[-1] for key, values in parse_qs(query_string).items()}


def scan_table(table):
    response = table.scan()
    items = response.get("Items", [])
    while response.get("LastEvaluatedKey"):
        response = table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        items.extend(response.get("Items", []))
    return items


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = compact_text(value).lower()
    return text not in {"", "0", "false", "no", "none"}


def parse_iso_datetime(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def task_sort_key(task: Dict[str, Any]) -> Any:
    due_date = task.get("due_date") or "9999-12-31"
    due_time = task.get("due_time") or "23:59"
    created_at = task.get("created_at") or ""
    try:
        created_at_ts = -dt.datetime.fromisoformat(created_at).timestamp()
    except Exception:
        created_at_ts = 0
    return (
        1 if task.get("completed") else 0,
        1 if not task.get("due_date") else 0,
        due_date,
        due_time,
        created_at_ts,
    )


def entry_sort_key(entry: Dict[str, Any]) -> Any:
    return (entry.get("entry_date") or "", entry.get("created_at") or "")


def evidence_sort_key(item: Dict[str, Any]) -> Any:
    return (item.get("entry_date") or "", item.get("created_at") or "")


def row_to_task(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "title": item.get("title", ""),
        "project": item.get("project", ""),
        "due_date": item.get("due_date", ""),
        "due_time": item.get("due_time", ""),
        "reminder_at": item.get("reminder_at", ""),
        "repeat_rule": item.get("repeat_rule", "none"),
        "repeat_interval_days": int(item.get("repeat_interval_days") or 1),
        "repeat_until": item.get("repeat_until", ""),
        "priority": item.get("priority", ""),
        "location": item.get("location", ""),
        "notes": item.get("notes", ""),
        "completed": to_bool(item.get("completed")),
        "completed_at": item.get("completed_at", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


def row_to_entry(item: Dict[str, Any], evidence_count: int = 0) -> Dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "entry_date": item.get("entry_date", ""),
        "title": item.get("title", ""),
        "what_i_did": item.get("what_i_did", ""),
        "quick_note": item.get("quick_note", ""),
        "project": item.get("project", ""),
        "skills_used": normalize_list(item.get("skills_used")),
        "outcome": item.get("outcome", ""),
        "reflection_notes": item.get("reflection_notes", ""),
        "tags": normalize_list(item.get("tags")),
        "difficulty": item.get("difficulty", ""),
        "source_mode": item.get("source_mode", "detailed"),
        "cv_bullet_draft": item.get("cv_bullet_draft", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
        "evidence_count": evidence_count,
    }


def row_to_evidence(item: Dict[str, Any]) -> Dict[str, Any]:
    evidence_type = item.get("evidence_type", "")
    return {
        "id": item.get("id", ""),
        "work_entry_id": item.get("work_entry_id", ""),
        "title": item.get("title", ""),
        "evidence_type": evidence_type,
        "evidence_type_label": EVIDENCE_TYPES.get(evidence_type, evidence_type),
        "evidence_url": item.get("evidence_url", ""),
        "description": item.get("description", ""),
        "provider": item.get("provider", ""),
        "provider_metadata": parse_json_object(item.get("provider_metadata")),
        "storage_key": item.get("storage_key", ""),
        "attachment_status": item.get("attachment_status", "linked"),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


def row_to_achievement(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "source_entry_id": item.get("source_entry_id", ""),
        "achieved_at": item.get("achieved_at", ""),
        "bullet": item.get("bullet", ""),
        "project": item.get("project", ""),
        "skills_used": normalize_list(item.get("skills_used")),
        "tags": normalize_list(item.get("tags")),
        "source": item.get("source", "auto"),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


def get_task(task_id: str) -> Dict[str, Any]:
    response = tasks_table.get_item(Key={"id": task_id})
    item = response.get("Item")
    if item is None:
        raise KeyError("Task not found.")
    return row_to_task(item)


def list_tasks(filters: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    filters = filters or {}
    items = [row_to_task(item) for item in scan_table(tasks_table)]
    items.sort(key=task_sort_key)
    return filter_tasks(items, filters)


def filter_tasks(tasks: List[Dict[str, Any]], filters: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    filters = filters or {}
    completed_filter = compact_text(filters.get("completed")).lower()
    if completed_filter in {"true", "1", "yes"}:
        return [task for task in tasks if task["completed"]]
    if completed_filter in {"false", "0", "no"}:
        return [task for task in tasks if not task["completed"]]
    return tasks


def create_task(data: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_iso()
    item_id = uuid.uuid4().hex
    item = {
        "id": item_id,
        "title": required_text(data, "title", "Task title"),
        "project": compact_text(data.get("project")),
        "due_date": validate_optional_date(data.get("due_date"), "Due date"),
        "due_time": validate_optional_time(data.get("due_time"), "Due time"),
        "reminder_at": validate_optional_datetime(data.get("reminder_at"), "Reminder"),
        "repeat_rule": validate_repeat_rule(data.get("repeat_rule")),
        "repeat_interval_days": validate_repeat_interval_days(data.get("repeat_interval_days")),
        "repeat_until": validate_optional_date(data.get("repeat_until"), "Repeat stop date"),
        "priority": validate_task_priority(data.get("priority")),
        "location": compact_text(data.get("location")),
        "notes": compact_text(data.get("notes")),
        "completed": bool(data.get("completed")),
        "completed_at": timestamp if bool(data.get("completed")) else "",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    tasks_table.put_item(Item=item)
    return get_task(item_id)


def update_task(task_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    current = get_task(task_id)
    merged = {**current, **data}
    timestamp = now_iso()
    completed = bool(merged.get("completed"))
    completed_at = current["completed_at"]
    if completed and not current["completed"]:
        completed_at = timestamp
    if not completed:
        completed_at = ""
    item = {
        "id": task_id,
        "title": required_text(merged, "title", "Task title"),
        "project": compact_text(merged.get("project")),
        "due_date": validate_optional_date(merged.get("due_date"), "Due date"),
        "due_time": validate_optional_time(merged.get("due_time"), "Due time"),
        "reminder_at": validate_optional_datetime(merged.get("reminder_at"), "Reminder"),
        "repeat_rule": validate_repeat_rule(merged.get("repeat_rule")),
        "repeat_interval_days": validate_repeat_interval_days(merged.get("repeat_interval_days")),
        "repeat_until": validate_optional_date(merged.get("repeat_until"), "Repeat stop date"),
        "priority": validate_task_priority(merged.get("priority")),
        "location": compact_text(merged.get("location")),
        "notes": compact_text(merged.get("notes")),
        "completed": completed,
        "completed_at": completed_at,
        "created_at": current["created_at"],
        "updated_at": timestamp,
    }
    tasks_table.put_item(Item=item)
    if completed and not current["completed"] and validate_repeat_rule(merged.get("repeat_rule")) != "none":
        create_next_repeating_task({**merged, "completed": False})
    return get_task(task_id)


def create_next_repeating_task(task: Dict[str, Any]) -> None:
    repeat_rule = validate_repeat_rule(task.get("repeat_rule"))
    if repeat_rule == "none":
        return
    base_date = validate_optional_date(task.get("due_date"), "Due date")
    if base_date:
        next_due_date = next_repeat_date(base_date, repeat_rule, task.get("repeat_interval_days"))
    else:
        next_due_date = next_repeat_date(dt.date.today().isoformat(), repeat_rule, task.get("repeat_interval_days"))
    repeat_until = validate_optional_date(task.get("repeat_until"), "Repeat stop date")
    if repeat_until and next_due_date > repeat_until:
        return
    next_reminder = next_repeat_datetime(
        task.get("reminder_at"),
        repeat_rule,
        base_date or dt.date.today().isoformat(),
        next_due_date,
    )
    create_task(
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
        }
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


def delete_task(task_id: str) -> None:
    response = tasks_table.delete_item(Key={"id": task_id})
    if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200:
        raise KeyError("Task not found.")


def create_entry(data: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_iso()
    item_id = uuid.uuid4().hex
    entry_date = validate_entry_date(data.get("entry_date"))
    title = required_text(data, "title", "Title")
    what_i_did = required_text(data, "what_i_did", "What I did")
    source_mode = validate_source_mode(data.get("source_mode"))
    difficulty = validate_difficulty(data.get("difficulty"))
    item = {
        "id": item_id,
        "entry_date": entry_date,
        "title": title,
        "what_i_did": what_i_did,
        "quick_note": compact_text(data.get("quick_note")),
        "project": compact_text(data.get("project")),
        "skills_used": json_list(data.get("skills_used")),
        "outcome": compact_text(data.get("outcome")),
        "reflection_notes": compact_text(data.get("reflection_notes")),
        "tags": json_list(data.get("tags")),
        "difficulty": difficulty,
        "source_mode": source_mode,
        "cv_bullet_draft": compact_text(data.get("cv_bullet_draft")),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    entries_table.put_item(Item=item)
    entry = get_entry(item_id)
    replace_achievements_for_entry(entry)
    return entry


def create_quick_log(data: Dict[str, Any]) -> Dict[str, Any]:
    note = required_text(data, "note", "Quick log")
    payload = {
        "entry_date": compact_text(data.get("entry_date")) or dt.date.today().isoformat(),
        "title": compact_text(data.get("title")) or infer_title_from_note(note),
        "what_i_did": note,
        "quick_note": note,
        "source_mode": "quick_log",
        "project": data.get("project", ""),
        "skills_used": data.get("skills_used", []),
        "tags": data.get("tags", []),
    }
    return create_entry(payload)


def get_entry(entry_id: str) -> Dict[str, Any]:
    response = entries_table.get_item(Key={"id": entry_id})
    item = response.get("Item")
    if item is None:
        raise KeyError("Work entry not found.")
    count = len([1 for evidence_item in scan_table(evidence_table) if evidence_item.get("work_entry_id") == entry_id])
    return row_to_entry(item, evidence_count=count)


def list_entries(filters: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    filters = filters or {}
    items = scan_table(entries_table)
    evidence_items = scan_table(evidence_table)
    return build_entries_payload(items, evidence_items, filters)


def build_entries_payload(
    entry_items: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    filters: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    filters = filters or {}
    evidence_counts = {}
    for evidence_item in evidence_items:
        work_entry_id = evidence_item.get("work_entry_id", "")
        evidence_counts[work_entry_id] = evidence_counts.get(work_entry_id, 0) + 1
    entries = [
        row_to_entry(entry_item, evidence_count=evidence_counts.get(entry_item.get("id", ""), 0))
        for entry_item in entry_items
    ]
    filtered = [entry for entry in entries if entry_matches(entry, filters)]
    filtered.sort(key=entry_sort_key, reverse=True)
    return filtered


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


def list_achievements(filters: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    filters = filters or {}
    achievements = [row_to_achievement(item) for item in scan_table(achievements_table)]
    filtered = [
        achievement
        for achievement in achievements
        if achievement_matches(achievement, filters)
    ]
    filtered.sort(key=achievement_sort_key, reverse=True)
    return filtered


def achievement_sort_key(item: Dict[str, Any]) -> Any:
    return (item.get("achieved_at") or "", item.get("created_at") or "")


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
    entry: Dict[str, Any], source: str = "auto"
) -> List[Dict[str, Any]]:
    existing = [
        item
        for item in scan_table(achievements_table)
        if item.get("source_entry_id") == entry["id"] and item.get("source", "auto") == source
    ]
    for item in existing:
        achievements_table.delete_item(Key={"id": item["id"]})

    timestamp = now_iso()
    bullets = extract_achievement_bullets(entry)
    for bullet in bullets:
        achievements_table.put_item(
            Item={
                "id": uuid.uuid4().hex,
                "source_entry_id": entry["id"],
                "achieved_at": entry["entry_date"],
                "bullet": bullet,
                "project": entry["project"],
                "skills_used": normalize_list(entry["skills_used"]),
                "tags": normalize_list(entry["tags"]),
                "source": source,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
    return list_achievements({"source_entry_id": entry["id"]})


def update_entry(entry_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    current = get_entry(entry_id)
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
    item = {
        "id": entry_id,
        "entry_date": entry_date,
        "title": title,
        "what_i_did": what_i_did,
        "quick_note": compact_text(merged.get("quick_note")),
        "project": compact_text(merged.get("project")),
        "skills_used": json_list(merged.get("skills_used")),
        "outcome": compact_text(merged.get("outcome")),
        "reflection_notes": compact_text(merged.get("reflection_notes")),
        "tags": json_list(merged.get("tags")),
        "difficulty": difficulty,
        "source_mode": source_mode,
        "cv_bullet_draft": compact_text(merged.get("cv_bullet_draft")),
        "created_at": current["created_at"],
        "updated_at": timestamp,
    }
    entries_table.put_item(Item=item)
    entry = get_entry(entry_id)
    if refresh_achievements:
        replace_achievements_for_entry(entry)
    return entry


def delete_entry(entry_id: str) -> None:
    evidence_items = [item for item in scan_table(evidence_table) if item.get("work_entry_id") == entry_id]
    for evidence_item in evidence_items:
        evidence_table.delete_item(Key={"id": evidence_item["id"]})
    achievement_items = [
        item for item in scan_table(achievements_table) if item.get("source_entry_id") == entry_id
    ]
    for achievement_item in achievement_items:
        achievements_table.delete_item(Key={"id": achievement_item["id"]})
    response = entries_table.delete_item(Key={"id": entry_id})
    if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200:
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


def create_evidence(data: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_iso()
    work_entry_id = compact_text(data.get("work_entry_id"))
    if not work_entry_id:
        raise ValidationError("Related work entry ID is required.")
    get_entry(work_entry_id)
    evidence_type = validate_evidence_type(data.get("evidence_type"))
    evidence_url = validate_url(data.get("evidence_url"), evidence_type)
    provider_metadata = parse_json_object(data.get("provider_metadata"))
    item_id = uuid.uuid4().hex
    item = {
        "id": item_id,
        "work_entry_id": work_entry_id,
        "title": required_text(data, "title", "Evidence title"),
        "evidence_type": evidence_type,
        "evidence_url": evidence_url,
        "description": compact_text(data.get("description")),
        "provider": compact_text(data.get("provider")),
        "provider_metadata": provider_metadata,
        "storage_key": compact_text(data.get("storage_key")),
        "attachment_status": compact_text(data.get("attachment_status")) or "linked",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    evidence_table.put_item(Item=item)
    return get_evidence(item_id)


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


def create_image_evidence(data: Dict[str, Any]) -> Dict[str, Any]:
    if not UPLOADS_BUCKET:
        raise ValidationError("Image uploads bucket is not configured.")
    raw_image, content_type = decode_image_data_url(data.get("data_url"))
    filename = safe_upload_filename(data.get("filename"), content_type)
    comment = compact_text(data.get("comment"))
    work_entry_id = compact_text(data.get("work_entry_id"))
    if work_entry_id:
        entry = get_entry(work_entry_id)
    else:
        entry = create_quick_log(
            {
                "note": comment or "Photo evidence added.",
                "entry_date": compact_text(data.get("entry_date")) or dt.date.today().isoformat(),
                "project": data.get("project", ""),
                "tags": ["evidence"],
            }
        )

    object_key = f"{UPLOADS_PREFIX.rstrip('/')}/{entry['id']}/{uuid.uuid4().hex}-{filename}"
    S3_CLIENT.put_object(
        Bucket=UPLOADS_BUCKET,
        Key=object_key,
        Body=raw_image,
        ContentType=content_type,
        Metadata={
            "source": "work-diary",
            "entry-id": str(entry["id"]),
        },
    )
    evidence = create_evidence(
        {
            "work_entry_id": entry["id"],
            "title": compact_text(data.get("title")) or filename,
            "evidence_type": "image",
            "evidence_url": f"s3://{UPLOADS_BUCKET}/{object_key}",
            "description": comment,
            "provider": "s3",
            "provider_metadata": {
                "bucket": UPLOADS_BUCKET,
                "key": object_key,
                "original_filename": filename,
                "content_type": content_type,
                "size_bytes": len(raw_image),
            },
            "storage_key": object_key,
            "attachment_status": "uploaded",
        }
    )
    return {"entry": entry, "evidence": evidence}


def get_evidence(evidence_id: str) -> Dict[str, Any]:
    response = evidence_table.get_item(Key={"id": evidence_id})
    item = response.get("Item")
    if item is None:
        raise KeyError("Evidence not found.")
    return row_to_evidence(item)


def list_evidence(filters: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    filters = filters or {}
    entry_items = scan_table(entries_table)
    evidence_items = scan_table(evidence_table)
    return build_evidence_payload(entry_items, evidence_items, filters)


def build_evidence_payload(
    entry_items: List[Dict[str, Any]],
    evidence_items: List[Dict[str, Any]],
    filters: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    filters = filters or {}
    entries = {item["id"]: row_to_entry(item) for item in entry_items}
    items: List[Dict[str, Any]] = []
    for evidence_record in evidence_items:
        evidence_item = row_to_evidence(evidence_record)
        entry = entries.get(evidence_item["work_entry_id"])
        if entry:
            evidence_item["entry_title"] = entry["title"]
            evidence_item["entry_date"] = entry["entry_date"]
            evidence_item["project"] = entry["project"]
            evidence_item["entry_skills_used"] = entry["skills_used"]
            evidence_item["entry_tags"] = entry["tags"]
        else:
            evidence_item["entry_title"] = ""
            evidence_item["entry_date"] = ""
            evidence_item["project"] = ""
            evidence_item["entry_skills_used"] = []
            evidence_item["entry_tags"] = []
        if evidence_matches(evidence_item, filters):
            items.append(evidence_item)
    items.sort(key=evidence_sort_key, reverse=True)
    return items


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


def update_evidence(evidence_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    current = get_evidence(evidence_id)
    merged = {**current, **data}
    evidence_type = validate_evidence_type(merged.get("evidence_type"))
    evidence_url = validate_url(merged.get("evidence_url"), evidence_type)
    timestamp = now_iso()
    provider_metadata = parse_json_object(merged.get("provider_metadata"))
    item = {
        "id": evidence_id,
        "work_entry_id": current["work_entry_id"],
        "title": required_text(merged, "title", "Evidence title"),
        "evidence_type": evidence_type,
        "evidence_url": evidence_url,
        "description": compact_text(merged.get("description")),
        "provider": compact_text(merged.get("provider")),
        "provider_metadata": provider_metadata,
        "storage_key": compact_text(merged.get("storage_key")),
        "attachment_status": compact_text(merged.get("attachment_status")) or "linked",
        "created_at": current["created_at"],
        "updated_at": timestamp,
    }
    evidence_table.put_item(Item=item)
    return get_evidence(evidence_id)


def delete_evidence(evidence_id: str) -> None:
    response = evidence_table.delete_item(Key={"id": evidence_id})
    if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200:
        raise KeyError("Evidence not found.")


def build_cv_bullet(entry: Dict[str, Any]) -> str:
    subject = compact_text(entry.get("title", "")).rstrip(".")
    work = compact_text(entry.get("what_i_did", "")).rstrip(".")
    outcome = compact_text(entry.get("outcome", "")).rstrip(".")
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


def generate_llm_achievements(entry: Dict[str, Any], config: Dict[str, str]) -> List[str]:
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
    entry: Dict[str, Any], config: Optional[Dict[str, str]] = None
) -> List[str]:
    config = config or read_openai_config()
    if config.get("api_key"):
        try:
            return generate_llm_achievements(entry, config)
        except Exception:
            return fallback_achievement_bullets(entry)
    return fallback_achievement_bullets(entry)


def build_llm_input(entry: Dict[str, Any], evidence_items: List[Dict[str, Any]]) -> str:
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
        data=json.dumps(body, ensure_ascii=True).encode("utf-8"),
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


def model_supports_reasoning(model: str) -> bool:
    normalized = compact_text(model).lower()
    return normalized.startswith(("gpt-5", "o1", "o3", "o4"))


def draft_cv_bullet(entry_id: str) -> Dict[str, Any]:
    entry = get_entry(entry_id)
    evidence_items = list_evidence_for_entry(entry_id)
    config = read_openai_config()
    if config["api_key"]:
        bullet = generate_llm_cv_bullet(entry, evidence_items, config)
    else:
        bullet = build_cv_bullet(entry)
    return update_entry(entry_id, {"cv_bullet_draft": bullet})


def list_evidence_for_entry(entry_id: str) -> List[Dict[str, Any]]:
    return [row_to_evidence(item) for item in scan_table(evidence_table) if item.get("work_entry_id") == entry_id]


def get_options() -> Dict[str, Any]:
    entries = build_entries_payload(scan_table(entries_table), scan_table(evidence_table))
    tasks = [row_to_task(item) for item in scan_table(tasks_table)]
    return build_options_payload(entries, tasks)


def build_options_payload(
    entries: List[Dict[str, Any]], tasks: List[Dict[str, Any]]
) -> Dict[str, Any]:
    projects = sorted(
        {
            *{entry["project"] for entry in entries if entry["project"]},
            *{task["project"] for task in tasks if task["project"]},
        }
    )
    skills = sorted({skill for entry in entries for skill in entry["skills_used"]})
    tags = sorted({tag for entry in entries for tag in entry["tags"]})
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


def get_bootstrap() -> Dict[str, Any]:
    task_items = scan_table(tasks_table)
    entry_items = scan_table(entries_table)
    evidence_items = scan_table(evidence_table)
    tasks = [row_to_task(item) for item in task_items]
    tasks.sort(key=task_sort_key)
    entries = build_entries_payload(entry_items, evidence_items)
    evidence = build_evidence_payload(entry_items, evidence_items)
    achievements = list_achievements()
    return {
        "tasks": tasks,
        "entries": entries,
        "evidence": evidence,
        "achievements": achievements,
        "options": build_options_payload(entries, tasks),
    }


def generate_llm_cv_bullet(
    entry: Dict[str, Any], evidence_items: List[Dict[str, Any]], config: Dict[str, str]
) -> str:
    body: Dict[str, Any] = {
        "model": config["model"],
        "instructions": """
You turn private work diary notes into one CV bullet.
Output exactly one concise bullet sentence with no markdown.
Use a strong active verb and preserve technical detail.
Stay truthful: do not invent metrics, employers, job titles, scope, dates, or outcomes.
If the entry has no measurable result, use a credible qualitative result from the provided outcome or work performed.
Prefer UK English spelling.
""".strip(),
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


def route_api(method: str, path: str, query: Dict[str, str], body: Dict[str, Any]) -> Any:
    entry_match = re.match(r"^/api/entries/([^/]+)$", path)
    bullet_match = re.match(r"^/api/entries/([^/]+)/draft-bullet$", path)
    evidence_match = re.match(r"^/api/evidence/([^/]+)$", path)
    task_match = re.match(r"^/api/tasks/([^/]+)$", path)

    if method == "POST" and path == "/api/login":
        if not auth_is_configured():
            raise ValidationError("Set APP_PASSWORD and SESSION_SECRET in environment first.")
        if not password_matches(body.get("password")):
            raise ValidationError("Incorrect password.")
        token = make_session_token(get_config_value("SESSION_SECRET"), int(get_config_value("SESSION_MAX_AGE_SECONDS", "2592000")))
        return {"ok": True, "token": token}
    if method == "POST" and path == "/api/logout":
        return {"ok": True}
    if method == "GET" and path == "/api/health":
        return {"ok": True}
    if method == "GET" and path == "/api/bootstrap":
        return get_bootstrap()
    if method == "GET" and path == "/api/options":
        return get_options()
    if method == "GET" and path == "/api/achievements":
        return list_achievements(query)
    if method == "GET" and path == "/api/tasks":
        return list_tasks(query)
    if method == "POST" and path == "/api/tasks":
        return create_task(body)
    if method == "GET" and task_match:
        return get_task(task_match.group(1))
    if method == "PUT" and task_match:
        return update_task(task_match.group(1), body)
    if method == "DELETE" and task_match:
        delete_task(task_match.group(1))
        return {"ok": True}
    if method == "GET" and path == "/api/entries":
        return list_entries(query)
    if method == "POST" and path == "/api/entries":
        return create_entry(body)
    if method == "POST" and path == "/api/quick-logs":
        return create_quick_log(body)
    if method == "POST" and path == "/api/image-evidence":
        return create_image_evidence(body)
    if method == "GET" and entry_match:
        return get_entry(entry_match.group(1))
    if method == "PUT" and entry_match:
        return update_entry(entry_match.group(1), body)
    if method == "DELETE" and entry_match:
        delete_entry(entry_match.group(1))
        return {"ok": True}
    if method == "POST" and bullet_match:
        return draft_cv_bullet(bullet_match.group(1))
    if method == "GET" and path == "/api/evidence":
        return list_evidence(query)
    if method == "POST" and path == "/api/evidence":
        return create_evidence(body)
    if method == "PUT" and evidence_match:
        return update_evidence(evidence_match.group(1), body)
    if method == "DELETE" and evidence_match:
        delete_evidence(evidence_match.group(1))
        return {"ok": True}
    raise KeyError("Route not found.")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if method == "OPTIONS":
        return response(204, {})
    if path not in PUBLIC_API_PATHS and not is_authenticated(headers):
        return response(401, {"error": "Login required."})
    try:
        body = parse_json_body(event) if method in {"POST", "PUT"} else {}
        query = parse_query(event)
        result = route_api(method, path, query, body)
        return response(200, result)
    except ValidationError as exc:
        return response(400, {"error": str(exc)})
    except KeyError as exc:
        return response(404, {"error": str(exc).strip("'")})
    except OpenAIRequestError as exc:
        return response(502, {"error": str(exc)})
    except json.JSONDecodeError:
        return response(400, {"error": "Request body must be valid JSON."})
    except Exception as exc:
        return response(500, {"error": f"Unexpected error: {exc}"})


class ValidationError(Exception):
    pass


class OpenAIRequestError(Exception):
    pass
