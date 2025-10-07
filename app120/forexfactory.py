"""ForexFactory ekonomik takvim entegrasyonu için yardımcılar."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Set
from zoneinfo import ZoneInfo

NEWS_FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
USER_AGENT = "app120-forexfactory/1.0"
CACHE_TTL = timedelta(minutes=10)
SOURCE_LABEL = "forexfactory.thisweek"

NY_TZ = ZoneInfo("America/New_York")

IMPACT_ORDER = {
    "HOLIDAY": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}


@dataclass(frozen=True)
class NewsEvent:
    """ForexFactory etkinliği."""

    time: datetime
    title: str
    country: str
    impact: str
    forecast: str
    previous: str


@dataclass
class NewsFetchResult:
    """Haber sorgusunun sonucu ve meta bilgileri."""

    events: List[NewsEvent]
    fetched_at: Optional[datetime]
    cache_hit: bool
    warnings: List[str]
    source: str
    error: Optional[str] = None


_cache_lock = threading.Lock()
_cache_events: Optional[List[NewsEvent]] = None
_cache_timestamp: Optional[datetime] = None


def _impact_label(raw: Optional[str]) -> str:
    if not raw:
        return "Unknown"
    label = raw.strip()
    if not label:
        return "Unknown"
    upper = label.upper()
    if upper == "NONE":
        return "Holiday"
    if upper in ("HIGH", "MEDIUM", "LOW", "HOLIDAY"):
        return upper.capitalize()
    return label


def _impact_level(label: str) -> int:
    return IMPACT_ORDER.get(label.upper(), 0 if label.upper() == "UNKNOWN" else 1)


def _parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    # ISO 8601 destekler; ForexFactory DST'yi ofset olarak gönderiyor.
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=NY_TZ)
    else:
        dt = dt.astimezone(NY_TZ)
    return dt.replace(tzinfo=None)


def _download_feed() -> List[NewsEvent]:
    req = urllib.request.Request(NEWS_FEED_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:  # type: ignore[arg-type]
        try:
            payload = json.load(resp)
        except json.JSONDecodeError as exc:  # pragma: no cover - korunma amaçlı
            raise RuntimeError("ForexFactory JSON çözümlemesi başarısız") from exc
    if not isinstance(payload, list):
        raise RuntimeError("ForexFactory beklenmedik cevap döndürdü")

    events: List[NewsEvent] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        ts = _parse_time(str(item.get("date")))
        if ts is None:
            continue
        title = str(item.get("title") or "").strip()
        country = str(item.get("country") or "").strip().upper()
        impact = _impact_label(item.get("impact"))
        forecast = str(item.get("forecast") or "").strip()
        previous = str(item.get("previous") or "").strip()
        events.append(NewsEvent(time=ts, title=title, country=country, impact=impact, forecast=forecast, previous=previous))

    events.sort(key=lambda e: e.time)
    return events


def fetch_calendar(force_refresh: bool = False) -> NewsFetchResult:
    global _cache_events, _cache_timestamp
    warnings: List[str] = []
    error: Optional[str] = None
    now = datetime.utcnow()

    with _cache_lock:
        if not force_refresh and _cache_events is not None and _cache_timestamp is not None:
            if now - _cache_timestamp <= CACHE_TTL:
                return NewsFetchResult(
                    events=list(_cache_events),
                    fetched_at=_cache_timestamp,
                    cache_hit=True,
                    warnings=warnings,
                    source=SOURCE_LABEL,
                    error=None,
                )

    try:
        events = _download_feed()
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        error = f"HTTP hatası: {exc}"
        events = []
    except Exception as exc:  # pragma: no cover - beklenmeyen durum
        error = str(exc)
        events = []

    fetched_at = datetime.utcnow()
    if events:
        with _cache_lock:
            _cache_events = events
            _cache_timestamp = fetched_at
    else:
        with _cache_lock:
            if _cache_events is not None and _cache_timestamp is not None:
                cached_copy = list(_cache_events)
                cached_time = _cache_timestamp
                warnings.append("Canlı ForexFactory verisine ulaşılamadı, önbellekteki sonuç gösteriliyor.")
                return NewsFetchResult(
                    events=cached_copy,
                    fetched_at=cached_time,
                    cache_hit=True,
                    warnings=warnings,
                    source=SOURCE_LABEL,
                    error=error,
                )

    return NewsFetchResult(
        events=events,
        fetched_at=fetched_at if events else None,
        cache_hit=False,
        warnings=warnings,
        source=SOURCE_LABEL,
        error=error,
    )


def get_events(
    range_start: datetime,
    range_end: datetime,
    *,
    min_impact: str = "Medium",
    countries: Optional[Iterable[str]] = None,
    pad_minutes: int = 0,
    force_refresh: bool = False,
) -> NewsFetchResult:
    """Verilen tarih aralığına uyan haberleri döndürür."""

    base = fetch_calendar(force_refresh=force_refresh)
    if not base.events:
        return base

    allowed_countries: Optional[Set[str]] = None
    if countries is not None:
        normalized = {c.strip().upper() for c in countries if c and c.strip()}
        if normalized:
            allowed_countries = normalized

    threshold = _impact_level(min_impact)
    window_start = range_start - timedelta(minutes=max(pad_minutes, 0))
    window_end = range_end + timedelta(minutes=max(pad_minutes, 0))

    filtered: List[NewsEvent] = []
    for event in base.events:
        if event.time < window_start or event.time > window_end:
            continue
        if allowed_countries and event.country not in allowed_countries:
            continue
        if _impact_level(event.impact) < threshold:
            continue
        filtered.append(event)

    warnings = list(base.warnings)
    if base.events:
        first_ts = base.events[0].time
        last_ts = base.events[-1].time
        if window_end < first_ts:
            warnings.append("ForexFactory kaynağı bu aralığın öncesini kapsamıyor (yalnızca güncel hafta mevcut).")
        elif window_start > last_ts:
            warnings.append("ForexFactory kaynağı bu aralığın sonrasını kapsamıyor (yalnızca güncel hafta mevcut).")

    return NewsFetchResult(
        events=filtered,
        fetched_at=base.fetched_at,
        cache_hit=base.cache_hit,
        warnings=warnings,
        source=base.source,
        error=base.error,
    )
