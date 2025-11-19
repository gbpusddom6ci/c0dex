import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

WEEKDAY_TOKENS = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
MONTH_LOOKUP = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}

TIME_AMPM_RE = re.compile(r"^\d{1,2}:\d{2}(am|pm)$", re.IGNORECASE)
TIME_24_RE = re.compile(r"^\d{1,2}:\d{2}$")


@dataclass
class CalendarEvent:
    date: str
    weekday: str
    currency: str
    title: str
    time_label: str
    time_24h: Optional[str]
    values: Dict[str, Optional[str]]


def parse_calendar_markdown(
    text: str,
    *,
    year: int,
) -> List[Dict[str, Any]]:
    lines = [line.rstrip() for line in text.splitlines()]
    idx = 0
    days: List[Dict[str, any]] = []

    while True:
        idx, token = _next_non_empty(lines, idx)
        if token is None:
            break

        if token not in WEEKDAY_TOKENS:
            idx += 1
            continue

        weekday = token
        idx, date_token = _next_non_empty(lines, idx + 1)
        if date_token is None:
            break

        if date_token in WEEKDAY_TOKENS:
            # Malformed block; skip to next token
            idx = idx + 1
            weekday = date_token
            continue

        date_iso = _parse_date_token(date_token, year)
        events: List[CalendarEvent] = []
        idx = (idx + 1) if idx is not None else idx

        while True:
            idx, token = _next_non_empty(lines, idx)
            if token is None or token in WEEKDAY_TOKENS:
                break

            if not _is_time_token(token):
                idx += 1
                continue

            time_label = token
            time_24h = _convert_time_to_24h(time_label)
            idx += 1

            while True:
                peek_idx, candidate = _next_non_empty(lines, idx)
                if candidate is None or candidate in WEEKDAY_TOKENS or _is_time_token(candidate):
                    idx = peek_idx
                    break

                currency = candidate
                idx = peek_idx + 1

                title_idx, title_token = _next_non_empty(lines, idx)
                if title_token is None or title_token in WEEKDAY_TOKENS or _is_time_token(title_token):
                    idx = title_idx
                    continue

                title = title_token
                idx = title_idx + 1

                values_idx, value_token = _next_non_empty(lines, idx)
                values_line: Optional[str] = None
                if (
                    value_token is not None
                    and value_token not in WEEKDAY_TOKENS
                    and not _is_time_token(value_token)
                    and _looks_like_values(value_token)
                ):
                    values_line = value_token
                    idx = values_idx + 1
                else:
                    idx = values_idx

                event = CalendarEvent(
                    date=date_iso,
                    weekday=weekday,
                    currency=currency,
                    title=title,
                    time_label=time_label,
                    time_24h=time_24h,
                    values=_parse_values(values_line),
                )
                events.append(event)

        days.append(
            {
                "date": date_iso,
                "weekday": weekday,
                "events": [event.__dict__ for event in events],
            }
        )

    return days


def to_json_document(
    days: List[Dict[str, any]],
    *,
    year: int,
    timezone: str,
    source: str = "markdown_import",
) -> Dict[str, Any]:
    total_events = sum(len(day["events"]) for day in days)
    return {
        "meta": {
            "source": source,
            "assumptions": {
                "year": year,
                "time_zone": timezone,
                "value_columns_order": ["actual", "forecast", "previous"],
                "two_value_rule": "When only two values are present, interpret as (actual, previous).",
            },
            "counts": {
                "days": len(days),
                "events": total_events,
            },
        },
        "days": days,
    }


def _next_non_empty(lines: List[str], start: int) -> Tuple[int, Optional[str]]:
    i = start
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped:
            return i, stripped
        i += 1
    return len(lines), None


def _parse_date_token(token: str, year: int) -> str:
    parts = token.split()
    if len(parts) != 2:
        raise ValueError(f"Tarih satırı çözümlenemedi: {token}")
    month_raw, day_raw = parts
    month = MONTH_LOOKUP.get(month_raw[:3].title())
    if month is None:
        raise ValueError(f"Bilinmeyen ay: {month_raw}")
    day = int(day_raw)
    dt = datetime(year=year, month=month, day=day)
    return dt.strftime("%Y-%m-%d")


def _is_time_token(token: str) -> bool:
    lowered = token.lower()
    if lowered in {"all day", "tentative"}:
        return True
    if TIME_AMPM_RE.match(lowered):
        return True
    if TIME_24_RE.match(token):
        return True
    return False


def _convert_time_to_24h(time_label: str) -> Optional[str]:
    lowered = time_label.lower()
    if lowered in {"all day", "tentative"}:
        return None
    if TIME_AMPM_RE.match(lowered):
        dt = datetime.strptime(lowered, "%I:%M%p")
        return dt.strftime("%H:%M")
    if TIME_24_RE.match(time_label):
        hours, minutes = time_label.split(":")
        return f"{int(hours):02d}:{int(minutes):02d}"
    return None


def _looks_like_values(token: str) -> bool:
    text = token.replace(",", "").strip()
    if not text:
        return False
    if any(ch.isdigit() for ch in text):
        return True
    lowered = text.lower()
    if lowered in {"n/a", "na", "--"}:
        return True
    if any(symbol in text for symbol in ["%", "|", "/", "k", "m", "b"]):
        return True
    return False


def _parse_values(line: Optional[str]) -> Dict[str, Optional[str]]:
    actual: Optional[str] = None
    forecast: Optional[str] = None
    previous: Optional[str] = None

    if line:
        tokens = [tok for tok in re.split(r"\s+", line.strip()) if tok]
        if len(tokens) == 1:
            actual = tokens[0]
        elif len(tokens) == 2:
            actual, previous = tokens
        elif len(tokens) >= 3:
            actual, forecast, previous = tokens[:3]

    return {"actual": actual, "forecast": forecast, "previous": previous}
