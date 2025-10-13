from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import html
import io
from typing import List, Optional, Dict, Any, Tuple

from favicon import render_head_links, try_load_asset

from .main import (
    Candle,
    SEQUENCES,
    normalize_key,
    parse_float,
    parse_time_value,
    load_candles,
    find_start_index,
    compute_dc_flags,
    compute_offset_alignment,
    detect_iou_candles,
)
import csv
from email.parser import BytesParser
from email.policy import default as email_default
from datetime import time as dtime
from datetime import timedelta

from news_loader import find_news_for_timestamp

MINUTES_PER_STEP = 60


def load_candles_from_text(text: str) -> List[Candle]:
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

    rows: List[Candle] = []
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
      <h2>app321</h2>
    </header>
    <nav class='tabs'>
      <a href='/' class='{ 'active' if active_tab=="analyze" else '' }'>Analiz</a>
      <a href='/dc' class='{ 'active' if active_tab=="dc" else '' }'>DC List</a>
      <a href='/matrix' class='{ 'active' if active_tab=="matrix" else '' }'>Matrix</a>
      <a href='/iou' class='{ 'active' if active_tab=="iou" else '' }'>IOU Tarama</a>
    </nav>
    {body}
  </body>
</html>"""
    return html_doc.encode("utf-8")


def render_index() -> bytes:
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
        <div>60m</div>
      </div>
      <div>
        <label>Girdi TZ</label>
        <select name='input_tz'>
          <option value='UTC-5' selected>UTC-5</option>
          <option value='UTC-4'>UTC-4</option>
        </select>
      </div>
      <div>
        <label>Dizi</label>
        <select name='sequence'>
          <option value='S1'>S1</option>
          <option value='S2' selected>S2</option>
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
    """
    return page("app321", body, active_tab="analyze")


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
            <div>60m</div>
          </div>
          <div>
            <label>Girdi TZ</label>
            <select name='input_tz'>
              <option value='UTC-5' selected>UTC-5</option>
              <option value='UTC-4'>UTC-4</option>
            </select>
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>DC'leri Listele</button>
        </div>
      </form>
    </div>
    <p>Not: DC istisnası 13:00–20:00 arasında sayımda geçerli; bu sayfada tüm DC'ler listelenir.</p>
    """
    return page("app321 - DC List", body, active_tab="dc")


# prediction sekmesi kaldırıldı; analiz içinde gösterilir


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
            <div>60m</div>
          </div>
          <div>
            <label>Girdi TZ</label>
            <select name='input_tz'>
              <option value='UTC-5' selected>UTC-5</option>
              <option value='UTC-4'>UTC-4</option>
            </select>
          </div>
          <div>
            <label>Dizi</label>
            <select name='sequence'>
              <option value='S1'>S1</option>
              <option value='S2' selected>S2</option>
            </select>
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>Oluştur</button>
        </div>
      </form>
    </div>
    <p>Matrix: Tüm offsetler (-3..+3) için zamanlar ve (veri yoksa) tahminler.</p>
    """
    return page("app321 - Matrix", body, active_tab="matrix")


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
            <div>60m</div>
          </div>
          <div>
            <label>Girdi TZ</label>
            <select name='input_tz'>
              <option value='UTC-5' selected>UTC-5</option>
              <option value='UTC-4'>UTC-4</option>
            </select>
          </div>
          <div>
            <label>Dizi</label>
            <select name='sequence'>
              <option value='S1'>S1</option>
              <option value='S2' selected>S2</option>
            </select>
          </div>
          <div>
            <label>Limit (|OC|, |PrevOC|)</label>
            <input type='number' step='0.0001' min='0' value='0.1' name='limit' />
          </div>
        </div>
        <div class='row' style='margin-top:12px; gap:32px;'>
          <label style='display:flex; align-items:center; gap:8px;'>
            <input type='checkbox' name='xyz_mode' />
            <span>XYZ kümesi (haber filtreli)</span>
          </label>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>IOU Tara</button>
        </div>
      </form>
    </div>
    <p>IOU mumlar, limit eşiğinin üzerindeki OC ve PrevOC değerlerinin aynı işaretli olduğu durumlarda raporlanır. Birden fazla CSV aynı anda seçilebilir.</p>
    """
    return page("app321 - IOU", body, active_tab="iou")


class AppHandler(BaseHTTPRequestHandler):
    def _parse_multipart(self) -> Dict[str, Any]:
        ct = self.headers.get("Content-Type", "")
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except Exception:
            length = 0
        body = self.rfile.read(length)
        if not ct.lower().startswith("multipart/form-data"):
            raise ValueError("Yalnızca multipart/form-data desteklenir")
        header_bytes = b"Content-Type: " + ct.encode("utf-8") + b"\r\nMIME-Version: 1.0\r\n\r\n"
        msg = BytesParser(policy=email_default).parsebytes(header_bytes + body)
        fields: Dict[str, Any] = {}
        for part in msg.iter_parts():
            cd = part.get("Content-Disposition", "")
            if not cd:
                continue
            params: Dict[str, str] = {}
            for item in cd.split(";"):
                item = item.strip()
                if "=" in item:
                    k, v = item.split("=", 1)
                    params[k.strip().lower()] = v.strip().strip('"')
            name = params.get("name")
            filename = params.get("filename")
            payload = part.get_payload(decode=True) or b""
            if not name:
                continue
            if filename is not None:
                entry = {"filename": filename, "data": payload}
                if name not in fields:
                    fields[name] = {"files": [entry]}
                else:
                    fields[name].setdefault("files", []).append(entry)
            else:
                charset = part.get_content_charset() or "utf-8"
                try:
                    value = payload.decode(charset, errors="replace")
                except Exception:
                    value = payload.decode("utf-8", errors="replace")
                fields[name] = {"value": value}
        return fields

    def do_GET(self):
        asset = try_load_asset(self.path)
        if asset:
            payload, content_type = asset
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.startswith("/dc"):
            body = render_dc_index()
        elif self.path.startswith("/matrix"):
            body = render_matrix_index()
        elif self.path.startswith("/iou"):
            body = render_iou_index()
        else:
            body = render_index()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/analyze", "/dc", "/matrix", "/iou"):
            self.send_error(404)
            return
        try:
            form = self._parse_multipart()
            file_field = form.get("csv") or {}
            files = [entry for entry in file_field.get("files", []) if entry.get("data") is not None]
            if not files:
                raise ValueError("CSV yüklenmedi")

            def decode_entry(entry: Dict[str, Any]) -> str:
                raw = entry.get("data")
                if isinstance(raw, (bytes, bytearray)):
                    return raw.decode("utf-8", errors="replace")
                return str(raw)

            def apply_tz(candles: List[Candle], tz_value: Optional[str]) -> Tuple[List[Candle], str]:
                tz_norm = (tz_value or "UTC-4").strip().upper().replace(" ", "")
                if tz_norm in {"UTC-5", "UTC-05", "UTC-05:00", "-05:00"}:
                    delta = timedelta(hours=1)
                    shifted = [
                        Candle(ts=c.ts + delta, open=c.open, high=c.high, low=c.low, close=c.close)
                        for c in candles
                    ]
                    return shifted, "UTC-5 -> UTC-4 (+1h)"
                return candles, "UTC-4 -> UTC-4 (+0h)"

            if self.path == "/analyze":
                entry = files[0]
                candles = load_candles_from_text(decode_entry(entry))
                if not candles:
                    raise ValueError("Veri boş veya çözümlenemedi")

                sequence = (form.get("sequence", {}).get("value") or "S2").strip()
                offset_s = (form.get("offset", {}).get("value") or "0").strip()
                show_dc = "show_dc" in form
                tz_an = (form.get("input_tz", {}).get("value") or "UTC-5").strip()

                candles, tz_label = apply_tz(candles, tz_an)

                start_tod = dtime(hour=18, minute=0)
                base_idx, align_status = find_start_index(candles, start_tod)
                try:
                    off = int(offset_s)
                except Exception:
                    off = 0
                if off < -3 or off > 3:
                    off = 0

                seq_values = SEQUENCES.get(sequence, SEQUENCES["S2"])[:]
                dc_flags_all = compute_dc_flags(candles)
                alignment = compute_offset_alignment(candles, dc_flags_all, base_idx, seq_values, off)

                rows_html = []
                for v, hit in zip(seq_values, alignment.hits):
                    idx = hit.idx
                    ts = hit.ts
                    if idx is None or ts is None or not (0 <= idx < len(candles)):
                        first = seq_values[0]
                        use_target = alignment.missing_steps and v <= alignment.missing_steps
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
                                actual_last_candle_ts = candles[-1].ts
                                actual_last_idx = len(candles) - 1
                                non_dc_steps = 0
                                for i in range(last_known_idx + 1, actual_last_idx + 1):
                                    if not dc_flags_all[i]:
                                        non_dc_steps += 1
                                steps_from_end = (v - last_known_v) - non_dc_steps
                                pred_ts_dt = actual_last_candle_ts + timedelta(minutes=60 * steps_from_end)
                            else:
                                delta_steps = max(0, v - first)
                                base_ts = alignment.start_ref_ts or alignment.target_ts
                                pred_ts_dt = (base_ts or candles[base_idx].ts) + timedelta(minutes=60 * delta_steps)
                        else:
                            delta_steps = max(0, v - first)
                            base_ts = alignment.target_ts or alignment.start_ref_ts or candles[base_idx].ts
                            pred_ts_dt = base_ts + timedelta(minutes=60 * delta_steps)

                        pred_cell = f"{pred_ts_dt.strftime('%Y-%m-%d %H:%M:%S')} (pred, OC -, PrevOC -)"
                        if show_dc:
                            rows_html.append(f"<tr><td>{v}</td><td>-</td><td>{html.escape(pred_cell)}</td><td>-</td></tr>")
                        else:
                            rows_html.append(f"<tr><td>{v}</td><td>-</td><td>{html.escape(pred_cell)}</td></tr>")
                        continue

                    ts_s = ts.strftime("%Y-%m-%d %H:%M:%S")
                    pip_label = format_pip(candles[idx].close - candles[idx].open)
                    prev_label = format_pip(candles[idx - 1].close - candles[idx - 1].open) if idx - 1 >= 0 else "-"
                    ts_with_pip = f"{ts_s} (OC {pip_label}, PrevOC {prev_label})"
                    if show_dc:
                        dc = dc_flags_all[idx]
                        dc_label = f"{dc}"
                        if hit.used_dc:
                            dc_label += " (rule)"
                        rows_html.append(f"<tr><td>{v}</td><td>{idx}</td><td>{html.escape(ts_with_pip)}</td><td>{dc_label}</td></tr>")
                    else:
                        rows_html.append(f"<tr><td>{v}</td><td>{idx}</td><td>{html.escape(ts_with_pip)}</td></tr>")

                start_target_s = html.escape(alignment.target_ts.strftime('%Y-%m-%d %H:%M:%S')) if alignment.target_ts else "-"
                actual_ts_s = html.escape(alignment.actual_ts.strftime('%Y-%m-%d %H:%M:%S')) if alignment.actual_ts else "-"
                start_idx_s = str(alignment.start_idx) if alignment.start_idx is not None else "-"

                info_lines = [
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>",
                    f"<div><strong>Zaman Dilimi:</strong> 60m</div>",
                    f"<div><strong>Range:</strong> {html.escape(candles[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>",
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>",
                    f"<div><strong>Start:</strong> base(18:00): idx={base_idx} ts={html.escape(candles[base_idx].ts.strftime('%Y-%m-%d %H:%M:%S'))} ({align_status}); offset={off} =&gt; target_ts={start_target_s} ({alignment.offset_status}) idx={start_idx_s} actual_ts={actual_ts_s} missing_steps={alignment.missing_steps}</div>",
                    f"<div><strong>Sequence:</strong> {html.escape(sequence)} {html.escape(str(seq_values))}</div>",
                ]

                header = "<tr><th>Seq</th><th>Index</th><th>Timestamp</th>"
                if show_dc:
                    header += "<th>DC</th>"
                header += "</tr>"

                table = f"<table><thead>{header}</thead><tbody>{''.join(rows_html)}</tbody></table>"
                body = "<div class='card'>" + "".join(info_lines) + "</div>" + table

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page("app321 sonuçlar", body, active_tab="analyze"))
                return

            entry = files[0]
            candles = load_candles_from_text(decode_entry(entry))
            if not candles:
                raise ValueError("Veri boş veya çözümlenemedi")

            if self.path == "/dc":
                candles, tz_label = apply_tz(candles, (form.get("input_tz", {}).get("value") or "UTC-5").strip())
                dc_flags = compute_dc_flags(candles)
                rows_html = []
                count = 0
                for i, c in enumerate(candles):
                    if not dc_flags[i]:
                        continue
                    ts = c.ts.strftime("%Y-%m-%d %H:%M:%S")
                    rows_html.append(f"<tr><td>{i}</td><td>{html.escape(ts)}</td><td>{c.open}</td><td>{c.high}</td><td>{c.low}</td><td>{c.close}</td></tr>")
                    count += 1
                header = "<tr><th>Index</th><th>Timestamp</th><th>Open</th><th>High</th><th>Low</th><th>Close</th></tr>"
                table = f"<table><thead>{header}</thead><tbody>{''.join(rows_html)}</tbody></table>"
                info = (
                    f"<div class='card'>"
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>"
                    f"<div><strong>Zaman Dilimi:</strong> 60m</div>"
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>"
                    f"<div><strong>DC count:</strong> {count}</div>"
                    f"</div>"
                )
                body = info + table
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page("app321 DC List", body, active_tab="dc"))
                return

            if self.path == "/matrix":
                candles, tz_label = apply_tz(candles, (form.get("input_tz", {}).get("value") or "UTC-5").strip())
                seq_mx = (form.get("sequence", {}).get("value") or "S2").strip()
                seq_values = SEQUENCES.get(seq_mx, SEQUENCES["S2"])[:]
                base_idx, align_status = find_start_index(candles, dtime(hour=18, minute=0))
                dc_flags = compute_dc_flags(candles)
                offsets = [-3, -2, -1, 0, 1, 2, 3]
                per_offset = {o: compute_offset_alignment(candles, dc_flags, base_idx, seq_values, o) for o in offsets}

                header_cells = ''.join(f"<th>{'+'+str(o) if o>0 else str(o)}</th>" for o in offsets)
                rows = []
                for idx_seq, v in enumerate(seq_values):
                    cells = [f"<td>{v}</td>"]
                    for o in offsets:
                        alignment = per_offset[o]
                        hit = alignment.hits[idx_seq] if idx_seq < len(alignment.hits) else None
                        idx_hit = hit.idx if hit else None
                        ts_hit = hit.ts if hit else None
                        if idx_hit is not None and ts_hit is not None and 0 <= idx_hit < len(candles):
                            ts_s = ts_hit.strftime('%Y-%m-%d %H:%M:%S')
                            oc_label = format_pip(candles[idx_hit].close - candles[idx_hit].open)
                            prev_label = format_pip(candles[idx_hit - 1].close - candles[idx_hit - 1].open) if idx_hit - 1 >= 0 else "-"
                            label = f"{ts_s} (OC {oc_label}, PrevOC {prev_label})"
                            if hit.used_dc:
                                label += " (DC)"
                            cells.append(f"<td>{html.escape(label)}</td>")
                        else:
                            first = seq_values[0]
                            delta_steps = max(0, v - first)
                            base_ts = alignment.target_ts if alignment.missing_steps and v <= alignment.missing_steps else alignment.start_ref_ts
                            base_ts = base_ts or candles[base_idx].ts
                            ts_pred = (base_ts + timedelta(minutes=60 * delta_steps)).strftime('%Y-%m-%d %H:%M:%S')
                            cells.append(f"<td>{html.escape(ts_pred)} (pred, OC -, PrevOC -)</td>")
                    rows.append(f"<tr>{''.join(cells)}</tr>")

                table = f"<table><thead><tr><th>Seq</th>{header_cells}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
                status_summary = ', '.join(
                    f"{('+' + str(o)) if o > 0 else str(o)}: {per_offset[o].offset_status}"
                    for o in offsets
                )
                info = (
                    f"<div class='card'>"
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>"
                    f"<div><strong>Zaman Dilimi:</strong> 60m</div>"
                    f"<div><strong>Range:</strong> {html.escape(candles[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>"
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>"
                    f"<div><strong>Sequence:</strong> {html.escape(seq_mx)}</div>"
                    f"<div><strong>Offset durumları:</strong> {html.escape(status_summary)}</div>"
                    f"</div>"
                )
                body = info + table
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page("app321 - Matrix", body, active_tab="matrix"))
                return

            # IOU branch
            tz_value = (form.get("input_tz", {}).get("value") or "UTC-5").strip()
            sequence = (form.get("sequence", {}).get("value") or "S2").strip()
            limit_raw = (form.get("limit", {}).get("value") or "0").strip()
            try:
                limit_val = float(limit_raw)
            except Exception:
                limit_val = 0.0
            limit_val = abs(limit_val)
            xyz_enabled = "xyz_mode" in form

            tz_norm = tz_value.upper().replace(" ", "")
            tz_shift = timedelta(hours=1) if tz_norm in {"UTC-5", "UTC-05", "UTC-05:00", "-05:00"} else timedelta(0)
            tz_label = "UTC-5 -> UTC-4 (+1h)" if tz_shift else "UTC-4 -> UTC-4 (+0h)"

            sections: List[str] = []
            for entry in files:
                text = decode_entry(entry)
                name = entry.get("filename") or "uploaded.csv"
                candles_raw = load_candles_from_text(text)
                if not candles_raw:
                    raise ValueError(f"{name}: Veri boş veya çözümlenemedi")
                if tz_shift:
                    candles_use = [
                        Candle(ts=c.ts + tz_shift, open=c.open, high=c.high, low=c.low, close=c.close)
                        for c in candles_raw
                    ]
                else:
                    candles_use = candles_raw

                report = detect_iou_candles(candles_use, sequence, limit_val)
                offset_statuses: List[str] = []
                offset_counts: List[str] = []
                total_hits = 0
                rows: List[str] = []
                offset_has_non_news: Dict[int, bool] = {}
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
                    effective_news = False
                    for ev in news_hits:
                        title = ev.get("title", "")
                        title_html = html.escape(title)
                        if ev.get("all_day"):
                            time_part = "All Day"
                        else:
                            time_part = html.escape(ev.get("time") or "-")
                        line = f"{time_part} {title_html}"
                        if ev.get("window") == "recent-null":
                            line += " (null)"
                        is_holiday = "holiday" in title.lower()
                        if is_holiday:
                            line += " (holiday)"
                        else:
                            effective_news = True
                        detail_lines.append(line)
                    news_cell_html = "Var<br>" + "<br>".join(detail_lines) if detail_lines else "Yok"
                    if xyz_enabled and not effective_news:
                        offset_has_non_news[item.offset] = True
                        rows.append(
                            f"<tr><td>{off_label}</td><td>{hit.seq_value}</td><td>{hit.idx}</td>"
                            f"<td>{html.escape(ts_s)}</td><td>{html.escape(oc_label)}</td>"
                            f"<td>{html.escape(prev_label)}</td><td>{dc_info}</td>"
                            f"<td>{news_cell_html}</td></tr>"
                        )

                xyz_line = ""
                if xyz_enabled:
                    base_offsets = [-3, -2, -1, 0, 1, 2, 3]
                    xyz_offsets = [o for o in base_offsets if not offset_has_non_news.get(o, False)]
                    xyz_text = ", ".join((f"+{o}" if o > 0 else str(o)) for o in xyz_offsets) if xyz_offsets else "-"
                    xyz_line = f"<div><strong>XYZ Kümesi:</strong> {html.escape(xyz_text)}</div>"

                info = (
                    f"<div class='card'>"
                    f"<h3>{html.escape(name)}</h3>"
                    f"<div><strong>Data:</strong> {len(candles_use)} candles</div>"
                    f"<div><strong>Range:</strong> {html.escape(candles_use[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles_use[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>"
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>"
                    f"<div><strong>Sequence:</strong> {html.escape(report.sequence)}</div>"
                    f"<div><strong>Limit:</strong> {report.limit:.5f}</div>"
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

            body = "\n".join(sections)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page("app321 IOU", body, active_tab="iou"))
        except Exception as exc:
            msg = html.escape(str(exc) or "Bilinmeyen hata")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page("Hata", f"<p>Hata: {msg}</p><p><a href='/'>&larr; Geri</a></p>"))

    def log_message(self, format, *args):
        pass


def run(host: str, port: int):
    httpd = HTTPServer((host, port), AppHandler)
    print(f"app321 web: http://{host}:{port}/")
    httpd.serve_forever()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app321.web", description="app321 için basit web arayüzü")
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu adresi (vars: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2019, help="Port (vars: 2019)")
    args = parser.parse_args(argv)
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
