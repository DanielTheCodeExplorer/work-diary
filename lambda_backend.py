import base64
import binascii
import datetime as dt
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import boto3

from integration_security import oauth_state_is_fresh, safe_return_url
from task_schedule import TaskSchedule, TaskScheduleValidationError

try:
    from botocore.exceptions import ClientError
except Exception:  # pragma: no cover - only used when botocore is unavailable in tests.
    class ClientError(Exception):
        def __init__(self, error_response: Dict[str, Any], operation_name: str = ""):
            super().__init__(str(error_response))
            self.response = error_response

try:
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover - dependency is supplied in Lambda by requirements.txt.
    WebPushException = Exception
    webpush = None

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
PUBLIC_API_PATHS = {
    "/api/login",
    "/api/logout",
    "/api/health",
    "/api/integrations/google/callback",
}
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
PROJECT_STATUSES = ["planned", "active", "paused", "complete"]
DEFAULT_PROJECT_COLOR = "#5DD4C0"
MAX_PROJECT_SUGGESTIONS = 3
REMINDER_TIMEZONE = "Europe/London"
TASK_REMINDER_OFFSET_MINUTES = 10
TASK_REMINDER_MAX_LATENESS_MINUTES = 30
WEB_PUSH_TTL_SECONDS = 24 * 60 * 60

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

DYNAMODB = boto3.resource("dynamodb")
S3_CLIENT = boto3.client("s3")
SCHEDULER_CLIENT = boto3.client("scheduler")
TASKS_TABLE = os.environ.get("TASKS_TABLE", "WorkDiaryTasks")
ENTRIES_TABLE = os.environ.get("ENTRIES_TABLE", "WorkDiaryEntries")
EVIDENCE_TABLE = os.environ.get("EVIDENCE_TABLE", "WorkDiaryEvidence")
ACHIEVEMENTS_TABLE = os.environ.get("ACHIEVEMENTS_TABLE", "WorkDiaryAchievements")
PROJECTS_TABLE = os.environ.get("PROJECTS_TABLE", "WorkDiaryProjects")
PUSH_SUBSCRIPTIONS_TABLE = os.environ.get("PUSH_SUBSCRIPTIONS_TABLE", "WorkDiaryPushSubscriptions")
GOOGLE_INTEGRATION_TABLE = os.environ.get("GOOGLE_INTEGRATION_TABLE", "WorkDiaryGoogleIntegration")
UPLOADS_BUCKET = os.environ.get("UPLOADS_BUCKET", "")
UPLOADS_PREFIX = os.environ.get("UPLOADS_PREFIX", "uploads/")
REMINDER_SCHEDULE_GROUP = os.environ.get("REMINDER_SCHEDULE_GROUP", "work-diary-reminders")
REMINDER_SCHEDULER_ROLE_ARN = os.environ.get("REMINDER_SCHEDULER_ROLE_ARN", "")
WORK_DIARY_FUNCTION_ARN = os.environ.get("WORK_DIARY_FUNCTION_ARN", "")
VAPID_PUBLIC_KEY = os.environ.get("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", "")
VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:you@example.com")

tasks_table = DYNAMODB.Table(TASKS_TABLE)
entries_table = DYNAMODB.Table(ENTRIES_TABLE)
evidence_table = DYNAMODB.Table(EVIDENCE_TABLE)
achievements_table = DYNAMODB.Table(ACHIEVEMENTS_TABLE)
projects_table = DYNAMODB.Table(PROJECTS_TABLE)
push_subscriptions_table = DYNAMODB.Table(PUSH_SUBSCRIPTIONS_TABLE)
google_integration_table = DYNAMODB.Table(GOOGLE_INTEGRATION_TABLE)


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
        raise ValidationError("Difficulty must be easy, medium, hard, or stretch.")
    return text


def validate_source_mode(value: Any) -> str:
    text = compact_text(value) or "detailed"
    if text not in {"quick_log", "detailed"}:
        raise ValidationError("Source mode must be quick_log or detailed.")
    return text


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


def read_vapid_config() -> Dict[str, str]:
    return {
        "public_key": get_config_value("VAPID_PUBLIC_KEY"),
        "private_key": get_config_value("VAPID_PRIVATE_KEY"),
        "subject": get_config_value("VAPID_SUBJECT", VAPID_SUBJECT),
    }


def read_google_config() -> Dict[str, str]:
    app_base_url = get_config_value("APP_BASE_URL").rstrip("/")
    redirect_uri = get_config_value("GOOGLE_REDIRECT_URI")
    if not redirect_uri and app_base_url:
        redirect_uri = f"{app_base_url}/api/integrations/google/callback"
    return {
        "client_id": get_config_value("GOOGLE_CLIENT_ID"),
        "client_secret": get_config_value("GOOGLE_CLIENT_SECRET"),
        "redirect_uri": redirect_uri,
        "frontend_url": get_config_value("APP_FRONTEND_URL") or app_base_url or "/",
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
        with urlopen(request, timeout=timeout) as response_value:
            raw = response_value.read().decode("utf-8")
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


def get_google_integration() -> Dict[str, Any]:
    try:
        item = google_integration_table.get_item(
            Key={"id": "default"}, ConsistentRead=True
        ).get("Item")
    except AttributeError:
        item = None
    payload = {**google_integration_defaults(), **(item or {})}
    try:
        payload["token_expires_at"] = int(payload.get("token_expires_at") or 0)
    except (TypeError, ValueError):
        payload["token_expires_at"] = 0
    return payload


def save_google_integration(updates: Dict[str, Any]) -> Dict[str, Any]:
    current = {**get_google_integration(), **updates}
    current["id"] = "default"
    current["updated_at"] = now_iso()
    try:
        google_integration_table.put_item(Item=current)
    except AttributeError:
        pass
    return current


def google_is_connected(integration: Dict[str, Any]) -> bool:
    return bool(integration.get("refresh_token") or integration.get("access_token"))


def google_failed_task_count() -> int:
    return len([task for task in list_tasks({}) if compact_text(task.get("google_sync_error"))])


def google_status() -> Dict[str, Any]:
    config = read_google_config()
    integration = get_google_integration()
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
        "failed_task_count": google_failed_task_count(),
        "scopes": GOOGLE_SCOPES,
    }


def start_google_connect(data: Dict[str, Any]) -> Dict[str, Any]:
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
        {
            "oauth_state": state,
            "oauth_state_created_at": now_iso(),
            "oauth_return_url": return_url,
            "last_error": "",
        }
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
    integration: Dict[str, Any], config: Optional[Dict[str, str]] = None
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
        {
            "access_token": payload.get("access_token", ""),
            "token_expires_at": int(time.time()) + max(60, expires_in) - 60,
            "scope": payload.get("scope", integration.get("scope", "")),
            "last_error": "",
        }
    )


def google_access_token() -> tuple[str, Dict[str, Any]]:
    config = read_google_config()
    if not google_client_is_configured(config):
        raise GoogleIntegrationError("Google OAuth is not configured.")
    integration = get_google_integration()
    if not google_is_connected(integration):
        raise GoogleIntegrationError("Google is not connected.")
    if integration.get("access_token") and int(integration.get("token_expires_at") or 0) > int(time.time()) + 30:
        return integration["access_token"], integration
    integration = google_refresh_access_token(integration, config)
    return integration["access_token"], integration


def google_api(method: str, url: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    token, _integration = google_access_token()
    return google_http_json(method, url, access_token=token, body=body)


def ensure_google_calendar(integration: Dict[str, Any]) -> str:
    calendar_id = compact_text(integration.get("calendar_id"))
    if calendar_id == GOOGLE_PRIMARY_CALENDAR_ID:
        return calendar_id
    save_google_integration(
        {"calendar_id": GOOGLE_PRIMARY_CALENDAR_ID, "last_error": ""}
    )
    return GOOGLE_PRIMARY_CALENDAR_ID


def ensure_google_tasklist(integration: Dict[str, Any]) -> str:
    tasklist_id = compact_text(integration.get("tasklist_id"))
    if tasklist_id == GOOGLE_DEFAULT_TASKLIST_ID:
        return tasklist_id
    save_google_integration(
        {"tasklist_id": GOOGLE_DEFAULT_TASKLIST_ID, "last_error": ""}
    )
    return GOOGLE_DEFAULT_TASKLIST_ID


def ensure_google_destinations() -> Dict[str, Any]:
    integration = get_google_integration()
    ensure_google_tasklist(integration)
    return get_google_integration()


def html_escape(value: Any) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


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
  <title>Work Diary Google connection</title>
  <style>body{{font-family:system-ui,-apple-system,sans-serif;margin:2rem;line-height:1.5;background:#111;color:#f5f5f5}}a{{color:#5DD4C0}}</style>
</head>
<body>
  <h1>{safe_message}</h1>
  <p>Returning to Work Diary...</p>
  <p><a href=\"{safe_url}\">Open Work Diary</a></p>
</body>
</html>"""
    }


def complete_google_connect(query: Dict[str, str]) -> Dict[str, Any]:
    integration = get_google_integration()
    return_url = integration.get("oauth_return_url") or read_google_config()["frontend_url"] or "/"
    if query.get("error"):
        save_google_integration({"last_error": query["error"], "oauth_state": ""})
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
            {
                "last_error": "Google OAuth state was invalid or expired.",
                "oauth_state": "",
                "oauth_state_created_at": "",
            }
        )
        return google_callback_html("Google connection failed.", return_url)
    code = compact_text(query.get("code"))
    if not code:
        save_google_integration({"last_error": "Google OAuth code was missing."})
        return google_callback_html("Google connection failed.", return_url)
    config = read_google_config()
    token_payload = google_exchange_code(code, config)
    refresh_token = token_payload.get("refresh_token") or integration.get("refresh_token", "")
    if not refresh_token:
        save_google_integration({"last_error": "Google did not return a refresh token. Try connecting again."})
        return google_callback_html("Google connection failed.", return_url)
    expires_in = int(token_payload.get("expires_in") or 3600)
    save_google_integration(
        {
            "access_token": token_payload.get("access_token", ""),
            "refresh_token": refresh_token,
            "token_expires_at": int(time.time()) + max(60, expires_in) - 60,
            "scope": token_payload.get("scope", " ".join(GOOGLE_SCOPES)),
            "oauth_state": "",
            "oauth_state_created_at": "",
            "connected_at": now_iso(),
            "last_error": "",
        }
    )
    try:
        ensure_google_destinations()
    except GoogleIntegrationError:
        logger.warning("Google destination setup failed after OAuth", exc_info=True)
        save_google_integration(
            {
                "last_error": (
                    "Google authorization succeeded, but setup is incomplete. "
                    "Enable the Google Tasks API, then finish setup."
                )
            }
        )
        return google_callback_html(
            "Google authorization succeeded, but setup is incomplete.", return_url
        )
    retry_google_sync(include_all=True)
    save_google_integration({"last_error": ""})
    return google_callback_html("Google connected.", return_url)


def retry_google_connection() -> Dict[str, Any]:
    try:
        ensure_google_destinations()
    except GoogleIntegrationError:
        save_google_integration(
            {
                "last_error": (
                    "Google setup is incomplete. Enable the Google Tasks API, then try again."
                )
            }
        )
        raise
    result = retry_google_sync(include_all=True)
    save_google_integration({"last_error": ""})
    return {**result, "status": google_status()}


def google_task_sync_target(task: Dict[str, Any]) -> str:
    return "google_task"


def google_task_hash(task: Dict[str, Any]) -> str:
    payload = {
        "target": google_task_sync_target(task),
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
        body["completed"] = compact_text(task.get("completed_at")) or now_iso()
    task_date = compact_text(task.get("due_date") or task.get("start_date"))
    if task_date:
        body["due"] = f"{task_date}T00:00:00.000Z"
    return body


def save_task_google_sync_state(
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
    task = get_task(str(task_id))
    timestamp = now_iso()
    calendar_event_id = task.get("google_calendar_event_id", "") if calendar_event_id is None else calendar_event_id
    calendar_event_link = task.get("google_calendar_event_link", "") if calendar_event_link is None else calendar_event_link
    google_task_id = task.get("google_task_id", "") if google_task_id is None else google_task_id
    google_task_link = task.get("google_task_link", "") if google_task_link is None else google_task_link
    task.update(
        {
            "google_sync_target": compact_text(target),
            "google_calendar_event_id": compact_text(calendar_event_id),
            "google_calendar_event_link": compact_text(calendar_event_link),
            "google_task_id": compact_text(google_task_id),
            "google_task_link": compact_text(google_task_link),
            "google_sync_hash": compact_text(sync_hash),
            "google_synced_at": timestamp if not error else compact_text(task.get("google_synced_at")),
            "google_sync_error": compact_text(error),
        }
    )
    tasks_table.put_item(Item=task)
    if error:
        save_google_integration({"last_error": compact_text(error)})
    else:
        save_google_integration({"last_sync_at": timestamp, "last_error": ""})


def save_task_google_error(task_id: Any, error: Exception) -> None:
    task = get_task(str(task_id))
    save_task_google_sync_state(
        task_id,
        target=compact_text(task.get("google_sync_target")) or google_task_sync_target(task),
        sync_hash=compact_text(task.get("google_sync_hash")),
        calendar_event_id=compact_text(task.get("google_calendar_event_id")),
        calendar_event_link=compact_text(task.get("google_calendar_event_link")),
        google_task_id=compact_text(task.get("google_task_id")),
        google_task_link=compact_text(task.get("google_task_link")),
        error=compact_text(str(error))[:500],
    )


def delete_google_calendar_event(task: Dict[str, Any]) -> None:
    event_id = compact_text(task.get("google_calendar_event_id"))
    if not event_id:
        return
    calendar_id = ensure_google_calendar(get_google_integration())
    try:
        google_api(
            "DELETE",
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}",
        )
    except GoogleIntegrationError as exc:
        if "(404)" not in str(exc) and "(410)" not in str(exc):
            raise


def delete_google_task(task: Dict[str, Any]) -> None:
    task_id = compact_text(task.get("google_task_id"))
    if not task_id:
        return
    tasklist_id = ensure_google_tasklist(get_google_integration())
    try:
        google_api(
            "DELETE",
            f"{GOOGLE_TASKS_API_BASE}/lists/{quote(tasklist_id, safe='')}/tasks/{quote(task_id, safe='')}",
        )
    except GoogleIntegrationError as exc:
        if "(404)" not in str(exc) and "(410)" not in str(exc):
            raise


def cleanup_previous_google_target(previous: Dict[str, Any], current_target: str) -> None:
    previous_target = compact_text(previous.get("google_sync_target")) or google_task_sync_target(previous)
    if previous_target == current_target:
        return
    if previous_target == "calendar_event":
        delete_google_calendar_event(previous)
    if previous_target == "google_task":
        delete_google_task(previous)


def sync_calendar_event_for_task(task: Dict[str, Any], sync_hash: str) -> None:
    if task.get("completed"):
        delete_google_calendar_event(task)
        save_task_google_sync_state(
            task["id"],
            target="calendar_and_task",
            sync_hash=sync_hash,
            calendar_event_id="",
            calendar_event_link="",
        )
        return
    calendar_id = ensure_google_calendar(get_google_integration())
    event_id = google_calendar_event_id_for_task(task)
    body = google_calendar_event_body(task, event_id)
    event_url = f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
    if compact_text(task.get("google_calendar_event_id")):
        try:
            payload = google_api("PUT", event_url, body)
        except GoogleIntegrationError as exc:
            if "(404)" not in str(exc) and "(410)" not in str(exc):
                raise
            payload = google_api(
                "POST",
                f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events",
                body,
            )
    else:
        payload = google_api(
            "POST",
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{quote(calendar_id, safe='')}/events",
            body,
        )
    save_task_google_sync_state(
        task["id"],
        target="calendar_and_task",
        sync_hash=sync_hash,
        calendar_event_id=event_id,
        calendar_event_link=compact_text(payload.get("htmlLink")),
    )


def sync_google_task_for_task(
    task: Dict[str, Any], sync_hash: str, *, target: str = "google_task"
) -> None:
    tasklist_id = ensure_google_tasklist(get_google_integration())
    body = google_task_body(task)
    google_task_id = compact_text(task.get("google_task_id"))
    if task.get("completed") and not google_task_id:
        save_task_google_sync_state(task["id"], target=target, sync_hash=sync_hash)
        return
    if google_task_id:
        try:
            payload = google_api(
                "PATCH",
                f"{GOOGLE_TASKS_API_BASE}/lists/{quote(tasklist_id, safe='')}/tasks/{quote(google_task_id, safe='')}",
                body,
            )
        except GoogleIntegrationError as exc:
            if "(404)" not in str(exc) and "(410)" not in str(exc):
                raise
            payload = google_api(
                "POST",
                f"{GOOGLE_TASKS_API_BASE}/lists/{quote(tasklist_id, safe='')}/tasks",
                body,
            )
    else:
        payload = google_api(
            "POST",
            f"{GOOGLE_TASKS_API_BASE}/lists/{quote(tasklist_id, safe='')}/tasks",
            body,
        )
    save_task_google_sync_state(
        task["id"],
        target=target,
        sync_hash=sync_hash,
        google_task_id=compact_text(payload.get("id")),
        google_task_link=compact_text(payload.get("webViewLink")),
    )


def sync_task_to_google(task: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> None:
    if not google_is_connected(get_google_integration()):
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
        delete_google_calendar_event(task)
        save_task_google_sync_state(
            task["id"],
            target="google_task",
            sync_hash=sync_hash,
            calendar_event_id="",
            calendar_event_link="",
        )
        task = get_task(str(task["id"]))
    sync_google_task_for_task(task, sync_hash, target="google_task")


def auto_sync_task_after_save(
    task: Dict[str, Any], previous: Optional[Dict[str, Any]] = None
) -> None:
    try:
        sync_task_to_google(task, previous)
        retry_google_sync(include_all=False, exclude_task_id=task.get("id"))
    except Exception as exc:
        save_task_google_error(task["id"], exc)


def auto_delete_google_for_task(task: Dict[str, Any]) -> None:
    if not google_is_connected(get_google_integration()):
        return
    try:
        target = compact_text(task.get("google_sync_target")) or google_task_sync_target(task)
        if target in {"calendar_event", "calendar_and_task"}:
            delete_google_calendar_event(task)
        if target in {"google_task", "calendar_and_task"}:
            delete_google_task(task)
        save_google_integration({"last_sync_at": now_iso(), "last_error": ""})
    except Exception as exc:
        save_google_integration({"last_error": compact_text(str(exc))[:500]})


def retry_google_sync(include_all: bool = False, exclude_task_id: Any = None) -> Dict[str, Any]:
    if not google_is_connected(get_google_integration()):
        return {"ok": False, "synced": 0, "failed": 0, "skipped": 0}
    task_list = list_tasks({})
    if not include_all:
        task_list = [task for task in task_list if compact_text(task.get("google_sync_error"))]
    synced = 0
    failed = 0
    skipped = 0
    for task in task_list:
        if exclude_task_id is not None and str(task["id"]) == str(exclude_task_id):
            skipped += 1
            continue
        try:
            before_hash = compact_text(task.get("google_sync_hash"))
            sync_task_to_google(task)
            after = get_task(str(task["id"]))
            if compact_text(after.get("google_sync_hash")) != before_hash or not compact_text(after.get("google_sync_error")):
                synced += 1
            else:
                skipped += 1
        except Exception as exc:
            failed += 1
            save_task_google_error(task["id"], exc)
    return {"ok": failed == 0, "synced": synced, "failed": failed, "skipped": skipped}


def disconnect_google() -> Dict[str, Any]:
    save_google_integration(
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
        }
    )
    for task in list_tasks({}):
        task.update(
            {
                "google_sync_target": "",
                "google_calendar_event_id": "",
                "google_calendar_event_link": "",
                "google_task_id": "",
                "google_task_link": "",
                "google_sync_hash": "",
                "google_synced_at": "",
                "google_sync_error": "",
            }
        )
        tasks_table.put_item(Item=task)
    return google_status()


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
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
    }
    if extra_headers:
        headers.update(extra_headers)
    body = json.dumps({} if payload is None else payload).encode("utf-8") if payload is not None else b""
    return {"statusCode": status, "headers": headers, "body": body.decode("utf-8")}


def html_response(status: int, html: str, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    headers = {
        "Content-Type": "text/html; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
    }
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": status, "headers": headers, "body": html}


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


def row_to_push_subscription(item: Dict[str, Any]) -> Dict[str, Any]:
    subscription = item.get("subscription")
    if not isinstance(subscription, dict):
        subscription = {"endpoint": item.get("endpoint", ""), "keys": {}}
    return {
        "id": item.get("id", ""),
        "endpoint": item.get("endpoint", ""),
        "subscription": subscription,
        "user_agent": item.get("user_agent", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


def list_push_subscriptions() -> List[Dict[str, Any]]:
    return [row_to_push_subscription(item) for item in scan_table(push_subscriptions_table)]


def save_push_subscription(data: Dict[str, Any]) -> Dict[str, Any]:
    subscription = normalize_push_subscription(data)
    subscription_id = push_subscription_id(subscription["endpoint"])
    timestamp = now_iso()
    existing = push_subscriptions_table.get_item(
        Key={"id": subscription_id}, ConsistentRead=True
    ).get("Item", {})
    push_subscriptions_table.put_item(
        Item={
            "id": subscription_id,
            "endpoint": subscription["endpoint"],
            "subscription": subscription,
            "user_agent": compact_text(data.get("user_agent")),
            "created_at": existing.get("created_at", timestamp),
            "updated_at": timestamp,
        }
    )
    return {"ok": True, "id": subscription_id}


def delete_push_subscription(data: Dict[str, Any]) -> Dict[str, Any]:
    endpoint = compact_text(data.get("endpoint"))
    if not endpoint and isinstance(data.get("subscription"), dict):
        endpoint = compact_text(data["subscription"].get("endpoint"))
    if not endpoint:
        return {"ok": True, "deleted": 0}
    push_subscriptions_table.delete_item(Key={"id": push_subscription_id(endpoint)})
    return {"ok": True, "deleted": 1}


def delete_push_subscription_by_endpoint(endpoint: Any) -> None:
    if compact_text(endpoint):
        push_subscriptions_table.delete_item(Key={"id": push_subscription_id(endpoint)})


def web_push_error_status(exc: Exception) -> Optional[int]:
    response_value = getattr(exc, "response", None)
    return getattr(response_value, "status_code", None) or getattr(response_value, "status", None)


def send_web_push(subscription: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    config = read_vapid_config()
    if not config["public_key"] or not config["private_key"]:
        raise ValidationError("Phone reminders are not configured yet.")
    if webpush is None:
        raise RuntimeError("pywebpush is not installed.")
    webpush(
        subscription_info=subscription["subscription"],
        data=json.dumps(payload, separators=(",", ":")),
        vapid_private_key=config["private_key"],
        vapid_claims={"sub": config["subject"]},
        ttl=WEB_PUSH_TTL_SECONDS,
    )
    return True


def send_push_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    sent = 0
    failed = 0
    expired = 0
    for subscription in list_push_subscriptions():
        subscription_id = compact_text(subscription.get("id")) or push_subscription_id(
            subscription.get("endpoint")
        )
        try:
            if send_web_push(subscription, payload):
                sent += 1
        except WebPushException as exc:
            status = web_push_error_status(exc)
            if status in {404, 410}:
                delete_push_subscription_by_endpoint(subscription["endpoint"])
                expired += 1
            else:
                failed += 1
            logger.warning(
                "Web Push delivery rejected: subscription=%s status=%s",
                subscription_id,
                status,
            )
        except Exception:
            failed += 1
            logger.exception("Web Push delivery failed: subscription=%s", subscription_id)
    result = {"ok": failed == 0, "sent": sent, "failed": failed, "expired": expired}
    logger.info(
        "Web Push delivery result: tag=%s sent=%d failed=%d expired=%d",
        compact_text(payload.get("tag")),
        sent,
        failed,
        expired,
    )
    return result


def get_push_status() -> Dict[str, Any]:
    config = read_vapid_config()
    subscriptions = list_push_subscriptions()
    public_configured = bool(config["public_key"])
    private_configured = bool(config["private_key"])
    webpush_installed = webpush is not None
    return {
        "ok": True,
        "publicKeyConfigured": public_configured,
        "privateKeyConfigured": private_configured,
        "webpushInstalled": webpush_installed,
        "configured": public_configured and private_configured and webpush_installed,
        "subscriptionCount": len(subscriptions),
        "scheduleGroup": REMINDER_SCHEDULE_GROUP,
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


def task_schedule_name(task_id: Any) -> str:
    clean_id = re.sub(r"[^A-Za-z0-9_-]+", "-", compact_text(task_id))[:48] or "unknown"
    return f"task-{clean_id}-due10"


def schedule_expression_at(local_datetime: dt.datetime) -> str:
    return f"at({local_datetime.replace(tzinfo=None).strftime('%Y-%m-%dT%H:%M:%S')})"


def scheduler_not_found(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in {"ResourceNotFoundException", "ResourceNotFound"}


def scheduler_conflict(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in {"ConflictException", "ResourceAlreadyExistsException"}


def task_reminder_schedule_target(task: Dict[str, Any]) -> Dict[str, Any]:
    reminder_at = task_reminder_datetime(task)
    return {
        "Arn": WORK_DIARY_FUNCTION_ARN,
        "RoleArn": REMINDER_SCHEDULER_ROLE_ARN,
        "Input": json.dumps(
            {
                "source": "work-diary.reminders",
                "action": "task_reminder",
                "task_id": str(task.get("id", "")),
                "expected_reminder_at": reminder_at.isoformat() if reminder_at else "",
            },
            separators=(",", ":"),
        ),
    }


def schedule_task_reminder(task: Dict[str, Any]) -> None:
    task_id = task.get("id")
    if not task_id or task.get("completed"):
        return
    if not WORK_DIARY_FUNCTION_ARN or not REMINDER_SCHEDULER_ROLE_ARN:
        return
    reminder_at = task_reminder_datetime(task)
    if not reminder_at:
        delete_task_reminder_schedule(task_id)
        return
    now_local = dt.datetime.now(ZoneInfo(REMINDER_TIMEZONE))
    if reminder_at <= now_local:
        delete_task_reminder_schedule(task_id)
        return
    name = task_schedule_name(task_id)
    schedule_args = {
        "Name": name,
        "GroupName": REMINDER_SCHEDULE_GROUP,
        "ScheduleExpression": schedule_expression_at(reminder_at),
        "ScheduleExpressionTimezone": REMINDER_TIMEZONE,
        "FlexibleTimeWindow": {"Mode": "OFF"},
        "State": "ENABLED",
        "Description": "Work Diary task reminder",
        "Target": task_reminder_schedule_target(task),
    }
    try:
        SCHEDULER_CLIENT.update_schedule(**schedule_args)
    except ClientError as exc:
        if scheduler_not_found(exc):
            try:
                SCHEDULER_CLIENT.create_schedule(**schedule_args, ActionAfterCompletion="DELETE")
            except ClientError as create_exc:
                if not scheduler_conflict(create_exc):
                    raise
                SCHEDULER_CLIENT.update_schedule(**schedule_args)
        else:
            raise


def delete_task_reminder_schedule(task_id: Any) -> None:
    if not task_id:
        return
    try:
        SCHEDULER_CLIENT.delete_schedule(
            Name=task_schedule_name(task_id),
            GroupName=REMINDER_SCHEDULE_GROUP,
        )
    except ClientError as exc:
        if not scheduler_not_found(exc):
            raise


def should_schedule_task_reminder(task: Dict[str, Any], now_local: Optional[dt.datetime] = None) -> bool:
    if not task.get("id") or task.get("completed"):
        return False
    reminder_at = task_reminder_datetime(task)
    if not reminder_at:
        return False
    current_time = now_local or dt.datetime.now(ZoneInfo(REMINDER_TIMEZONE))
    return reminder_at > current_time


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


def sync_task_reminder_schedules(tasks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    scheduled = 0
    skipped = 0
    failed = 0
    task_list = tasks if tasks is not None else list_tasks({})
    now_local = dt.datetime.now(ZoneInfo(REMINDER_TIMEZONE))

    for task in task_list:
        if not should_schedule_task_reminder(task, now_local):
            try:
                delete_task_reminder_schedule(task.get("id"))
                skipped += 1
            except Exception:
                failed += 1
            continue
        try:
            schedule_task_reminder(task)
            scheduled += 1
        except Exception:
            failed += 1

    return {"ok": failed == 0, "scheduled": scheduled, "skipped": skipped, "failed": failed}


def open_tasks_due_on(tasks: List[Dict[str, Any]], date_value: str) -> List[Dict[str, Any]]:
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


def build_daily_summary_payload(tasks: List[Dict[str, Any]], date_value: Optional[str] = None) -> Dict[str, Any]:
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


def task_reminder_event_is_timely(
    reminder_at: dt.datetime,
    now_local: Optional[dt.datetime] = None,
) -> bool:
    current_time = now_local or dt.datetime.now(ZoneInfo(REMINDER_TIMEZONE))
    age = current_time - reminder_at
    return dt.timedelta(minutes=-1) <= age <= dt.timedelta(
        minutes=TASK_REMINDER_MAX_LATENESS_MINUTES
    )


def record_task_reminder_delivery(task_id: str, expected_reminder_at: str) -> None:
    tasks_table.update_item(
        Key={"id": task_id},
        UpdateExpression="SET reminder_sent_for = :expected",
        ExpressionAttributeValues={":expected": expected_reminder_at},
    )


def handle_reminder_event(event: Dict[str, Any]) -> Dict[str, Any]:
    action = compact_text(event.get("action") or event.get("detail", {}).get("action"))
    if action == "task_reminder":
        task = get_task(compact_text(event.get("task_id") or event.get("detail", {}).get("task_id")))
        if task.get("completed"):
            return {"ok": True, "sent": 0, "skipped": "Task already completed."}
        expected = compact_text(
            event.get("expected_reminder_at")
            or event.get("detail", {}).get("expected_reminder_at")
        )
        current = task_reminder_datetime(task)
        if not current or not expected or expected != current.isoformat():
            return {"ok": True, "sent": 0, "skipped": "Stale task reminder."}
        if not task_reminder_event_is_timely(current):
            return {"ok": True, "sent": 0, "skipped": "Expired task reminder."}
        if compact_text(task.get("reminder_sent_for")) == expected:
            return {"ok": True, "sent": 0, "skipped": "Task reminder already sent."}
        result = send_push_payload(build_task_reminder_payload(task))
        if int(result.get("sent") or 0) > 0:
            try:
                record_task_reminder_delivery(str(task.get("id", "")), expected)
            except Exception:
                logger.exception("Could not record task reminder delivery")
        return result
    if action == "daily_summary":
        return send_push_payload(build_daily_summary_payload(list_tasks({})))
    raise NotFoundError("Reminder route not found.")


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = compact_text(value).lower()
    return text not in {"", "0", "false", "no", "none"}


def parse_iso_datetime(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def task_sort_key(task: Dict[str, Any]) -> Any:
    due_date = task.get("due_date") or "9999-12-31"
    start_date = task.get("start_date") or due_date
    start_time = task.get("start_time") or task.get("due_time") or "23:59"
    due_time = task.get("due_time") or "23:59"
    created_at = task.get("created_at") or ""
    try:
        created_at_ts = -dt.datetime.fromisoformat(created_at).timestamp()
    except Exception:
        created_at_ts = 0
    return (
        1 if task.get("completed") else 0,
        1 if not (task.get("start_date") or task.get("due_date")) else 0,
        start_date,
        start_time,
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
        "project_id": item.get("project_id", ""),
        "project": item.get("project", ""),
        "start_date": item.get("start_date", ""),
        "start_time": item.get("start_time", ""),
        "due_date": item.get("due_date", ""),
        "due_time": item.get("due_time", ""),
        "reminder_at": item.get("reminder_at", ""),
        "reminder_sent_for": item.get("reminder_sent_for", ""),
        "repeat_rule": item.get("repeat_rule", "none"),
        "repeat_interval_days": int(item.get("repeat_interval_days") or 1),
        "repeat_until": item.get("repeat_until", ""),
        "priority": item.get("priority", ""),
        "location": item.get("location", ""),
        "notes": item.get("notes", ""),
        "project_order": int(item.get("project_order") or 0),
        "google_sync_target": item.get("google_sync_target", ""),
        "google_calendar_event_id": item.get("google_calendar_event_id", ""),
        "google_calendar_event_link": item.get("google_calendar_event_link", ""),
        "google_task_id": item.get("google_task_id", ""),
        "google_task_link": item.get("google_task_link", ""),
        "google_sync_hash": item.get("google_sync_hash", ""),
        "google_synced_at": item.get("google_synced_at", ""),
        "google_sync_error": item.get("google_sync_error", ""),
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
        "project_id": item.get("project_id", ""),
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


def row_to_project(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": item.get("id", ""),
        "name": item.get("name", ""),
        "goal": item.get("goal", ""),
        "deadline": item.get("deadline", ""),
        "status": item.get("status", "active"),
        "color": item.get("color", DEFAULT_PROJECT_COLOR),
        "notes": item.get("notes", ""),
        "completed_at": item.get("completed_at", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


def project_sort_key(project: Dict[str, Any]) -> Any:
    status_rank = {"active": 0, "planned": 1, "paused": 2, "complete": 3}
    return (
        status_rank.get(project.get("status"), 4),
        1 if not project.get("deadline") else 0,
        project.get("deadline") or "",
        project.get("name", "").lower(),
    )


def list_projects() -> List[Dict[str, Any]]:
    ensure_projects_from_existing_labels()
    projects = [row_to_project(item) for item in scan_table(projects_table)]
    return sorted(projects, key=project_sort_key)


def get_project(project_id: Any) -> Dict[str, Any]:
    text_id = compact_text(project_id)
    if not text_id:
        raise NotFoundError("Project not found.")
    response = projects_table.get_item(Key={"id": text_id}, ConsistentRead=True)
    item = response.get("Item")
    if item is None:
        raise NotFoundError("Project not found.")
    return row_to_project(item)


def find_project_by_name(name: Any) -> Optional[Dict[str, Any]]:
    project_name = compact_text(name)
    if not project_name:
        return None
    for item in scan_table(projects_table):
        if compact_text(item.get("name")).lower() == project_name.lower():
            return row_to_project(item)
    return None


def create_project(data: Dict[str, Any], *, auto_created: bool = False) -> Dict[str, Any]:
    timestamp = now_iso()
    name = required_text(data, "name", "Project name")
    existing = find_project_by_name(name)
    if existing:
        return existing
    item_id = uuid.uuid4().hex
    item = {
        "id": item_id,
        "name": name,
        "goal": compact_text(data.get("goal")),
        "deadline": validate_optional_date(data.get("deadline"), "Project deadline"),
        "status": validate_project_status(data.get("status") or ("active" if auto_created else "planned")),
        "color": validate_project_color(data.get("color")),
        "notes": compact_text(data.get("notes")),
        "completed_at": "",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    projects_table.put_item(Item=item)
    return get_project(item_id)


def update_project(project_id: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    current = get_project(project_id)
    merged = {**current, **data}
    timestamp = now_iso()
    name = required_text(merged, "name", "Project name")
    status = validate_project_status(merged.get("status"))
    completed_at = current.get("completed_at", "")
    if status != "complete":
        completed_at = ""
    elif not completed_at:
        completed_at = timestamp
    item = {
        "id": current["id"],
        "name": name,
        "goal": compact_text(merged.get("goal")),
        "deadline": validate_optional_date(merged.get("deadline"), "Project deadline"),
        "status": status,
        "color": validate_project_color(merged.get("color")),
        "notes": compact_text(merged.get("notes")),
        "completed_at": completed_at,
        "created_at": current["created_at"],
        "updated_at": timestamp,
    }
    projects_table.put_item(Item=item)
    for raw_task in scan_table(tasks_table):
        if compact_text(raw_task.get("project_id")) == current["id"]:
            raw_task["project"] = name
            raw_task["updated_at"] = timestamp
            tasks_table.put_item(Item=raw_task)
    linked_entry_ids = set()
    for raw_entry in scan_table(entries_table):
        if compact_text(raw_entry.get("project_id")) == current["id"]:
            raw_entry["project"] = name
            raw_entry["updated_at"] = timestamp
            linked_entry_ids.add(raw_entry.get("id", ""))
            entries_table.put_item(Item=raw_entry)
    for achievement in scan_table(achievements_table):
        if achievement.get("source_entry_id") in linked_entry_ids:
            achievement["project"] = name
            achievement["updated_at"] = timestamp
            achievements_table.put_item(Item=achievement)
    return get_project(current["id"])


def find_project_completion_entry(project_id: Any) -> Optional[Dict[str, Any]]:
    for item in scan_table(entries_table):
        entry = row_to_entry(item)
        if (
            str(entry.get("project_id") or "") == str(project_id)
            and "project_completion" in entry.get("tags", [])
        ):
            return entry
    return None


def project_completion_note(project: Dict[str, Any]) -> str:
    tasks = [
        task
        for task in list_tasks()
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


def complete_project(project_id: Any) -> Dict[str, Any]:
    current = get_project(project_id)
    timestamp = now_iso()
    existing_entry = find_project_completion_entry(current["id"])
    completed_at = current.get("completed_at") or timestamp

    if current.get("status") != "complete" or current.get("completed_at") != completed_at:
        item = {
            **current,
            "status": "complete",
            "completed_at": completed_at,
            "updated_at": timestamp,
        }
        projects_table.put_item(Item=item)

    updated = get_project(current["id"])
    if not existing_entry:
        create_quick_log(
            {
                "entry_date": completed_at[:10],
                "title": f"Completed project: {updated['name']}",
                "note": project_completion_note(updated),
                "project_id": updated["id"],
                "project": updated["name"],
                "tags": ["project_completion", "project"],
            }
        )
    return get_project(current["id"])


def delete_project(project_id: Any) -> None:
    current = get_project(project_id)
    timestamp = now_iso()
    for raw_task in scan_table(tasks_table):
        if compact_text(raw_task.get("project_id")) == current["id"]:
            raw_task["project_id"] = ""
            raw_task["project"] = ""
            raw_task["updated_at"] = timestamp
            tasks_table.put_item(Item=raw_task)
    linked_entry_ids = set()
    for raw_entry in scan_table(entries_table):
        if compact_text(raw_entry.get("project_id")) == current["id"]:
            raw_entry["project_id"] = ""
            raw_entry["project"] = ""
            raw_entry["updated_at"] = timestamp
            linked_entry_ids.add(raw_entry.get("id", ""))
            entries_table.put_item(Item=raw_entry)
    for achievement in scan_table(achievements_table):
        if achievement.get("source_entry_id") in linked_entry_ids:
            achievement["project"] = ""
            achievement["updated_at"] = timestamp
            achievements_table.put_item(Item=achievement)
    projects_table.delete_item(Key={"id": current["id"]})


def ensure_project_for_name(name: Any) -> Optional[Dict[str, Any]]:
    project_name = compact_text(name)
    if not project_name:
        return None
    existing = find_project_by_name(project_name)
    if existing:
        return existing
    return create_project({"name": project_name, "status": "active"}, auto_created=True)


def ensure_projects_from_existing_labels() -> None:
    existing = {
        compact_text(item.get("name")).lower(): compact_text(item.get("id"))
        for item in scan_table(projects_table)
        if compact_text(item.get("name"))
    }
    labels = []
    for item in [*scan_table(tasks_table), *scan_table(entries_table)]:
        label = compact_text(item.get("project"))
        if label and label.lower() not in {value.lower() for value in labels}:
            labels.append(label)
    for label in labels:
        key = label.lower()
        project_id = existing.get(key)
        if not project_id:
            project = create_project(
                {"name": label, "status": "active", "color": DEFAULT_PROJECT_COLOR},
                auto_created=True,
            )
            project_id = project["id"]
            existing[key] = project_id
        for raw_task in scan_table(tasks_table):
            if not compact_text(raw_task.get("project_id")) and compact_text(raw_task.get("project")).lower() == key:
                raw_task["project_id"] = project_id
                tasks_table.put_item(Item=raw_task)
        for raw_entry in scan_table(entries_table):
            if not compact_text(raw_entry.get("project_id")) and compact_text(raw_entry.get("project")).lower() == key:
                raw_entry["project_id"] = project_id
                entries_table.put_item(Item=raw_entry)


def resolve_project_fields(data: Dict[str, Any]) -> tuple[str, str]:
    project_id = compact_text(data.get("project_id"))
    project_name = compact_text(data.get("project"))
    if project_id:
        project = get_project(project_id)
        return project["id"], project["name"]
    if project_name:
        project = ensure_project_for_name(project_name)
        if project:
            return project["id"], project["name"]
    return "", ""


def get_task(task_id: str) -> Dict[str, Any]:
    response = tasks_table.get_item(Key={"id": task_id}, ConsistentRead=True)
    item = response.get("Item")
    if item is None:
        raise NotFoundError("Task not found.")
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


def next_project_task_order(project_id: str) -> int:
    if not compact_text(project_id):
        return 0
    orders = [
        int(task.get("project_order") or 0)
        for task in list_tasks({})
        if str(task.get("project_id") or "") == str(project_id)
    ]
    return (max(orders) if orders else 0) + 10


def create_task(data: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_iso()
    completed = validate_boolean(data.get("completed"), "Completed")
    item_id = uuid.uuid4().hex
    schedule = validate_task_schedule(data)
    start_date, due_date = schedule.start_date, schedule.end_date
    start_time, due_time = schedule.start_time, schedule.end_time
    project_id, project_name = resolve_project_fields(data)
    project_order = validate_project_order(data.get("project_order"))
    if project_id and not project_order:
        project_order = next_project_task_order(project_id)
    item = {
        "id": item_id,
        "title": required_text(data, "title", "Task title", 240),
        "project_id": project_id,
        "project": project_name,
        "start_date": start_date,
        "start_time": start_time,
        "due_date": due_date,
        "due_time": due_time,
        "reminder_at": validate_optional_datetime(data.get("reminder_at"), "Reminder"),
        "repeat_rule": validate_repeat_rule(data.get("repeat_rule")),
        "repeat_interval_days": validate_repeat_interval_days(data.get("repeat_interval_days")),
        "repeat_until": validate_optional_date(data.get("repeat_until"), "Repeat stop date"),
        "priority": validate_task_priority(data.get("priority")),
        "location": limited_text(data.get("location"), "Location", 500),
        "notes": limited_text(data.get("notes"), "Notes", 20_000),
        "project_order": project_order,
        "google_sync_target": "",
        "google_calendar_event_id": "",
        "google_calendar_event_link": "",
        "google_task_id": "",
        "google_task_link": "",
        "google_sync_hash": "",
        "google_synced_at": "",
        "google_sync_error": "",
        "completed": completed,
        "completed_at": timestamp if completed else "",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    tasks_table.put_item(Item=item)
    task = get_task(item_id)
    schedule_task_reminder(task)
    auto_sync_task_after_save(task)
    return task


def update_task(task_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
    current = get_task(task_id)
    merged = {**current, **data}
    timestamp = now_iso()
    completed = validate_boolean(merged.get("completed"), "Completed")
    completed_at = current["completed_at"]
    if completed and not current["completed"]:
        completed_at = timestamp
    if not completed:
        completed_at = ""
    schedule = validate_task_schedule(merged)
    start_date, due_date = schedule.start_date, schedule.end_date
    start_time, due_time = schedule.start_time, schedule.end_time
    project_id, project_name = resolve_project_fields(merged)
    project_order = validate_project_order(merged.get("project_order"))
    if str(project_id or "") != str(current.get("project_id") or ""):
        project_order = 0
    if project_id and not project_order:
        project_order = next_project_task_order(project_id)
    item = {
        "id": task_id,
        "title": required_text(merged, "title", "Task title", 240),
        "project_id": project_id,
        "project": project_name,
        "start_date": start_date,
        "start_time": start_time,
        "due_date": due_date,
        "due_time": due_time,
        "reminder_at": validate_optional_datetime(merged.get("reminder_at"), "Reminder"),
        "repeat_rule": validate_repeat_rule(merged.get("repeat_rule")),
        "repeat_interval_days": validate_repeat_interval_days(merged.get("repeat_interval_days")),
        "repeat_until": validate_optional_date(merged.get("repeat_until"), "Repeat stop date"),
        "priority": validate_task_priority(merged.get("priority")),
        "location": limited_text(merged.get("location"), "Location", 500),
        "notes": limited_text(merged.get("notes"), "Notes", 20_000),
        "project_order": project_order,
        "google_sync_target": compact_text(current.get("google_sync_target")),
        "google_calendar_event_id": compact_text(current.get("google_calendar_event_id")),
        "google_calendar_event_link": compact_text(current.get("google_calendar_event_link")),
        "google_task_id": compact_text(current.get("google_task_id")),
        "google_task_link": compact_text(current.get("google_task_link")),
        "google_sync_hash": compact_text(current.get("google_sync_hash")),
        "google_synced_at": compact_text(current.get("google_synced_at")),
        "google_sync_error": compact_text(current.get("google_sync_error")),
        "completed": completed,
        "completed_at": completed_at,
        "created_at": current["created_at"],
        "updated_at": timestamp,
    }
    tasks_table.put_item(Item=item)
    updated = get_task(task_id)
    if updated["completed"] or not task_reminder_datetime(updated):
        delete_task_reminder_schedule(task_id)
    else:
        schedule_task_reminder(updated)
    auto_sync_task_after_save(updated, current)
    if completed and not current["completed"] and validate_repeat_rule(merged.get("repeat_rule")) != "none":
        create_next_repeating_task({**merged, "completed": False})
    return updated


def project_open_tasks(project_id: Any) -> List[Dict[str, Any]]:
    project = get_project(project_id)
    return [
        task
        for task in list_tasks({})
        if not task["completed"] and str(task.get("project_id") or "") == str(project["id"])
    ]


def reorder_project_tasks(project_id: Any, data: Dict[str, Any]) -> Dict[str, Any]:
    project = get_project(project_id)
    raw_ids = data.get("task_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValidationError("Task order must include task_ids.")
    task_ids = [compact_text(task_id) for task_id in raw_ids]
    if any(not task_id for task_id in task_ids) or len(task_ids) != len(set(task_ids)):
        raise ValidationError("Task order contains invalid task ids.")

    tasks = project_open_tasks(project["id"])
    task_map = {str(task["id"]): task for task in tasks}
    invalid_ids = [task_id for task_id in task_ids if task_id not in task_map]
    if invalid_ids:
        raise ValidationError("Only open tasks linked to this project can be reordered.")
    if set(task_ids) != set(task_map):
        raise ValidationError("Task order must include every open task in the project.")

    timestamp = now_iso()
    for index, task_id in enumerate(task_ids, start=1):
        task = {**task_map[task_id], "project_order": index * 10, "updated_at": timestamp}
        tasks_table.put_item(Item=task)
    return {"ok": True, "tasks": project_open_tasks(project["id"])}


def create_next_repeating_task(task: Dict[str, Any]) -> None:
    repeat_rule = validate_repeat_rule(task.get("repeat_rule"))
    if repeat_rule == "none":
        return
    start_date = validate_optional_date(task.get("start_date"), "Start date")
    base_date = validate_optional_date(task.get("due_date"), "Due date")
    repeat_base_date = base_date or start_date
    if repeat_base_date:
        next_due_date = next_repeat_date(repeat_base_date, repeat_rule, task.get("repeat_interval_days"))
    else:
        next_due_date = next_repeat_date(dt.date.today().isoformat(), repeat_rule, task.get("repeat_interval_days"))
    next_start_date = next_repeat_start_date(
        start_date,
        repeat_base_date or dt.date.today().isoformat(),
        next_due_date,
    )
    repeat_until = validate_optional_date(task.get("repeat_until"), "Repeat stop date")
    if repeat_until and next_due_date > repeat_until:
        return
    next_reminder = next_repeat_datetime(
        task.get("reminder_at"),
        repeat_rule,
        repeat_base_date or dt.date.today().isoformat(),
        next_due_date,
    )
    create_task(
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


def delete_task(task_id: str) -> None:
    task = get_task(task_id)
    auto_delete_google_for_task(task)
    response = tasks_table.delete_item(Key={"id": task_id})
    if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200:
        raise NotFoundError("Task not found.")
    delete_task_reminder_schedule(task_id)


def create_entry(data: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = now_iso()
    item_id = uuid.uuid4().hex
    entry_date = validate_entry_date(data.get("entry_date"))
    title = required_text(data, "title", "Title")
    what_i_did = required_text(data, "what_i_did", "What I did")
    source_mode = validate_source_mode(data.get("source_mode"))
    difficulty = validate_difficulty(data.get("difficulty"))
    project_id, project_name = resolve_project_fields(data)
    item = {
        "id": item_id,
        "entry_date": entry_date,
        "title": title,
        "what_i_did": what_i_did,
        "quick_note": compact_text(data.get("quick_note")),
        "project_id": project_id,
        "project": project_name,
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
        "project_id": data.get("project_id", ""),
        "project": data.get("project", ""),
        "skills_used": data.get("skills_used", []),
        "tags": data.get("tags", []),
    }
    return create_entry(payload)


def get_entry(entry_id: str) -> Dict[str, Any]:
    response = entries_table.get_item(Key={"id": entry_id}, ConsistentRead=True)
    item = response.get("Item")
    if item is None:
        raise NotFoundError("Work entry not found.")
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
    project_id, project_name = resolve_project_fields(merged)
    item = {
        "id": entry_id,
        "entry_date": entry_date,
        "title": title,
        "what_i_did": what_i_did,
        "quick_note": compact_text(merged.get("quick_note")),
        "project_id": project_id,
        "project": project_name,
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
    response = evidence_table.get_item(Key={"id": evidence_id}, ConsistentRead=True)
    item = response.get("Item")
    if item is None:
        raise NotFoundError("Evidence not found.")
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
        raise NotFoundError("Evidence not found.")


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


def item_matches_project(item: Dict[str, Any], project: Dict[str, Any]) -> bool:
    project_id = compact_text(project.get("id"))
    project_name = compact_text(project.get("name")).lower()
    return (
        compact_text(item.get("project_id")) == project_id
        or compact_text(item.get("project")).lower() == project_name
    )


def project_context(project: Dict[str, Any]) -> Dict[str, Any]:
    tasks = [task for task in list_tasks({}) if item_matches_project(task, project)]
    entries = [entry for entry in list_entries({}) if item_matches_project(entry, project)]
    achievements = [
        item
        for item in list_achievements({})
        if compact_text(item.get("project")).lower() == compact_text(project.get("name")).lower()
    ]
    entry_ids = {str(entry["id"]) for entry in entries}
    evidence = [
        item
        for item in list_evidence({})
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
    return {"title": title, "guidance": guidance, "notes": guidance}


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
            "guidance": "List the smallest useful actions you can finish next, then add only the first one or two to Planner.",
        },
        {
            "title": "Capture project evidence",
            "guidance": "Save a screenshot, file, or note that proves what changed so the CV log has useful detail later.",
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


def extract_project_suggestions_from_payload(payload: Dict[str, Any]) -> List[Dict[str, str]]:
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


def suggest_project_next_steps(project_id: Any) -> List[Dict[str, str]]:
    project = get_project(project_id)
    context = project_context(project)
    config = read_openai_config()
    if config.get("api_key"):
        try:
            return generate_llm_project_suggestions(project, context, config)
        except Exception:
            return fallback_project_suggestions(project, context)
    return fallback_project_suggestions(project, context)


def get_options() -> Dict[str, Any]:
    entries = build_entries_payload(scan_table(entries_table), scan_table(evidence_table))
    tasks = [row_to_task(item) for item in scan_table(tasks_table)]
    projects = list_projects()
    return build_options_payload(entries, tasks, projects)


def build_options_payload(
    entries: List[Dict[str, Any]],
    tasks: List[Dict[str, Any]],
    projects: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    project_records = projects or []
    projects = sorted(
        {
            *{project["name"] for project in project_records if project.get("name")},
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
    ensure_projects_from_existing_labels()
    try:
        retry_google_sync(include_all=False)
    except Exception:
        pass
    task_items = scan_table(tasks_table)
    entry_items = scan_table(entries_table)
    evidence_items = scan_table(evidence_table)
    tasks = [row_to_task(item) for item in task_items]
    tasks.sort(key=task_sort_key)
    entries = build_entries_payload(entry_items, evidence_items)
    evidence = build_evidence_payload(entry_items, evidence_items)
    achievements = list_achievements()
    projects = list_projects()
    return {
        "tasks": tasks,
        "entries": entries,
        "evidence": evidence,
        "achievements": achievements,
        "projects": projects,
        "options": build_options_payload(entries, tasks, projects),
        "google_integration": google_status(),
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
    project_match = re.match(r"^/api/projects/([^/]+)$", path)
    project_complete_match = re.match(r"^/api/projects/([^/]+)/complete$", path)
    project_reorder_match = re.match(r"^/api/projects/([^/]+)/tasks/reorder$", path)
    project_suggestions_match = re.match(r"^/api/projects/([^/]+)/suggestions$", path)

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
    if method == "GET" and path == "/api/integrations/google/status":
        return google_status()
    if method == "POST" and path == "/api/integrations/google/connect":
        return start_google_connect(body)
    if method == "GET" and path == "/api/integrations/google/callback":
        return complete_google_connect(query)
    if method == "POST" and path == "/api/integrations/google/retry":
        return retry_google_connection()
    if method == "POST" and path == "/api/integrations/google/disconnect":
        return disconnect_google()
    if method == "GET" and path == "/api/achievements":
        return list_achievements(query)
    if method == "GET" and path == "/api/projects":
        return list_projects()
    if method == "POST" and path == "/api/projects":
        return create_project(body)
    if method == "POST" and project_complete_match:
        return complete_project(project_complete_match.group(1))
    if method == "POST" and project_reorder_match:
        return reorder_project_tasks(project_reorder_match.group(1), body)
    if method == "GET" and project_match:
        return get_project(project_match.group(1))
    if method == "PUT" and project_match:
        return update_project(project_match.group(1), body)
    if method == "DELETE" and project_match:
        delete_project(project_match.group(1))
        return {"ok": True}
    if method == "POST" and project_suggestions_match:
        return suggest_project_next_steps(project_suggestions_match.group(1))
    if method == "GET" and path == "/api/push/public-key":
        config = read_vapid_config()
        return {
            "publicKey": config["public_key"],
            "configured": bool(config["public_key"] and config["private_key"] and webpush is not None),
            "privateKeyConfigured": bool(config["private_key"]),
            "webpushInstalled": webpush is not None,
        }
    if method == "GET" and path == "/api/push/status":
        return get_push_status()
    if method == "POST" and path == "/api/push/subscribe":
        return save_push_subscription(body)
    if method == "POST" and path == "/api/push/unsubscribe":
        return delete_push_subscription(body)
    if method == "POST" and path == "/api/push/test":
        return send_push_payload(build_test_push_payload())
    if method == "POST" and path == "/api/reminders/sync":
        return sync_task_reminder_schedules()
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
    raise NotFoundError("Route not found.")


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    request_id = compact_text(getattr(context, "aws_request_id", ""))
    is_reminder_event = False
    try:
        if not isinstance(event, dict):
            raise ValidationError("Lambda event must be an object.")
        is_reminder_event = event.get("source") == "work-diary.reminders"
        if is_reminder_event:
            return handle_reminder_event(event)

        request_context = event.get("requestContext") or {}
        http_context = request_context.get("http") if isinstance(request_context, dict) else {}
        http_context = http_context if isinstance(http_context, dict) else {}
        method = compact_text(http_context.get("method")).upper()
        path = compact_text(event.get("rawPath"))
        raw_headers = event.get("headers") or {}
        if not isinstance(raw_headers, dict):
            raise ValidationError("Request headers are invalid.")
        headers = {str(key).lower(): value for key, value in raw_headers.items()}
        if method == "OPTIONS":
            return response(204, {})
        if path not in PUBLIC_API_PATHS and not is_authenticated(headers):
            return response(401, {"error": "Login required."})

        body = parse_json_body(event) if method in {"POST", "PUT"} else {}
        query = parse_query(event)
        result = route_api(method, path, query, body)
        if isinstance(result, dict) and "_html" in result:
            return html_response(200, result["_html"])
        return response(200, result)
    except ValidationError as exc:
        if is_reminder_event:
            return {"ok": False, "error": str(exc)}
        return response(400, {"error": str(exc)})
    except NotFoundError as exc:
        if is_reminder_event:
            return {"ok": False, "error": str(exc).strip("'")}
        return response(404, {"error": str(exc).strip("'")})
    except OpenAIRequestError:
        logger.warning("OpenAI request failed: request_id=%s", request_id or "unknown", exc_info=True)
        return response(502, {"error": "AI service request failed. Please try again."})
    except GoogleIntegrationError:
        logger.warning("Google integration request failed: request_id=%s", request_id or "unknown", exc_info=True)
        return response(
            502,
            {"error": "Google integration request failed. Please reconnect or try again."},
        )
    except json.JSONDecodeError:
        return response(400, {"error": "Request body must be valid JSON."})
    except Exception:
        logger.exception(
            "%s failed: request_id=%s",
            "Reminder event" if is_reminder_event else "Unhandled API request",
            request_id or "unknown",
        )
        payload = {"error": "Unexpected server error."}
        if request_id:
            payload["request_id"] = request_id
        if is_reminder_event:
            return {"ok": False, **payload}
        return response(500, payload)


class ValidationError(Exception):
    pass


class NotFoundError(KeyError):
    pass


class OpenAIRequestError(Exception):
    pass


class GoogleIntegrationError(Exception):
    pass
