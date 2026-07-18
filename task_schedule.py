"""Shared task scheduling rules for local and AWS runtimes.

Keeping date/time parsing here prevents the SQLite and DynamoDB implementations
from drifting apart. The rest of each backend can work with the validated,
immutable ``TaskSchedule`` value object instead of reinterpreting raw strings.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


class TaskScheduleValidationError(ValueError):
    """Raised when a task contains an invalid or backwards schedule."""


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _optional_date(value: Any, label: str) -> str:
    text = _clean(value)
    if not text:
        return ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise TaskScheduleValidationError(
            f"{label} must use YYYY-MM-DD format."
        )
    try:
        dt.date.fromisoformat(text)
    except ValueError as exc:
        raise TaskScheduleValidationError(
            f"{label} must use YYYY-MM-DD format."
        ) from exc
    return text


def _optional_time(value: Any, label: str) -> str:
    text = _clean(value)
    if not text:
        return ""
    if not re.fullmatch(r"\d{2}:\d{2}", text):
        raise TaskScheduleValidationError(f"{label} must use HH:MM format.")
    try:
        dt.time.fromisoformat(text)
    except ValueError as exc:
        raise TaskScheduleValidationError(f"{label} must use HH:MM format.") from exc
    return text


@dataclass(frozen=True)
class TaskSchedule:
    start_date: str = ""
    start_time: str = ""
    end_date: str = ""
    end_time: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "TaskSchedule":
        schedule = cls(
            start_date=_optional_date(data.get("start_date"), "Start date"),
            start_time=_optional_time(data.get("start_time"), "Start time"),
            end_date=_optional_date(data.get("due_date"), "End date"),
            end_time=_optional_time(data.get("due_time"), "End time"),
        )
        schedule._validate_order()
        return schedule

    def _validate_order(self) -> None:
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise TaskScheduleValidationError(
                "Start date must be on or before end date."
            )

        effective_start_date = self.start_date or self.end_date
        effective_end_date = self.end_date or self.start_date
        same_day = effective_start_date == effective_end_date
        if (
            self.start_time
            and self.end_time
            and same_day
            and self.start_time > self.end_time
        ):
            raise TaskScheduleValidationError(
                "Start time must be on or before end time."
            )

    def timed_bounds(self, default_minutes: int = 30) -> Optional[Tuple[dt.datetime, dt.datetime]]:
        """Return calendar bounds, or ``None`` for an all-day schedule.

        Any dated task can sync to Google Calendar. A lone end time retains the
        historical 30-minute event behavior; a start time turns the task into
        an explicit interval and preserves a later end date when supplied.
        """

        event_date = self.end_date or self.start_date
        if not event_date or not (self.start_time or self.end_time):
            return None
        start_day = (self.start_date or event_date) if self.start_time else event_date
        start_clock = self.start_time or self.end_time
        start = dt.datetime.fromisoformat(f"{start_day}T{start_clock}:00")
        if self.start_time:
            end_clock = self.end_time or self.start_time
            end = dt.datetime.fromisoformat(f"{event_date}T{end_clock}:00")
        else:
            end = start + dt.timedelta(minutes=default_minutes)
        # Be defensive with legacy records created before range validation.
        if end <= start:
            end = start + dt.timedelta(minutes=default_minutes)
        return start, end
