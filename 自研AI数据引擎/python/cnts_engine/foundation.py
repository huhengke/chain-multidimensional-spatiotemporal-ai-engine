from __future__ import annotations

from datetime import date, timedelta


def normalize_time_phrase(phrase: str, reference_date: date) -> tuple[date, date]:
    """Translate a small deterministic time vocabulary into an exact date range."""
    normalized = phrase.strip()
    week_start = reference_date - timedelta(days=reference_date.weekday())
    if normalized == "本周":
        return week_start, week_start + timedelta(days=6)
    if normalized == "上周":
        start = week_start - timedelta(days=7)
        return start, start + timedelta(days=6)
    if normalized == "今天":
        return reference_date, reference_date
    if normalized == "昨天":
        value = reference_date - timedelta(days=1)
        return value, value
    raise ValueError(f"unsupported time phrase: {phrase}")
