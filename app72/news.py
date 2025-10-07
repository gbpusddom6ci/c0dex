"""ForexFactory news integration helpers for app72.

The IOU workflow uses this module to fetch ForexFactory calendar events and
highlight mid/high impact items that fall into each 72m candle.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - fallback for very old interpreters
    ZoneInfo = None  # type: ignore


NY_TZ = ZoneInfo("America/New_York") if ZoneInfo else None
IMPACT_WHITELIST = {"high", "medium"}
DEFAULT_BASE_URL = "https://nfs.faireconomy.media"
DEFAULT_CACHE_DIR = Path(os.environ.get("APP72_NEWS_CACHE_DIR", Path.home() / ".cache" / "app72_news"))
USER_AGENT = "app72-news/1.0 (+https://www.forexfactory.com/)"
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.8",
    "Referer": "https://www.forexfactory.com/calendar",
    "Origin": "https://www.forexfactory.com",
}


@dataclass(frozen=True)
class NewsEvent:
    """Normalized ForexFactory calendar entry."""

    start: datetime
    impact: str
    title: str
    country: str
    url: str

    def label(self) -> str:
        """Human friendly label used in IOU tables."""
        time_part = self.start.strftime("%m-%d %H:%M")
        impact_lower = self.impact.lower()
        suffix = f"{self.title} ({self.country})"
        return f"{impact_lower}: {suffix} @ {time_part}"


class NewsService:
    """Small helper around ForexFactory JSON feeds with on-disk caching."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        impacts: Iterable[str] = IMPACT_WHITELIST,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[str] = None,
        retry_attempts: int = 3,
        retry_delay: float = 5.0,
        timeout: float = 10.0,
        verbose: bool = False,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._cache_dir = cache_dir
        self._impacts = {i.lower() for i in impacts}
        combined_headers = dict(DEFAULT_HEADERS)
        if headers:
            for key, value in headers.items():
                if value is None:
                    continue
                combined_headers[str(key)] = str(value)
        self._headers = combined_headers
        cookie_source = cookies if cookies is not None else os.environ.get("APP72_NEWS_COOKIES", "")
        self._cookies = cookie_source.strip() or None
        self._retry_attempts = max(1, int(retry_attempts))
        self._retry_delay = max(0.0, float(retry_delay))
        self._timeout = max(1.0, float(timeout))
        self._verbose = bool(verbose)
        self._week_cache: Dict[date, List[NewsEvent]] = {}
        self._last_error: Optional[str] = None

    # ------------------------- public API -------------------------
    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def prefetch_week(self, week_start: date, force: bool = False) -> Tuple[int, bool]:
        normalized = _normalize_week_start(week_start)
        if not force and normalized in self._week_cache:
            self._last_error = None
            return len(self._week_cache[normalized]), True
        if force:
            self._week_cache.pop(normalized, None)
        events = self._load_week(normalized, force=force)
        if self._last_error is None:
            self._week_cache[normalized] = events
        return len(events), False

    def lookup_range(self, start: datetime, end: datetime) -> List[NewsEvent]:
        """Return whitelisted events that fall inside [start, end)."""

        if start >= end:
            return []
        self._ensure_range(start, end)
        hits: List[NewsEvent] = []
        for week_start in _week_starts_between(start.date(), (end - timedelta(minutes=1)).date()):
            normalized = _normalize_week_start(week_start)
            for event in self._week_cache.get(normalized, []):
                if start <= event.start < end:
                    hits.append(event)
        return hits

    # ----------------------- internal helpers ---------------------
    def _ensure_range(self, start: datetime, end: datetime) -> None:
        for week_start in _week_starts_between(start.date(), (end - timedelta(minutes=1)).date()):
            normalized = _normalize_week_start(week_start)
            if normalized not in self._week_cache:
                events = self._load_week(normalized)
                self._week_cache[normalized] = events

    def _load_week(self, week_start: date, force: bool = False) -> List[NewsEvent]:
        normalized = _normalize_week_start(week_start)
        if normalized < date(1970, 1, 1):  # sanity guard
            self._last_error = "week-before-1970"
            return []
        cache_key = normalized.strftime("%Y%m%d")
        cache_path: Optional[Path] = None
        if self._cache_dir:
            cache_path = self._cache_dir / f"week-{cache_key}.json"
            if force and cache_path.exists():
                try:
                    cache_path.unlink()
                except Exception:
                    pass
        if cache_path and cache_path.is_file():
            try:
                data = json.loads(cache_path.read_text("utf-8"))
                self._last_error = None
                return _parse_events(data, self._impacts)
            except Exception:
                pass
        data = self._fetch_week_payload(normalized)
        if data is None:
            return []
        events = _parse_events(data, self._impacts)
        if cache_path:
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass
        return events

    def _fetch_week_payload(self, week_start: date) -> Optional[List[Dict[str, object]]]:
        week_diff = _diff_weeks(week_start, datetime.now().date())
        url = self._resolve_url(week_start, week_diff)
        if url is None:
            self._last_error = "no-url"
            return None
        headers = dict(self._headers)
        if self._cookies:
            headers["Cookie"] = self._cookies
        for attempt in range(self._retry_attempts):
            try:
                self._log(f"GET {url} [{attempt + 1}/{self._retry_attempts}]")
                req = Request(url, headers=headers)
                with urlopen(req, timeout=self._timeout) as resp:
                    raw = resp.read()
                data = json.loads(raw.decode("utf-8"))
            except HTTPError as exc:
                self._last_error = f"http-{exc.code}"
                if exc.code == 404:
                    return []
                if exc.code in (403, 429) and attempt + 1 < self._retry_attempts:
                    time.sleep(self._retry_delay)
                    continue
                return None
            except URLError as exc:
                self._last_error = f"url-error: {exc.reason}"
                if attempt + 1 < self._retry_attempts:
                    time.sleep(self._retry_delay)
                    continue
                return None
            except Exception as exc:
                self._last_error = f"fetch-error: {exc}"
                return None
            else:
                self._last_error = None
                return data
        return None

    def _resolve_url(self, week_start: date, week_diff: int) -> Optional[str]:
        # Direct shortcuts for nearby weeks first.
        shortcuts = {
            0: "ff_calendar_thisweek.json",
            1: "ff_calendar_nextweek.json",
            -1: "ff_calendar_lastweek.json",
        }
        key = shortcuts.get(week_diff)
        if key:
            return f"{self._base_url}/{key}"
        # Fallback to query parameter endpoint if available.
        return f"{self._base_url}/ff_calendar_thisweek.json?date={week_start.isoformat()}"

    def _log(self, message: str) -> None:
        if self._verbose:
            print(message)


# --------------------------- utilities ----------------------------


def _normalize_week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _parse_events(payload: List[Dict[str, object]], impacts: Iterable[str]) -> List[NewsEvent]:
    if not payload:
        return []
    impact_filter = {i.lower() for i in impacts}
    if ZoneInfo and NY_TZ:
        tz = NY_TZ
    else:
        tz = None
    events: List[NewsEvent] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        impact_raw = str(item.get("impact", "")).strip()
        if not impact_raw:
            continue
        impact_norm = impact_raw.split()[0].lower()
        if impact_norm not in impact_filter:
            continue
        date_raw = item.get("date")
        if not isinstance(date_raw, str):
            continue
        try:
            dt = datetime.fromisoformat(date_raw)
        except ValueError:
            continue
        if dt.tzinfo is not None and tz is not None:
            dt = dt.astimezone(tz).replace(tzinfo=None)
        else:
            dt = dt.replace(tzinfo=None)
        title = str(item.get("title", "")).strip() or "(no title)"
        country = str(item.get("country", "")).strip() or "-"
        url = str(item.get("url", "")).strip()
        events.append(
            NewsEvent(
                start=dt,
                impact=impact_norm,
                title=title,
                country=country,
                url=url,
            )
        )
    return events


def _week_starts_between(start: date, end: date) -> List[date]:
    if end < start:
        start, end = end, start
    result: List[date] = []
    cursor = _normalize_week_start(start)
    end_cursor = _normalize_week_start(end)
    while cursor <= end_cursor:
        result.append(cursor)
        cursor += timedelta(days=7)
    return result


def _diff_weeks(target: date, today: date) -> int:
    delta_days = (target - (today - timedelta(days=today.weekday()))).days
    return delta_days // 7
