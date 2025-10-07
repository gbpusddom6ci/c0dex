import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from .news import NewsService, _normalize_week_start


def parse_date(value: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"geçersiz tarih: {value}") from exc


def parse_header(entries: List[str]) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    for item in entries:
        if "=" not in item:
            raise argparse.ArgumentTypeError("header girdileri key=value formatında olmalı")
        key, val = item.split("=", 1)
        headers[key.strip()] = val.strip()
    return headers


def build_target_weeks(start: date, weeks: int, direction: str) -> List[date]:
    normalized = _normalize_week_start(start)
    offsets: List[int] = []
    for i in range(weeks):
        delta = i * 7
        offsets.append(-delta if direction == "past" else delta)
    unique_weeks = {normalized + timedelta(days=off) for off in offsets}
    return sorted(unique_weeks)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app72.prefetch_news",
        description="ForexFactory haftalık JSONlarını önbelleğe alır.",
    )
    parser.add_argument("--start", type=parse_date, default=datetime.now().date(), help="Başlangıç tarihi (YYYY-MM-DD)")
    parser.add_argument("--weeks", type=int, default=1, help="Toplam hafta sayısı")
    parser.add_argument("--direction", choices=["past", "future"], default="past", help="Haftaları hangi yöne doğru çekelim")
    parser.add_argument("--delay", type=float, default=5.0, help="Ağ çağrıları arasında bekleme süresi (sn)")
    parser.add_argument("--cookies", default=None, help="ForexFactory çerezleri (ör. __cf_bm; cf_clearance)")
    parser.add_argument("--header", action="append", default=[], help="Ek HTTP header (key=value)")
    parser.add_argument("--impacts", nargs="+", default=["high", "medium"], help="Impact filtreleri")
    parser.add_argument("--cache-dir", type=Path, default=None, help="Önbellek klasörü")
    parser.add_argument("--base-url", default=None, help="ForexFactory CDN tabanı")
    parser.add_argument("--retries", type=int, default=5, help="İstek tekrarı sayısı")
    parser.add_argument("--retry-delay", type=float, default=10.0, help="Tekrarlar arası bekleme")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout")
    parser.add_argument("--force", action="store_true", help="Önbellek dosyası olsa bile yeniden indir")
    parser.add_argument("--verbose", action="store_true", help="İlerlemeyi stdout'a yaz")

    args = parser.parse_args(argv)

    if args.weeks <= 0:
        parser.error("weeks pozitif olmalı")

    headers = parse_header(args.header)
    cache_dir = args.cache_dir if args.cache_dir is not None else Path.home() / ".cache" / "app72_news"
    base_url = args.base_url or "https://nfs.faireconomy.media"

    service = NewsService(
        base_url=base_url,
        cache_dir=cache_dir,
        impacts=args.impacts,
        headers=headers,
        cookies=args.cookies,
        retry_attempts=args.retries,
        retry_delay=args.retry_delay,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    targets = build_target_weeks(args.start, args.weeks, args.direction)
    total = len(targets)
    failures = 0

    for idx, week_start in enumerate(targets, start=1):
        count, served_from_cache = service.prefetch_week(week_start, force=args.force)
        label = week_start.strftime("%Y-%m-%d")
        if service.last_error:
            status = f"hata ({service.last_error})"
            failures += 1
        elif served_from_cache:
            status = "cache"
        else:
            status = "indirildi"
        print(f"[{idx}/{total}] {label}: {count} kayıt ({status})")
        if not served_from_cache and args.delay > 0 and idx < total:
            time.sleep(args.delay)

    return 1 if failures else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
