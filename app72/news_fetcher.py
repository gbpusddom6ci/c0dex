import csv
import gzip
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


BASE_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CSV_BASE_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.csv"
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; app72-news/1.0)",
    "Accept": "application/json",
    "Accept-Encoding": "gzip",
    "Referer": "https://www.forexfactory.com/calendar",
}
MAX_SLEEP_ON_RETRY = 60

IMPACT_PRIORITY: Dict[str, int] = {
    "": 0,
    "holiday": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
}


@dataclass
class CalendarEvent:
    timestamp: datetime
    title: str
    country: str
    impact: str
    actual: str
    forecast: str
    previous: str


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _format_week_param(d: date) -> str:
    return d.strftime("%Y%m%d")


def _decode_response(data: bytes, encoding: Optional[str]) -> List[dict]:
    payload = gzip.decompress(data) if encoding == "gzip" else data
    text = payload.decode("utf-8")
    if text.lstrip().startswith("<"):
        raise RateLimitError("rate limited (html payload)")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover
        raise RuntimeError("ForexFactory JSON çözümlenemedi") from exc


def _decode_csv(data: bytes, encoding: Optional[str]) -> List[dict]:
    payload = gzip.decompress(data) if encoding == "gzip" else data
    text = payload.decode("utf-8")
    if text.lstrip().startswith("<"):
        raise RateLimitError("rate limited (html payload)")
    reader = csv.DictReader(text.splitlines())
    return list(reader)


class RateLimitError(RuntimeError):
    pass


def _request_week_json(week: date, *, retries: int = 3) -> List[dict]:
    url = f"{BASE_URL}?week={_format_week_param(week)}"
    delay = 2
    for attempt in range(retries):
        try:
            req = Request(url, headers=DEFAULT_HEADERS)
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
                encoding = resp.headers.get("Content-Encoding")
                return _decode_response(raw, encoding)
        except HTTPError as exc:
            if exc.code == 429:
                raise RateLimitError(f"rate limited for {url} retry_after={exc.headers.get('Retry-After')}")
            if 500 <= exc.code < 600 and attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, MAX_SLEEP_ON_RETRY)
                continue
            raise RuntimeError(f"ForexFactory haftası alınamadı: {url} (HTTP {exc.code})") from exc
        except URLError as exc:
            if attempt >= retries - 1:
                raise RuntimeError(f"ForexFactory isteği başarısız: {exc}") from exc
            time.sleep(delay)
            delay = min(delay * 2, MAX_SLEEP_ON_RETRY)
    raise RuntimeError(f"ForexFactory haftası alınamadı: {url}")


def _request_week_csv(week: date, *, retries: int = 2) -> List[dict]:
    url = f"{CSV_BASE_URL}?week={_format_week_param(week)}"
    delay = 2
    for attempt in range(retries):
        try:
            req = Request(url, headers={**DEFAULT_HEADERS, "Accept": "text/csv"})
            with urlopen(req, timeout=30) as resp:
                raw = resp.read()
                encoding = resp.headers.get("Content-Encoding")
                return _decode_csv(raw, encoding)
        except HTTPError as exc:
            if exc.code == 429:
                raise RateLimitError(f"rate limited for {url} retry_after={exc.headers.get('Retry-After')}")
            if 500 <= exc.code < 600 and attempt < retries - 1:
                time.sleep(delay)
                delay = min(delay * 2, MAX_SLEEP_ON_RETRY)
                continue
            raise RuntimeError(f"ForexFactory CSV alınamadı: {url} (HTTP {exc.code})") from exc
        except URLError as exc:
            if attempt >= retries - 1:
                raise RuntimeError(f"ForexFactory CSV isteği başarısız: {exc}") from exc
            time.sleep(delay)
            delay = min(delay * 2, MAX_SLEEP_ON_RETRY)
    raise RuntimeError(f"ForexFactory CSV alınamadı: {url}")


def _request_week(week: date, *, retries: int = 3) -> List[dict]:
    try:
        return _request_week_json(week, retries=retries)
    except RateLimitError as exc_json:
        try:
            csv_payload = _request_week_csv(week)
        except RateLimitError as exc_csv:
            raise RateLimitError(
                f"ForexFactory rate limit aşıldı (week={_format_week_param(week)})"
            ) from exc_csv
        for item in csv_payload:
            date_str = (item.get("Date") or "").strip()
            time_str = (item.get("Time") or "").strip()
            if not date_str or not time_str:
                item["date"] = None
                continue
            lower_time = time_str.lower()
            if lower_time in {"all day", "tentative"}:
                item["date"] = None
                continue
            try:
                dt = datetime.strptime(f"{date_str} {time_str}", "%m-%d-%Y %I:%M%p")
            except ValueError:
                item["date"] = None
                continue
            if ZoneInfo is not None:
                dt = dt.replace(tzinfo=ZoneInfo("America/New_York"))
            item["date"] = dt.isoformat()
            item.setdefault("title", item.get("Title"))
            item.setdefault("country", item.get("Country"))
            item.setdefault("impact", item.get("Impact"))
            item.setdefault("forecast", item.get("Forecast"))
            item.setdefault("previous", item.get("Previous"))
        return csv_payload


def _to_event(item: dict, target_tz: timezone) -> Optional[CalendarEvent]:
    dt_raw = item.get("date") or item.get("timestamp")
    if not dt_raw:
        return None
    dt_val: Optional[datetime] = None
    if isinstance(dt_raw, str):
        try:
            dt_val = datetime.fromisoformat(dt_raw.replace("Z", "+00:00"))
        except ValueError:
            dt_val = None
    if dt_val is None:
        try:
            dt_val = datetime.fromtimestamp(int(dt_raw), tz=timezone.utc)
        except Exception:
            return None
    if dt_val.tzinfo is None:
        dt_local = dt_val.replace(tzinfo=target_tz)
    else:
        dt_local = dt_val.astimezone(target_tz)
    timestamp = dt_local.replace(tzinfo=None)
    return CalendarEvent(
        timestamp=timestamp,
        title=(item.get("title") or "").strip(),
        country=(item.get("country") or "").strip(),
        impact=(item.get("impact") or "").strip(),
        actual=str(item.get("actual") or "").strip(),
        forecast=str(item.get("forecast") or "").strip(),
        previous=str(item.get("previous") or "").strip(),
    )


def _iter_weeks(start: date, end: date) -> Iterable[date]:
    cursor = _week_start(start)
    end_week = _week_start(end)
    while cursor <= end_week:
        yield cursor
        cursor += timedelta(days=7)


def fetch_calendar_events(
    start: datetime,
    end: datetime,
    *,
    tz_offset_hours: int = -4,
    min_impact: Optional[str] = None,
    retries: int = 3,
) -> List[CalendarEvent]:
    if start > end:
        start, end = end, start
    target_tz = timezone(timedelta(hours=tz_offset_hours))
    weeks = list(_iter_weeks(start.date(), end.date()))
    cache: Dict[date, List[CalendarEvent]] = {}
    events: List[CalendarEvent] = []
    for week in weeks:
        if week not in cache:
            payload = _request_week(week, retries=retries)
            cache[week] = [
                ev
                for item in payload
                if (ev := _to_event(item, target_tz)) is not None
            ]
        events.extend(cache[week])

    min_rank = None
    if min_impact:
        min_rank = IMPACT_PRIORITY.get(min_impact.lower())
        if min_rank is None:
            raise ValueError(f"Bilinmeyen impact filtresi: {min_impact}")

    start_bound = start.replace(second=0, microsecond=0)
    end_bound = end.replace(second=0, microsecond=0)
    filtered: List[CalendarEvent] = []
    for event in events:
        ts = event.timestamp
        if ts < start_bound or ts > end_bound:
            continue
        if min_rank is not None:
            rank = IMPACT_PRIORITY.get(event.impact.lower(), 0)
            if rank < min_rank:
                continue
        filtered.append(event)

    filtered.sort(key=lambda ev: ev.timestamp)
    return filtered


__all__ = ["CalendarEvent", "fetch_calendar_events", "IMPACT_PRIORITY"]
