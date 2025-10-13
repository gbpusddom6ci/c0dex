import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

NEWS_FILE_PATTERN = "*.json"
NEWS_DIR_NAME = "economic_calendar"
_NEWS_CACHE: List[Dict[str, Any]] = []
_NEWS_CACHE_KEY: Optional[Tuple[Tuple[str, int, int], ...]] = None


def _gather_news_files(base_dir: Path) -> List[Path]:
    """
    Collect news JSON files from the dedicated economic_calendar directory.
    Falls back to root-level JSON files so existing drops continue to work.
    """
    candidates: List[Path] = []
    calendar_dir = base_dir / NEWS_DIR_NAME
    if calendar_dir.exists():
        candidates.extend(p for p in calendar_dir.glob(NEWS_FILE_PATTERN) if p.is_file())
    candidates.extend(p for p in base_dir.glob(NEWS_FILE_PATTERN) if p.is_file())

    seen = set()
    unique: List[Path] = []
    for path in sorted(candidates):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _build_news_cache_key(files: List[Path]) -> Tuple[Tuple[str, int, int], ...]:
    key_parts: List[Tuple[str, int, int]] = []
    for path in files:
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        key_parts.append((str(path.resolve()), int(stat.st_mtime_ns), stat.st_size))
    return tuple(key_parts)


def load_news_events() -> List[Dict[str, Any]]:
    """
    Load ForexFactory-style news events from JSON files. Each file must provide a
    top-level `days` list whose entries expose `date` and an `events` list. Every
    event should include `time_24h` (HH:MM) and `title`. The loader caches results
    and refreshes automatically when files change.
    """
    base_dir = Path(__file__).resolve().parent
    files = _gather_news_files(base_dir)
    cache_key = _build_news_cache_key(files)

    global _NEWS_CACHE, _NEWS_CACHE_KEY
    if cache_key == _NEWS_CACHE_KEY:
        return _NEWS_CACHE

    events: List[Dict[str, Any]] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue

        days = payload.get("days")
        if not isinstance(days, list):
            continue

        for day in days:
            if not isinstance(day, dict):
                continue
            date_str = day.get("date")
            event_list = day.get("events") or []

            if not date_str or not isinstance(event_list, list):
                continue

            for event in event_list:
                if not isinstance(event, dict):
                    continue

                title = (event.get("title") or "").strip()
                if not title:
                    continue

                raw_time = event.get("time_24h")
                if not (isinstance(raw_time, str) and raw_time.strip()):
                    for alt_key in ("time", "time_text", "time_label", "session"):
                        alt_val = event.get(alt_key)
                        if isinstance(alt_val, str) and alt_val.strip():
                            raw_time = alt_val
                            break

                time_str = (raw_time or "").strip()
                lowered = time_str.lower()
                is_all_day = bool(event.get("all_day"))
                if not is_all_day and lowered in {"all day", "all-day"}:
                    is_all_day = True

                if is_all_day:
                    try:
                        event_ts = datetime.strptime(date_str, "%Y-%m-%d")
                    except ValueError:
                        continue
                    display_time = "All Day"
                else:
                    if not (len(time_str) == 5 and time_str[2] == ":"):
                        continue
                    try:
                        event_ts = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
                    except ValueError:
                        continue
                    display_time = time_str

                values = event.get("values") or {}

                def _is_null_value(val: Any) -> bool:
                    if val is None:
                        return True
                    if isinstance(val, str):
                        return val.strip().lower() == "null" or val.strip() == ""
                    return False

                has_null_value = _is_null_value(values.get("actual"))

                events.append(
                    {
                        "timestamp": event_ts,
                        "date": event_ts.date(),
                        "time": display_time,
                        "title": title,
                        "has_null_value": has_null_value,
                        "all_day": is_all_day,
                    }
                )

    events.sort(key=lambda item: item["timestamp"])
    _NEWS_CACHE = events
    _NEWS_CACHE_KEY = cache_key
    return events


def find_news_for_timestamp(
    ts: datetime,
    duration_minutes: int,
    null_back_minutes: int = 0,
) -> List[Dict[str, Any]]:
    """
    Return news events that fall within the inclusive start / exclusive end window
    of the candle that begins at `ts`. If `null_back_minutes` is provided, recent
    news entries whose actual values are missing/NULL are also returned when they
    occurred within the previous `null_back_minutes`.
    """
    events = load_news_events()
    if not events:
        return []

    window_end = ts + timedelta(minutes=duration_minutes)
    null_window_start = ts - timedelta(minutes=max(0, null_back_minutes))

    matches: List[Dict[str, Any]] = []
    for event in events:
        event_ts = event["timestamp"]
        if event.get("all_day"):
            if event_ts.date() == ts.date():
                matches.append({**event, "window": "all-day"})
            continue
        if ts <= event_ts < window_end:
            matches.append({**event, "window": "forward"})
            continue
        if (
            null_back_minutes > 0
            and event.get("has_null_value")
            and null_window_start <= event_ts < ts
        ):
            matches.append({**event, "window": "recent-null"})

    matches.sort(key=lambda item: item["timestamp"])
    return matches
