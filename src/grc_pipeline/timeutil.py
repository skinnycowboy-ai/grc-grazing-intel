from __future__ import annotations

from datetime import UTC, date, datetime


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def parse_date(s: str) -> date:
    return date.fromisoformat(s)


def date_iso(d: date) -> str:
    return d.isoformat()
