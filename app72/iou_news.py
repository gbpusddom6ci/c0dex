import argparse
from datetime import timedelta
from typing import List, Optional

from .counter import Candle, detect_iou_candles, fmt_pip, load_candles
from .news_fetcher import fetch_calendar_events


def _normalize_timezone(candles: List[Candle], tz_label: str) -> List[Candle]:
    tz_norm = tz_label.upper().replace(" ", "")
    if tz_norm in {"UTC-5", "UTC-05", "UTC-05:00", "-05:00"}:
        delta = timedelta(hours=1)
        return [
            Candle(ts=c.ts + delta, open=c.open, high=c.high, low=c.low, close=c.close)
            for c in candles
        ]
    return candles


def _format_delta_minutes(base_ts, target_ts) -> str:
    diff_minutes = int((target_ts - base_ts).total_seconds() // 60)
    if diff_minutes == 0:
        return "0dk"
    if diff_minutes > 0:
        return f"+{diff_minutes}dk"
    return f"{diff_minutes}dk"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app72.iou_news",
        description="IOU mumlarını ForexFactory haberleriyle eşleştirir",
    )
    parser.add_argument("--csv", required=True, help="72m CSV dosya yolu")
    parser.add_argument("--sequence", choices=["S1", "S2"], default="S2", help="Kullanılacak dizi")
    parser.add_argument("--limit", type=float, default=0.0, help="Mutlak pip eşiği (varsayılan 0)")
    parser.add_argument(
        "--input-tz",
        choices=["UTC-4", "UTC-5"],
        default="UTC-5",
        help="CSV zaman dilimi (varsayılan UTC-5, otomatik UTC-4'e kaydırılır)",
    )
    parser.add_argument(
        "--min-impact",
        choices=["all", "holiday", "low", "medium", "high"],
        default="low",
        help="En düşük haber etkisi (varsayılan low)",
    )
    parser.add_argument(
        "--news-before",
        type=int,
        default=72,
        help="Mum başlangıcından önce kontrol edilecek dakika sayısı",
    )
    parser.add_argument(
        "--news-after",
        type=int,
        default=0,
        help="Mum başlangıcından sonra kontrol edilecek dakika sayısı",
    )

    args = parser.parse_args(argv)

    candles = load_candles(args.csv)
    if not candles:
        print("Veri yüklenemedi ya da boş")
        return 1
    candles = _normalize_timezone(candles, args.input_tz)

    report = detect_iou_candles(candles, args.sequence, abs(args.limit))

    hits = []
    for offset_result in report.offsets:
        for hit in offset_result.hits:
            if hit.ts is None or hit.idx is None:
                continue
            hits.append((offset_result.offset, hit))

    if not hits:
        print("IOU mum bulunamadı.")
        return 0

    before_delta = timedelta(minutes=max(0, args.news_before))
    after_delta = timedelta(minutes=max(0, args.news_after))
    earliest = min(hit.ts for _, hit in hits) - before_delta
    latest = max(hit.ts for _, hit in hits) + after_delta

    min_impact = args.min_impact if args.min_impact != "all" else None
    try:
        events = fetch_calendar_events(
            earliest,
            latest,
            tz_offset_hours=-4,
            min_impact=min_impact,
        )
    except Exception as exc:
        print(f"Haber verisi alınamadı: {exc}")
        return 2

    grouped_output: List[str] = []
    hits.sort(key=lambda item: (item[1].ts, item[0], item[1].seq_value))
    for offset, hit in hits:
        window_start = hit.ts - before_delta
        window_end = hit.ts + after_delta
        oc_label = fmt_pip(hit.oc)
        prev_label = fmt_pip(hit.prev_oc)
        dc_info = "DC" if hit.dc_flag else "Normal"
        used_rule = " (rule)" if hit.used_dc else ""
        header = (
            f"ofs:{offset:+d} v:{hit.seq_value} idx:{hit.idx} ts:{hit.ts:%Y-%m-%d %H:%M:%S} "
            f"OC:{oc_label} PrevOC:{prev_label} {dc_info}{used_rule}"
        )
        grouped_output.append(header)

        matching = [
            ev
            for ev in events
            if window_start <= ev.timestamp <= window_end
        ]

        if not matching:
            grouped_output.append("  haber yok")
            continue

        matching.sort(key=lambda ev: ev.timestamp)
        for ev in matching:
            impact_label = ev.impact or "-"
            rel = _format_delta_minutes(hit.ts, ev.timestamp)
            grouped_output.append(
                f"  {ev.timestamp:%Y-%m-%d %H:%M} [{impact_label}] {ev.country or '-'} - {ev.title or '-'}"
            )
            grouped_output.append(f"    delta={rel}")
            extra: List[str] = []
            if ev.actual:
                extra.append(f"actual:{ev.actual}")
            if ev.forecast:
                extra.append(f"forecast:{ev.forecast}")
            if ev.previous:
                extra.append(f"previous:{ev.previous}")
            if extra:
                grouped_output.append("    " + " ".join(extra))

    print(f"Toplam IOU: {len(hits)} | Dizi: {report.sequence} | Limit: {report.limit:.5f}")
    for line in grouped_output:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
