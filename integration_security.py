"""Security helpers shared by local and cloud integration flows."""

from __future__ import annotations

import datetime as dt
from typing import Optional
from urllib.parse import urljoin, urlparse


OAUTH_STATE_MAX_AGE_SECONDS = 10 * 60


def safe_return_url(requested: str, frontend_url: str) -> str:
    """Keep OAuth redirects on the configured frontend origin."""

    fallback = str(frontend_url or "/").strip() or "/"
    requested = str(requested or "").strip()
    if not requested:
        return fallback

    frontend = urlparse(fallback)
    candidate = urlparse(requested)
    if frontend.scheme in {"http", "https"} and frontend.netloc:
        if not candidate.scheme and not candidate.netloc and requested.startswith("/"):
            return urljoin(fallback, requested)
        if (
            candidate.scheme == frontend.scheme
            and candidate.netloc == frontend.netloc
        ):
            return requested
        return fallback

    return requested if requested.startswith("/") and not requested.startswith("//") else fallback


def oauth_state_is_fresh(
    created_at: str,
    *,
    now: Optional[dt.datetime] = None,
    max_age_seconds: int = OAUTH_STATE_MAX_AGE_SECONDS,
) -> bool:
    if not created_at:
        return False
    try:
        created = dt.datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=dt.timezone.utc)
    current = now or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=dt.timezone.utc)
    age = (current.astimezone(dt.timezone.utc) - created.astimezone(dt.timezone.utc)).total_seconds()
    return 0 <= age <= max_age_seconds
