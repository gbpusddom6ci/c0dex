import argparse
import csv
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, time as dtime
from typing import List, Optional, Tuple, Dict


@dataclass
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float


def normalize_key(name: str) -> str:
    return name.strip().strip('"').strip("'").lower()


def parse_float(val: str) -> Optional[float]:
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.lower() in {"nan", "null", "none"}:
        return None
    if "," in s and "." not in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_time_value(val: str) -> Optional[datetime]:
    if val is None:
        return None
    s = str(val).strip()
    if s == "":
        return None
    try:
        if s.isdigit():
            iv = int(s)
            if iv >= 10**12:
                return datetime.fromtimestamp(iv / 1000, tz=timezone.utc).replace(tzinfo=None)
            return datetime.fromtimestamp(iv, tz=timezone.utc).replace(tzinfo=None)
    except Exception:
        pass
    iso_try = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_try)
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt
    except Exception:
        pass
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            continue
    return None


def sniff_dialect(path: str) -> csv.Dialect:
    with open(path, "r", encoding="utf-8", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t")
        except Exception:
            class _D(csv.Dialect):
                delimiter = ","
                quotechar = '"'
                doublequote = True
                skipinitialspace = True
                lineterminator = "\n"
                quoting = csv.QUOTE_MINIMAL

            return _D()


def load_candles(path: str) -> List[Candle]:
    dialect = sniff_dialect(path)
    rows: List[Candle] = []
    with open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError("CSV header bulunamadı")
        field_map = {normalize_key(k): k for k in reader.fieldnames}

        def pick(*alts: str) -> Optional[str]:
            for a in alts:
                if a in field_map:
                    return field_map[a]
            return None

        time_key = pick("time", "timestamp", "date", "datetime")
        open_key = pick("open", "o", "open (first)")
        high_key = pick("high", "h")
        low_key = pick("low", "l")
        close_key = pick("close (last)", "close", "last", "c", "close last", "close(last)", "latest", "price", "close price", "last price")
        if not (time_key and open_key and high_key and low_key and close_key):
            raise ValueError("CSV başlıkları eksik. Gerekli: Time, Open, High, Low, Close (Last)")

        for row in reader:
            t = parse_time_value(row.get(time_key))
            o = parse_float(row.get(open_key))
            h = parse_float(row.get(high_key))
            l = parse_float(row.get(low_key))
            c = parse_float(row.get(close_key))
            if None in (t, o, h, l, c):
                continue
            rows.append(Candle(ts=t, open=o, high=h, low=l, close=c))
    rows.sort(key=lambda x: x.ts)
    return rows


def estimate_timeframe_minutes(candles: List[Candle]) -> Optional[float]:
    if len(candles) < 2:
        return None
    deltas = [
        (candles[i].ts - candles[i - 1].ts).total_seconds() / 60
        for i in range(1, min(len(candles), 200))
    ]
    deltas = [d for d in deltas if d > 0]
    if not deltas:
        return None
    deltas.sort()
    mid = len(deltas) // 2
    if len(deltas) % 2 == 1:
        return deltas[mid]
    return (deltas[mid - 1] + deltas[mid]) / 2


def adjust_to_output_tz(candles: List[Candle], input_tz: str) -> Tuple[List[Candle], str]:
    tz_norm = (input_tz or "").strip().upper().replace(" ", "")
    shift_hours = 0
    if tz_norm in {"UTC-5", "UTC-05", "UTC-05:00", "-05:00"}:
        shift_hours = 1
        label = "UTC-5 -> UTC-4 (+1h)"
    else:
        label = "UTC-4 -> UTC-4 (+0h)"
    if shift_hours == 0:
        return candles, label
    delta = timedelta(hours=shift_hours)
    shifted: List[Candle] = [
        Candle(
            ts=c.ts + delta,
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
        )
        for c in candles
    ]
    return shifted, label


def _align_to_96_minutes(ts: datetime) -> datetime:
    # 96 dakikalık blokları her gün 18:00'e göre hizala.
    anchor = datetime.combine(ts.date(), dtime(hour=18, minute=0))
    if ts < anchor:
        anchor -= timedelta(days=1)
    delta_minutes = int((ts - anchor).total_seconds() // 60)
    block_index = delta_minutes // 96
    return anchor + timedelta(minutes=block_index * 96)


def convert_12m_to_96m(candles: List[Candle]) -> List[Candle]:
    if not candles:
        raise ValueError("Veri boş")

    ordered_all = sorted(candles, key=lambda c: c.ts)
    ordered: List[Candle] = []
    for c in ordered_all:
        wd = c.ts.weekday()
        if wd == 5:
            continue  # Cumartesi mumlarını atla
        if wd == 6 and c.ts.hour < 18:
            continue  # Pazar 18:00 öncesini atla (hafta açılışı 18:00)
        ordered.append(c)

    if not ordered:
        raise ValueError("Hafta içi mum bulunamadı")
    groups: Dict[datetime, List[Candle]] = {}
    order: List[datetime] = []

    for candle in ordered:
        block_ts = _align_to_96_minutes(candle.ts)
        if block_ts not in groups:
            groups[block_ts] = []
            order.append(block_ts)
        groups[block_ts].append(candle)

    aggregated: List[Candle] = []
    for block_ts in order:
        block = groups[block_ts]
        if not block:
            continue
        open_ = block[0].open
        close_ = block[-1].close
        high_ = max(c.high for c in block)
        low_ = min(c.low for c in block)
        aggregated.append(Candle(ts=block_ts, open=open_, high=high_, low=low_, close=close_))

    if not aggregated:
        raise ValueError("96 dakikalık mum üretilemedi")

    for i in range(len(aggregated) - 1):
        next_open = aggregated[i + 1].open
        aggregated[i].close = next_open
        if next_open >= aggregated[i].high:
            aggregated[i].high = next_open
        if next_open <= aggregated[i].low:
            aggregated[i].low = next_open

    last = aggregated[-1]
    if last.close >= last.high:
        last.high = last.close
    if last.close <= last.low:
        last.low = last.close

    return aggregated


def format_price(value: float) -> str:
    s = f"{value:.6f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def write_csv(path: Optional[str], candles: List[Candle]) -> None:
    writer_target = open(path, "w", encoding="utf-8", newline="") if path else None
    try:
        out = writer_target or sys.stdout
        csv_writer = csv.writer(out)
        csv_writer.writerow(["Time", "Open", "High", "Low", "Close"])
        for c in candles:
            csv_writer.writerow([
                c.ts.strftime("%Y-%m-%d %H:%M:%S"),
                format_price(c.open),
                format_price(c.high),
                format_price(c.low),
                format_price(c.close),
            ])
    finally:
        if writer_target:
            writer_target.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app96",
        description="12m mumları UTC-4 96m mumlarına dönüştürür",
    )
    parser.add_argument("--csv", required=True, help="Girdi CSV (12m, UTC-5)")
    parser.add_argument(
        "--input-tz",
        choices=["UTC-4", "UTC-5"],
        default="UTC-5",
        help="Girdi zaman dilimi (varsayılan UTC-5)",
    )
    parser.add_argument(
        "--output",
        help="Çıktı CSV dosya yolu (boş bırakılırsa stdout'a yazılır)",
    )

    args = parser.parse_args(argv)

    candles = load_candles(args.csv)
    if not candles:
        raise SystemExit("Veri okunamadı veya boş")

    tf_est = estimate_timeframe_minutes(candles)
    if tf_est is None or abs(tf_est - 12) > 1.0:
        raise ValueError("Girdi 12 dakikalık akış gibi görünmüyor")

    shifted, tz_label = adjust_to_output_tz(candles, args.input_tz)
    converted = convert_12m_to_96m(shifted)

    write_csv(args.output, converted)

    print(
        f"Input candles: {len(candles)} | Output candles: {len(converted)} | TZ: {tz_label}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
