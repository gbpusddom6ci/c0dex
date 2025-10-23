from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import html
import io
import csv
from typing import List, Optional, Dict, Any, Type, Set

from favicon import render_head_links, try_load_asset

from .counter import (
    Candle as CounterCandle,
    SEQUENCES,
    MINUTES_PER_STEP,
    DEFAULT_START_TOD,
    normalize_key,
    parse_float,
    parse_time_value,
    find_start_index,
    compute_dc_flags,
    compute_offset_alignment,
    predict_time_after_n_steps,
    detect_iou_candles,
)
from .main import (
    Candle as ConverterCandle,
    estimate_timeframe_minutes,
    adjust_to_output_tz,
    convert_30m_to_90m,
    format_price,
)
from email.parser import BytesParser
from email.policy import default as email_default
from datetime import timedelta, time as dtime

from news_loader import find_news_for_timestamp

IOU_TOLERANCE = 0.005
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_FILES = 25

def _add_security_headers(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.send_header("X-Frame-Options", "DENY")
    handler.send_header("Referrer-Policy", "no-referrer")
    handler.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'")

def _sanitize_csv_filename(name: str, suffix: str) -> str:
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = base.replace('"', "").replace("'", "").strip()
    if "." in base:
        base = base.rsplit(".", 1)[0]
    filtered = "".join(ch for ch in base if ch.isalnum() or ch in ("-", "_", "."))
    filtered = filtered.strip(".") or "converted"
    out = filtered
    if not out.lower().endswith(suffix.lower()):
        out = filtered + suffix
    if len(out) > 128:
        if "." in out:
            stem, ext = out.rsplit(".", 1)
            out = (stem[:100] or "converted") + "." + ext
        else:
            out = out[:120]
    return out
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB


def load_candles_from_text(text: str, candle_cls: Type) -> List:
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except Exception:
        class _D(csv.Dialect):
            delimiter = ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = "\n"
            quoting = csv.QUOTE_MINIMAL
        dialect = _D()

    f = io.StringIO(text)
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
    close_key = pick("close (last)", "close", "last", "c", "close last", "close(last)")
    if not (time_key and open_key and high_key and low_key and close_key):
        raise ValueError("CSV başlıkları eksik. Gerekli: Time, Open, High, Low, Close (Last)")

    candles: List = []
    for row in reader:
        t = parse_time_value(row.get(time_key))
        o = parse_float(row.get(open_key))
        h = parse_float(row.get(high_key))
        l = parse_float(row.get(low_key))
        c = parse_float(row.get(close_key))
        if None in (t, o, h, l, c):
            continue
        candles.append(candle_cls(ts=t, open=o, high=h, low=l, close=c))
    candles.sort(key=lambda x: x.ts)
    return candles


def format_pip(delta: Optional[float]) -> str:
    if delta is None:
        return "-"
    return f"{delta:+.5f}"


def page(title: str, body: str, active_tab: str = "analyze") -> bytes:
    head_links = render_head_links("    ")
    html_doc = f"""<!doctype html>
<html>
  <head>
    <meta charset='utf-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1'/>
    {head_links}
    <title>{html.escape(title)}</title>
    <style>
      body{{font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin:20px;}}
      header{{margin-bottom:16px;}}
      form label{{display:block; margin:8px 0 4px;}}
      input, select{{padding:6px; font-size:14px;}}
      button{{padding:8px 12px; font-size:14px; cursor:pointer;}}
      .row{{display:flex; gap:16px; flex-wrap:wrap; align-items:flex-end;}}
      .card{{border:1px solid #ddd; border-radius:8px; padding:12px; margin:12px 0;}}
      table{{border-collapse:collapse; width:100%;}}
      th, td{{border:1px solid #ddd; padding:6px 8px; text-align:left;}}
      th{{background:#f5f5f5;}}
      code{{background:#f5f5f5; padding:2px 4px; border-radius:4px;}}
      .tabs{{display:flex; gap:12px; margin-bottom:12px;}}
      .tabs a{{text-decoration:none; color:#0366d6; padding:6px 8px; border-radius:6px;}}
      .tabs a.active{{background:#e6f2ff; color:#024ea2;}}
    </style>
  </head>
  <body>
    <header>
      <h2>app90</h2>
    </header>
    <nav class='tabs'>
      <a href='/' class='{ 'active' if active_tab=="analyze" else '' }'>Analiz</a>
      <a href='/dc' class='{ 'active' if active_tab=="dc" else '' }'>DC List</a>
      <a href='/matrix' class='{ 'active' if active_tab=="matrix" else '' }'>Matrix</a>
      <a href='/converter' class='{ 'active' if active_tab=="converter" else '' }'>30→90 Converter</a>
      <a href='/iou' class='{ 'active' if active_tab=="iou" else '' }'>IOU Tarama</a>
    </nav>
    {body}
  </body>
</html>"""
    return html_doc.encode("utf-8")


def render_analyze_index() -> bytes:
    body = """
    <div class='card'>
      <form method='post' action='/analyze' enctype='multipart/form-data'>
        <div class='row'>
          <div>
            <label>CSV</label>
            <input type='file' name='csv' accept='.csv,text/csv' required />
          </div>
          <div>
            <label>Zaman Dilimi</label>
            <div>90m</div>
          </div>
          <div>
            <label>Girdi TZ</label>
            <select name='input_tz'>
              <option value='UTC-5'>UTC-5</option>
              <option value='UTC-4' selected>UTC-4</option>
            </select>
          </div>
          <div>
            <label>Dizi</label>
            <select name='sequence'>
              <option value='S1' selected>S1</option>
              <option value='S2'>S2</option>
            </select>
          </div>
          <div>
            <label>Offset</label>
            <select name='offset'>
              <option value='-3'>-3</option>
              <option value='-2'>-2</option>
              <option value='-1'>-1</option>
              <option value='0' selected>0</option>
              <option value='+1'>+1</option>
              <option value='+2'>+2</option>
              <option value='+3'>+3</option>
            </select>
          </div>
          <div>
            <label>DC Göster</label>
            <input type='checkbox' name='show_dc' checked />
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>Analiz Et</button>
        </div>
      </form>
    </div>
    <p>CSV başlıkları: <code>Time, Open, High, Low, Close (Last)</code> (eş anlamlılar desteklenir).</p>
    <p><strong>Not:</strong> 18:00, (Pazar hariç) 19:30 ve Cuma 16:30 mumları DC sayılmaz; bu slotlar IOU taramasında da dışlanır.</p>
    """
    return page("app90", body, active_tab="analyze")


def render_dc_index() -> bytes:
    body = """
    <div class='card'>
      <form method='post' action='/dc' enctype='multipart/form-data'>
        <div class='row'>
          <div>
            <label>CSV</label>
            <input type='file' name='csv' accept='.csv,text/csv' required />
          </div>
          <div>
            <label>Zaman Dilimi</label>
            <div>90m</div>
          </div>
          <div>
            <label>Girdi TZ</label>
            <select name='input_tz'>
              <option value='UTC-5'>UTC-5</option>
              <option value='UTC-4' selected>UTC-4</option>
            </select>
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>DC'leri Listele</button>
        </div>
      </form>
    </div>
    <p><strong>Not:</strong> Liste, app90 için hesaplanan tüm DC mumlarını içerir. 18:00, (Pazar hariç) 19:30 ve Cuma 16:30 mumları DC dışındadır.</p>
    """
    return page("app90 - DC List", body, active_tab="dc")


def render_matrix_index() -> bytes:
    body = """
    <div class='card'>
      <form method='post' action='/matrix' enctype='multipart/form-data'>
        <div class='row'>
          <div>
            <label>CSV</label>
            <input type='file' name='csv' accept='.csv,text/csv' required />
          </div>
          <div>
            <label>Zaman Dilimi</label>
            <div>90m</div>
          </div>
          <div>
            <label>Girdi TZ</label>
            <select name='input_tz'>
              <option value='UTC-5'>UTC-5</option>
              <option value='UTC-4' selected>UTC-4</option>
            </select>
          </div>
          <div>
            <label>Dizi</label>
            <select name='sequence'>
              <option value='S1' selected>S1</option>
              <option value='S2'>S2</option>
            </select>
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>Matrix Göster</button>
        </div>
      </form>
    </div>
    """
    return page("app90 - Matrix", body, active_tab="matrix")


def render_converter_index() -> bytes:
    body = """
    <div class='card'>
      <form method='post' action='/converter' enctype='multipart/form-data'>
        <label>CSV (30m, UTC-5)</label>
        <input type='file' name='csv' accept='.csv,text/csv' required />
        <div style='margin-top:12px;'>
          <button type='submit'>90m'e Dönüştür</button>
        </div>
      </form>
    </div>
    <p>Girdi UTC-5 30 dakikalık mumlar olmalıdır. Çıktı UTC-4 90 dakikalık mumlar olarak indirilir (3 × 30m = 1 × 90m).</p>
    """
    return page("app90 - Converter", body, active_tab="converter")


def render_iou_index() -> bytes:
    body = """
    <div class='card'>
      <form method='post' action='/iou' enctype='multipart/form-data'>
        <div class='row'>
          <div>
            <label>CSV</label>
            <input type='file' name='csv' accept='.csv,text/csv' required multiple />
          </div>
          <div>
            <label>Zaman Dilimi</label>
            <div>90m</div>
          </div>
          <div>
            <label>Girdi TZ</label>
            <select name='input_tz'>
              <option value='UTC-5'>UTC-5</option>
              <option value='UTC-4' selected>UTC-4</option>
            </select>
          </div>
          <div>
            <label>Dizi</label>
            <select name='sequence'>
              <option value='S1' selected>S1</option>
              <option value='S2'>S2</option>
            </select>
          </div>
          <div>
            <label>Limit (|OC|, |PrevOC|)</label>
            <input type='number' step='0.0001' min='0' value='0.1' name='limit' />
          </div>
          <div>
            <label>± Tolerans</label>
            <input type='number' step='0.0001' min='0' value='0.005' name='tolerance' />
          </div>
        </div>
        <div class='row' style='margin-top:12px; gap:32px;'>
          <label style='display:flex; align-items:center; gap:8px;'>
            <input type='checkbox' name='xyz_mode' checked />
            <span>XYZ kümesi (haber filtreli)</span>
          </label>
          <label style='display:flex; align-items:center; gap:8px;'>
            <input type='checkbox' name='xyz_summary' />
            <span>Özet tablo (yalnız XYZ kümesi)</span>
          </label>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>IOU Tara</button>
        </div>
      </form>
    </div>
    <p>IOU taraması, limit üzerindeki OC/PrevOC değerlerinin aynı işaretli olduğu mumları dosya bazında listeler. Çoklu CSV seçimini destekler.</p>
    <p><strong>Not:</strong> 18:00, (Pazar hariç) 19:30 ve Cuma 16:30 mumları IOU listesine dahil edilmez.</p>
    """
    return page("app90 - IOU", body, active_tab="iou")


def parse_multipart(handler: BaseHTTPRequestHandler) -> Dict[str, Dict[str, Any]]:
    ctype = handler.headers.get("Content-Type")
    if not ctype or "multipart/form-data" not in ctype:
        raise ValueError("multipart/form-data bekleniyor")
    length = int(handler.headers.get("Content-Length", "0") or "0")
    form = BytesParser(policy=email_default).parsebytes(
        b"Content-Type: " + ctype.encode("utf-8") + b"\n\n" + handler.rfile.read(length)
    )
    out: Dict[str, Dict[str, Any]] = {}
    for part in form.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        filename = part.get_filename()
        payload = part.get_payload(decode=True)
        if filename:
            data = payload
            if data is None:
                content = part.get_content()
                data = content.encode("utf-8", errors="replace") if isinstance(content, str) else content
            entry = {"filename": filename, "data": data or b""}
            container = out.setdefault(name, {"files": []})
            container.setdefault("files", []).append(entry)
            if "data" not in container:
                container["data"] = entry["data"]
                container["filename"] = entry["filename"]
        else:
            if payload is not None:
                value = payload.decode("utf-8", errors="replace")
            else:
                content = part.get_content()
                value = content if isinstance(content, str) else content.decode("utf-8", errors="replace")
            out[name] = {"value": value}
    return out


class App90Handler(BaseHTTPRequestHandler):
    server_version = "Candles90/1.0"
    sys_version = ""
    def do_GET(self):
        asset = try_load_asset(self.path)
        if asset:
            payload, content_type = asset
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            _add_security_headers(self)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path == "/":
            body = render_analyze_index()
        elif self.path == "/dc":
            body = render_dc_index()
        elif self.path == "/matrix":
            body = render_matrix_index()
        elif self.path == "/converter":
            body = render_converter_index()
        elif self.path == "/iou":
            body = render_iou_index()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        _add_security_headers(self)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            # Upload size guard
            try:
                length_hdr = int(self.headers.get("Content-Length", "0") or 0)
            except Exception:
                length_hdr = 0
            if length_hdr > MAX_UPLOAD_BYTES:
                self.send_response(413)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Upload too large (max 50 MB).")
                return
            form = parse_multipart(self)
            file_obj = form.get("csv")
            if not file_obj or "data" not in file_obj:
                raise ValueError("CSV dosyası bulunamadı")
            raw = file_obj["data"]
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)

            files_list = file_obj.get("files") or [{"filename": file_obj.get("filename"), "data": file_obj.get("data")}]
            if len(files_list) > MAX_FILES:
                self.send_response(413)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(b"Too many files (max 25).")
                return

            if self.path == "/converter":
                candles = load_candles_from_text(text, ConverterCandle)
                if not candles:
                    raise ValueError("Veri boş veya çözümlenemedi")
                tf_est = estimate_timeframe_minutes(candles)
                if tf_est is None or abs(tf_est - 30) > 1.0:
                    raise ValueError("Girdi 30 dakikalık akış gibi görünmüyor")
                shifted, _ = adjust_to_output_tz(candles, "UTC-5")
                converted = convert_30m_to_90m(shifted)

                buffer = io.StringIO()
                writer = csv.writer(buffer)
                writer.writerow(["Time", "Open", "High", "Low", "Close"])
                for c in converted:
                    writer.writerow([
                        c.ts.strftime("%Y-%m-%d %H:%M:%S"),
                        format_price(c.open),
                        format_price(c.high),
                        format_price(c.low),
                        format_price(c.close),
                    ])
                data = buffer.getvalue().encode("utf-8")
                filename = file_obj.get("filename") or "converted"
                download_name = _sanitize_csv_filename(filename, "_90m.csv")

                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f"attachment; filename=\"{download_name}\"")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(data)
                return

            if self.path == "/iou":
                sequence = (form.get("sequence", {}).get("value") or "S1").strip() or "S1"
                tz_value = (form.get("input_tz", {}).get("value") or "UTC-4").strip()
                limit_raw = (form.get("limit", {}).get("value") or "0").strip()
                tol_raw = (form.get("tolerance", {}).get("value") or str(IOU_TOLERANCE)).strip()
                xyz_enabled = "xyz_mode" in form
                summary_mode = "xyz_summary" in form
                try:
                    limit_val = float(limit_raw)
                except Exception:
                    limit_val = 0.0
                limit_val = abs(limit_val)
                try:
                    tolerance_val = float(tol_raw)
                except Exception:
                    tolerance_val = IOU_TOLERANCE
                tolerance_val = abs(tolerance_val)
                limit_margin = limit_val + tolerance_val

                sections: List[str] = []
                summary_entries: List[Dict[str, Any]] = []
                for entry in files_list:
                    entry_data = entry.get("data")
                    if isinstance(entry_data, (bytes, bytearray)):
                        text_entry = entry_data.decode("utf-8", errors="replace")
                    else:
                        text_entry = str(entry_data)
                    candles_entry = load_candles_from_text(text_entry, CounterCandle)
                    name = entry.get("filename") or "uploaded.csv"
                    if not candles_entry:
                        raise ValueError(f"{name}: Veri boş veya çözümlenemedi")

                    tz_norm = tz_value.upper().replace(" ", "")
                    tz_label = "UTC-4 -> UTC-4 (+0h)"
                    if tz_norm in {"UTC-5", "UTC-05", "UTC-05:00", "-05:00"}:
                        delta = timedelta(hours=1)
                        candles_entry = [CounterCandle(ts=c.ts + delta, open=c.open, high=c.high, low=c.low, close=c.close) for c in candles_entry]
                        tz_label = "UTC-5 -> UTC-4 (+1h)"

                    base_idx, base_status = find_start_index(candles_entry, DEFAULT_START_TOD)
                    report = detect_iou_candles(candles_entry, sequence, limit_val, tolerance=tolerance_val)

                    offset_statuses: List[str] = []
                    offset_counts: List[str] = []
                    total_hits = 0
                    rows: List[str] = []
                    offset_has_non_news: Dict[int, bool] = {}
                    offset_eliminations: Dict[int, List[str]] = {}
                    for item in report.offsets:
                        off_label = f"{('+' + str(item.offset)) if item.offset > 0 else str(item.offset)}"
                        status = item.offset_status or "-"
                        status_label = f"{off_label}: {status}"
                        if item.missing_steps:
                            status_label += f" (missing {item.missing_steps})"
                        offset_statuses.append(status_label)

                        hit_count = len(item.hits)
                        offset_counts.append(f"{off_label}: {hit_count}")
                        total_hits += hit_count

                        for hit in item.hits:
                            ts_s = hit.ts.strftime('%Y-%m-%d %H:%M:%S')
                            oc_label = format_pip(hit.oc)
                            prev_label = format_pip(hit.prev_oc)
                            dc_info = "True" if hit.dc_flag else "False"
                            if hit.used_dc:
                                dc_info += " (rule)"
                            news_hits = find_news_for_timestamp(hit.ts, MINUTES_PER_STEP, null_back_minutes=60)
                            detail_lines: List[str] = []
                            has_effective_news = False
                            categories_present: Set[str] = set()
                            for ev in news_hits:
                                title = ev.get("title", "")
                                title_html = html.escape(title)
                                is_all_day = bool(ev.get("all_day"))
                                if is_all_day:
                                    time_part = "All Day"
                                else:
                                    time_part = html.escape(ev.get("time") or "-")
                                line = f"{time_part} {title_html}"
                                if ev.get("window") == "recent-null":
                                    line += " (null)"
                                category = ev.get("category") or "normal"
                                categories_present.add(category)
                                if category == "holiday":
                                    line += " (holiday)"
                                elif category == "all-day":
                                    line += " (all-day)"
                                elif category == "speech":
                                    line += " (speech)"
                                if category in {"normal", "speech"}:
                                    has_effective_news = True
                                detail_lines.append(line)
                            if detail_lines:
                                if has_effective_news:
                                    prefix = "Var"
                                elif "holiday" in categories_present:
                                    prefix = "Holiday"
                                elif "all-day" in categories_present:
                                    prefix = "AllDay"
                                else:
                                    prefix = "Yok"
                                news_cell_html = prefix + "<br>" + "<br>".join(detail_lines)
                            else:
                                news_cell_html = "Yok"
                            if xyz_enabled and not has_effective_news:
                                oc_abs = abs(hit.oc)
                                prev_abs = abs(hit.prev_oc)
                                if oc_abs > limit_margin or prev_abs > limit_margin:
                                    if hit.ts.time() == dtime(hour=18, minute=0):
                                        pass
                                    else:
                                        offset_has_non_news[item.offset] = True
                                        elim_label = f"{hit.ts.strftime('%Y-%m-%d %H:%M:%S')} (seq {hit.seq_value})"
                                        bucket = offset_eliminations.setdefault(item.offset, [])
                                        if elim_label not in bucket:
                                            bucket.append(elim_label)
                            rows.append(
                                f"<tr><td>{off_label}</td><td>{hit.seq_value}</td><td>{hit.idx}</td>"
                                f"<td>{html.escape(ts_s)}</td><td>{html.escape(oc_label)}</td>"
                                f"<td>{html.escape(prev_label)}</td><td>{dc_info}</td>"
                                f"<td>{news_cell_html}</td></tr>"
                            )

                    base_offsets = [-3, -2, -1, 0, 1, 2, 3]
                    xyz_offsets = [o for o in base_offsets if not offset_has_non_news.get(o, False)] if xyz_enabled else base_offsets
                    xyz_text = ", ".join((f"+{o}" if o > 0 else str(o)) for o in xyz_offsets) if xyz_offsets else "-"
                    xyz_line = ""
                    if xyz_enabled and not summary_mode:
                        xyz_line = f"<div><strong>XYZ Kümesi:</strong> {html.escape(xyz_text)}</div>"

                    if summary_mode:
                        elimination_rows = []
                        for offset in sorted(offset_eliminations.keys()):
                            offset_label = f"+{offset}" if offset > 0 else str(offset)
                            ts_joined = ", ".join(offset_eliminations[offset])
                            elimination_rows.append(f"{offset_label}: {ts_joined}")
                        elimination_cell = "<br>".join(html.escape(row) for row in elimination_rows) if elimination_rows else "-"
                        summary_entries.append({
                            "name": name,
                            "xyz_text": xyz_text,
                            "elimination_html": elimination_cell or "-",
                        })
                    else:
                        info = (
                            f"<div class='card'>"
                            f"<h3>{html.escape(name)}</h3>"
                            f"<div><strong>Data:</strong> {len(candles_entry)} candles</div>"
                            f"<div><strong>Range:</strong> {html.escape(candles_entry[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles_entry[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>"
                            f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>"
                            f"<div><strong>Sequence:</strong> {html.escape(report.sequence)}</div>"
                            f"<div><strong>Limit:</strong> {report.limit:.5f}</div>"
                            f"<div><strong>Tolerans:</strong> {tolerance_val:.5f}</div>"
                            f"<div><strong>Base(18:00):</strong> idx={report.base_idx} status={html.escape(report.base_status)} ts={html.escape(report.base_ts.strftime('%Y-%m-%d %H:%M:%S')) if report.base_ts else '-'} </div>"
                            f"<div><strong>Offset durumları:</strong> {html.escape(', '.join(offset_statuses)) if offset_statuses else '-'} </div>"
                            f"<div><strong>Offset IOU sayıları:</strong> {html.escape(', '.join(offset_counts)) if offset_counts else '-'} </div>"
                            f"<div><strong>Toplam IOU:</strong> {total_hits}</div>"
                            f"{xyz_line}"
                            f"</div>"
                        )

                        if rows:
                            table = "<table><thead><tr><th>Offset</th><th>Seq</th><th>Index</th><th>Timestamp</th><th>OC</th><th>PrevOC</th><th>DC</th><th>Haber</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
                        else:
                            table = "<p>IOU mum bulunamadı.</p>"

                        sections.append(info + table)

                if summary_mode:
                    header = "<tr><th>Dosya</th><th>XYZ Kümesi</th><th>Elenen Offsetler</th></tr>"
                    rows_html = []
                    for entry in summary_entries:
                        rows_html.append(
                            "<tr>"
                            f"<td>{html.escape(entry['name'])}</td>"
                            f"<td>{html.escape(entry['xyz_text'])}</td>"
                            f"<td>{entry['elimination_html']}</td>"
                            "</tr>"
                        )
                    table = "<table><thead>" + header + "</thead><tbody>" + "".join(rows_html) + "</tbody></table>"
                    body = "<div class='card'>" + table + "</div>"
                else:
                    body = "\n".join(sections)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page("app90 IOU", body, active_tab="iou"))
                return

            candles = load_candles_from_text(text, CounterCandle)
            if not candles:
                raise ValueError("Veri boş veya çözümlenemedi")

            sequence = (form.get("sequence", {}).get("value") or "S1").strip() if self.path in ("/analyze", "/matrix") else "S1"
            offset_s = (form.get("offset", {}).get("value") or "0").strip() if self.path == "/analyze" else "0"
            show_dc = ("show_dc" in form) if self.path == "/analyze" else False
            tz_label_sel = (form.get("input_tz", {}).get("value") or "UTC-4").strip()

            tz_norm = tz_label_sel.upper().replace(" ", "")
            tz_label = "UTC-4 -> UTC-4 (+0h)"
            if tz_norm in {"UTC-5", "UTC-05", "UTC-05:00", "-05:00"}:
                delta = timedelta(hours=1)
                candles = [CounterCandle(ts=c.ts + delta, open=c.open, high=c.high, low=c.low, close=c.close) for c in candles]
                tz_label = "UTC-5 -> UTC-4 (+1h)"

            if self.path == "/analyze":
                try:
                    offset = int(offset_s)
                except Exception:
                    offset = 0
                if offset < -3 or offset > 3:
                    offset = 0
                seq_values = SEQUENCES.get(sequence, SEQUENCES["S2"])[:]
                base_idx, align_status = find_start_index(candles, DEFAULT_START_TOD)
                dc_flags = compute_dc_flags(candles)
                alignment = compute_offset_alignment(candles, dc_flags, base_idx, seq_values, offset)

                info_lines = [
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>",
                    f"<div><strong>Zaman Dilimi:</strong> 90m</div>",
                    f"<div><strong>Range:</strong> {html.escape(candles[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>",
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>",
                    f"<div><strong>Start:</strong> base(18:00): idx={base_idx} ts={html.escape(candles[base_idx].ts.strftime('%Y-%m-%d %H:%M:%S'))} ({align_status}); "
                    f"offset={offset} =&gt; target_ts={html.escape(alignment.target_ts.strftime('%Y-%m-%d %H:%M:%S') if alignment.target_ts else '-') } ({alignment.offset_status}) "
                    f"idx={(alignment.start_idx if alignment.start_idx is not None else '-') } actual_ts={html.escape(alignment.actual_ts.strftime('%Y-%m-%d %H:%M:%S') if alignment.actual_ts else '-') } "
                    f"missing_steps={alignment.missing_steps}</div>",
                    f"<div><strong>Sequence:</strong> {html.escape(sequence)} {html.escape(str(seq_values))}</div>",
                ]

                rows_html = []
                for v, hit in zip(seq_values, alignment.hits):
                    idx = hit.idx
                    ts = hit.ts
                    if idx is None or ts is None or not (0 <= idx < len(candles)):
                        first = seq_values[0]
                        use_target = alignment.missing_steps and v <= alignment.missing_steps
                        
                        # Son bilinen gerçek veriyi bul
                        if not use_target:
                            last_known_v = None
                            last_known_ts = None
                            last_known_idx = -1
                            for seq_v, seq_hit in zip(seq_values, alignment.hits):
                                if seq_hit.idx is not None and seq_hit.ts is not None and 0 <= seq_hit.idx < len(candles):
                                    last_known_v = seq_v
                                    last_known_ts = seq_hit.ts
                                    last_known_idx = seq_hit.idx
                            if last_known_v is not None and v > last_known_v:
                                # Son gerçek mumdan başla
                                actual_last_candle_ts = candles[-1].ts
                                actual_last_idx = len(candles) - 1
                                # DC'leri dikkate al - sadece NON-DC adımları say
                                non_dc_steps_from_last_known_to_end = 0
                                for i in range(last_known_idx + 1, actual_last_idx + 1):
                                    is_dc = dc_flags[i] if i < len(dc_flags) else False
                                    if not is_dc:
                                        non_dc_steps_from_last_known_to_end += 1
                                steps_from_end_to_v = (v - last_known_v) - non_dc_steps_from_last_known_to_end
                                pred_ts = predict_time_after_n_steps(actual_last_candle_ts, steps_from_end_to_v)
                            else:
                                delta_steps = max(0, v - first)
                                base_ts = alignment.start_ref_ts or alignment.target_ts or candles[base_idx].ts
                                pred_ts = predict_time_after_n_steps(base_ts, delta_steps)
                        else:
                            delta_steps = max(0, v - first)
                            base_ts = alignment.target_ts or alignment.start_ref_ts or candles[base_idx].ts
                            pred_ts = predict_time_after_n_steps(base_ts, delta_steps)
                        
                        pred_label = html.escape(pred_ts.strftime('%Y-%m-%d %H:%M:%S')) + " (pred, OC -, PrevOC -)"
                        if show_dc:
                            rows_html.append(f"<tr><td>{v}</td><td>-</td><td>{pred_label}</td><td>-</td></tr>")
                        else:
                            rows_html.append(f"<tr><td>{v}</td><td>-</td><td>{pred_label}</td></tr>")
                        continue
                    ts_s = ts.strftime('%Y-%m-%d %H:%M:%S')
                    pip_label = format_pip(candles[idx].close - candles[idx].open)
                    prev_label = format_pip(candles[idx - 1].close - candles[idx - 1].open) if idx - 1 >= 0 else "-"
                    ts_with_pip = f"{ts_s} (OC {pip_label}, PrevOC {prev_label})"
                    if show_dc:
                        dc_flag = dc_flags[idx]
                        dc_label = f"{dc_flag}"
                        if hit.used_dc:
                            dc_label += " (rule)"
                        rows_html.append(f"<tr><td>{v}</td><td>{idx}</td><td>{html.escape(ts_with_pip)}</td><td>{dc_label}</td></tr>")
                    else:
                        rows_html.append(f"<tr><td>{v}</td><td>{idx}</td><td>{html.escape(ts_with_pip)}</td></tr>")

                header = "<tr><th>Seq</th><th>Index</th><th>Timestamp</th>"
                if show_dc:
                    header += "<th>DC</th>"
                header += "</tr>"
                table = f"<table><thead>{header}</thead><tbody>{''.join(rows_html)}</tbody></table>"
                body = "<div class='card'>" + "".join(info_lines) + "</div>" + table
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(page("app90 sonuçlar", body, active_tab="analyze"))
                return

            dc_flags = compute_dc_flags(candles)
            if self.path == "/dc":
                rows_html = []
                count = 0
                for i, c in enumerate(candles):
                    if not dc_flags[i]:
                        continue
                    ts = c.ts.strftime("%Y-%m-%d %H:%M:%S")
                    rows_html.append(
                        f"<tr><td>{i}</td><td>{html.escape(ts)}</td><td>{c.open}</td><td>{c.high}</td><td>{c.low}</td><td>{c.close}</td></tr>"
                    )
                    count += 1
                header = "<tr><th>Index</th><th>Timestamp</th><th>Open</th><th>High</th><th>Low</th><th>Close</th></tr>"
                table = f"<table><thead>{header}</thead><tbody>{''.join(rows_html)}</tbody></table>"
                info = (
                    f"<div class='card'>"
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>"
                    f"<div><strong>Zaman Dilimi:</strong> 90m</div>"
                    f"<div><strong>Range:</strong> {html.escape(candles[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>"
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>"
                    f"<div><strong>DC count:</strong> {count}</div>"
                    f"</div>"
                )
                body = info + table
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(page("app90 DC List", body, active_tab="dc"))
                return

            if self.path == "/matrix":
                seq_values = SEQUENCES.get(sequence, SEQUENCES["S2"])[:]
                base_idx, align_status = find_start_index(candles, DEFAULT_START_TOD)
                offsets = [-3, -2, -1, 0, 1, 2, 3]
                per_offset = {o: compute_offset_alignment(candles, dc_flags, base_idx, seq_values, o) for o in offsets}

                header_cells = ''.join(f"<th>{'+'+str(o) if o>0 else str(o)}</th>" for o in offsets)
                rows = []
                for vi, v in enumerate(seq_values):
                    cells = [f"<td>{v}</td>"]
                    for o in offsets:
                        alignment = per_offset[o]
                        hit = alignment.hits[vi] if vi < len(alignment.hits) else None
                        idx = hit.idx if hit else None
                        ts = hit.ts if hit else None
                        if idx is not None and ts is not None and 0 <= idx < len(candles):
                            ts_s = ts.strftime('%Y-%m-%d %H:%M:%S')
                            oc_label = format_pip(candles[idx].close - candles[idx].open)
                            prev_label = format_pip(candles[idx - 1].close - candles[idx - 1].open) if idx - 1 >= 0 else "-"
                            label = f"{ts_s} (OC {oc_label}, PrevOC {prev_label})"
                            if hit.used_dc:
                                label += " (DC)"
                            cells.append(f"<td>{html.escape(label)}</td>")
                        else:
                            first = seq_values[0]
                            use_target = alignment.missing_steps and v <= alignment.missing_steps
                            
                            # Son bilinen gerçek veriyi bul
                            if not use_target:
                                last_known_v = None
                                last_known_ts = None
                                last_known_idx = -1
                                for seq_v, seq_hit in zip(seq_values, alignment.hits):
                                    if seq_hit.idx is not None and seq_hit.ts is not None and 0 <= seq_hit.idx < len(candles):
                                        last_known_v = seq_v
                                        last_known_ts = seq_hit.ts
                                        last_known_idx = seq_hit.idx
                                if last_known_v is not None and v > last_known_v:
                                    # Son gerçek mumdan başla
                                    actual_last_candle_ts = candles[-1].ts
                                    actual_last_idx = len(candles) - 1
                                    # DC'leri dikkate al - sadece NON-DC adımları say
                                    non_dc_steps_from_last_known_to_end = 0
                                    for i in range(last_known_idx + 1, actual_last_idx + 1):
                                        is_dc = dc_flags[i] if i < len(dc_flags) else False
                                        if not is_dc:
                                            non_dc_steps_from_last_known_to_end += 1
                                    steps_from_end_to_v = (v - last_known_v) - non_dc_steps_from_last_known_to_end
                                    ts_pred = predict_time_after_n_steps(actual_last_candle_ts, steps_from_end_to_v)
                                else:
                                    delta_steps = max(0, v - first)
                                    base_ts = alignment.start_ref_ts or alignment.target_ts or candles[base_idx].ts
                                    ts_pred = predict_time_after_n_steps(base_ts, delta_steps)
                            else:
                                delta_steps = max(0, v - first)
                                base_ts = alignment.target_ts or alignment.start_ref_ts or candles[base_idx].ts
                                ts_pred = predict_time_after_n_steps(base_ts, delta_steps)
                            
                            cells.append(f"<td>{html.escape(ts_pred.strftime('%Y-%m-%d %H:%M:%S'))} (pred, OC -, PrevOC -)</td>")
                    rows.append(f"<tr>{''.join(cells)}</tr>")

                status_summary = ', '.join(
                    f"{('+' + str(o)) if o > 0 else str(o)}: {per_offset[o].offset_status}"
                    for o in offsets
                )

                table = f"<table><thead><tr><th>Seq</th>{header_cells}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
                info = (
                    f"<div class='card'>"
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>"
                    f"<div><strong>Zaman Dilimi:</strong> 90m</div>"
                    f"<div><strong>Range:</strong> {html.escape(candles[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>"
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>"
                    f"<div><strong>Sequence:</strong> {html.escape(sequence)}</div>"
                    f"<div><strong>Offset durumları:</strong> {html.escape(status_summary)}</div>"
                    f"</div>"
                )

                body = info + table
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(page("app90 Matrix", body, active_tab="matrix"))
                return

            raise ValueError("Bilinmeyen istek")
        except Exception as e:
            msg = html.escape(str(e) or "Bilinmeyen hata")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            _add_security_headers(self)
            self.end_headers()
            self.wfile.write(page("Hata", f"<p>Hata: {msg}</p><p><a href='/'>&larr; Geri</a></p>"))

    def log_message(self, format, *args):
        pass


def run(host: str, port: int) -> None:
    server = HTTPServer((host, port), App90Handler)
    print(f"app90 web: http://{host}:{port}/")
    server.serve_forever()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app90.web", description="app90 için birleşik web arayüzü")
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu adresi (vars: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2190, help="Port (vars: 2190)")
    args = parser.parse_args(argv)
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
