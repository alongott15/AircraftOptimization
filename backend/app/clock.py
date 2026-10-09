"""Injectable clock (CLAUDE.md rule 8). Domain code never calls datetime.now() itself."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(tz=UTC)


class FrozenClock:
    """Fixed 'now' for tests and demos (APP_FROZEN_NOW)."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock requires a timezone-aware datetime")
        self._at = at.astimezone(UTC)

    def now(self) -> datetime:
        return self._at

    def advance(self, delta: timedelta) -> None:
        self._at = self._at + delta

    def set(self, at: datetime) -> None:
        self._at = at.astimezone(UTC)


def parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 string (accepts trailing Z) into an aware UTC datetime."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
