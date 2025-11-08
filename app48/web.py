from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import html
import io
import base64
from typing import List, Optional, Dict, Any, Set, Tuple

from favicon import render_head_links, try_load_asset

from .main import (
    Candle,
    SEQUENCES,
    normalize_key,
    parse_float,
    parse_time_value,
    estimate_timeframe_minutes,
    find_start_index,
    parse_tod,
    compute_dc_flags,
    compute_offset_alignment,
    adjust_to_output_tz,
    insert_synthetic_48m,
    convert_12m_to_48m,
    detect_iou_candles,
)
import csv
from email.parser import BytesParser
from email.policy import default as email_default
from zipfile import ZipFile, ZIP_DEFLATED
from typing import Tuple

from news_loader import find_news_for_timestamp

MINUTES_PER_STEP = 48
IOU_TOLERANCE = 0.005

# --- Örüntüleme Yardımcıları ---

PATTERN_MAX_PATHS = 1000
PATTERN_BEAM_WIDTH = 512

def _fmt_off(v: int) -> str:
    return f"+{v}" if v > 0 else str(v)

def _sign(v: int) -> int:
    if v > 0:
        return 1
    if v < 0:
        return -1
    return 0

def _allowed_values_for_state(state: Dict[str, Any], choices: Set[int], allow_zero_after_start: bool) -> List[int]:
    prev = state.get("prev")
    mode = state.get("mode")  # 'free', 'after_zero', 'triple', 'need_zero'
    sign = state.get("sign")
    direction = state.get("dir")  # 'asc' | 'desc' | None
    pos = state.get("pos")  # 1|2|3|None
    allow_zero_next = bool(state.get("allow_zero_next"))

    allowed: Set[int] = set()
    if mode == "free":
        allowed = set(choices)
    elif mode == "after_zero":
        for k in (1, 3):
            for s in (-1, 1):
                v = s * k
                if v in choices:
                    allowed.add(v)
    elif mode == "triple":
        if pos == 2 and direction is None:
            # ±2 ile başlandıysa sonraki değer 1 veya 3 (aynı işaret)
            for k in (1, 3):
                v = sign * k
                if v in choices:
                    allowed.add(v)
        else:
            if direction == "asc":
                nxt = 2 if pos == 1 else (3 if pos == 2 else None)
            else:  # desc
                nxt = 2 if pos == 3 else (1 if pos == 2 else None)
            if nxt is not None:
                v = sign * nxt
                if v in choices:
                    allowed.add(v)
    elif mode == "need_zero":
        if 0 in choices:
            allowed = {0}

    # Özel kural: ilk adım ±1/±3 ise bir sonraki adımda 0 opsiyonunu da aç
    if allow_zero_after_start and allow_zero_next and 0 in choices:
        allowed.add(0)

    # Ard arda aynı değer yasak
    if prev is not None and prev in allowed:
        allowed.discard(prev)

    order = { -3:0, -2:1, -1:2, 0:3, 1:4, 2:5, 3:6 }
    return sorted(list(allowed), key=lambda v: order.get(v, 99))


def _advance_state(state: Dict[str, Any], value: int, step_idx: int, allow_zero_after_start: bool) -> Dict[str, Any]:
    # Kopya oluştur
    ns = {
        "mode": state.get("mode"),
        "sign": state.get("sign"),
        "dir": state.get("dir"),
        "pos": state.get("pos"),
        "allow_zero_next": False,  # varsayılan reset
        "prev": value,
        "seq": list(state.get("seq") or []) + [value],
    }

    mode = state.get("mode")
    sign = state.get("sign")
    direction = state.get("dir")
    pos = state.get("pos")

    if mode == "free":
        if value == 0:
            ns.update({"mode": "after_zero", "sign": None, "dir": None, "pos": None})
        else:
            s = _sign(value)
            a = abs(value)
            if a == 2:
                ns.update({"mode": "triple", "sign": s, "dir": None, "pos": 2})
            elif a == 1:
                ns.update({"mode": "triple", "sign": s, "dir": "asc", "pos": 1})
            elif a == 3:
                ns.update({"mode": "triple", "sign": s, "dir": "desc", "pos": 3})
            # Özel kural (sadece global ilk adım için): sonraki adıma 0 izni
            if allow_zero_after_start and a in (1, 3) and step_idx == 0:
                ns["allow_zero_next"] = True
    elif mode == "after_zero":
        # Sadece ±1/±3 ile yeni üçlü başlar
        s = _sign(value)
        a = abs(value)
        if a == 1:
            ns.update({"mode": "triple", "sign": s, "dir": "asc", "pos": 1})
        elif a == 3:
            ns.update({"mode": "triple", "sign": s, "dir": "desc", "pos": 3})
    elif mode == "triple":
        if value == 0 and state.get("allow_zero_next"):
            # İlk adım ±1/±3 sonrası 0 istisnası
            ns.update({"mode": "after_zero", "sign": None, "dir": None, "pos": None})
        else:
            s = sign
            a = abs(value)
            if pos == 2 and direction is None:
                # 2'den 1 veya 3'e geçiş tamamlanınca 0 beklenir
                if a == 1:
                    ns.update({"mode": "need_zero", "sign": s, "dir": "desc", "pos": 1})
                elif a == 3:
                    ns.update({"mode": "need_zero", "sign": s, "dir": "asc", "pos": 3})
            else:
                if direction == "asc":
                    if pos == 1 and a == 2:
                        ns.update({"mode": "triple", "sign": s, "dir": "asc", "pos": 2})
                    elif pos == 2 and a == 3:
                        ns.update({"mode": "need_zero", "sign": s, "dir": "asc", "pos": 3})
                else:  # desc
                    if pos == 3 and a == 2:
                        ns.update({"mode": "triple", "sign": s, "dir": "desc", "pos": 2})
                    elif pos == 2 and a == 1:
                        ns.update({"mode": "need_zero", "sign": s, "dir": "desc", "pos": 1})
    elif mode == "need_zero":
        if value == 0:
            ns.update({"mode": "after_zero", "sign": None, "dir": None, "pos": None})

    return ns


def build_patterns_from_xyz_lists(xyz_sets: List[Set[int]], allow_zero_after_start: bool, max_paths: int = PATTERN_MAX_PATHS, beam_width: int = PATTERN_BEAM_WIDTH) -> List[List[int]]:
    if not xyz_sets:
        return []
    # Başlangıç durumu
    states: List[Dict[str, Any]] = [{
        "mode": "free",
        "sign": None,
        "dir": None,
        "pos": None,
        "allow_zero_next": False,
        "prev": None,
        "seq": [],
    }]

    for idx, choices in enumerate(xyz_sets):
        next_states: List[Dict[str, Any]] = []
        for st in states:
            allowed = _allowed_values_for_state(st, choices, allow_zero_after_start)
            if not allowed:
                continue
            for v in allowed:
                ns = _advance_state(st, v, idx, allow_zero_after_start)
                next_states.append(ns)
                if len(next_states) >= beam_width:
                    # Basit beam budaması
                    break
            if len(next_states) >= beam_width:
                break
        states = next_states
        if not states:
            break

    results: List[List[int]] = [st["seq"] for st in states if len(st.get("seq", [])) == len(xyz_sets)]
    return results[:max_paths]


def render_pattern_panel(
    xyz_sets: List[Set[int]],
    allow_zero_after_start: bool,
    file_names: Optional[List[str]] = None,
    joker_indices: Optional[Set[int]] = None,
    sequence_name: Optional[str] = None,
) -> str:
    patterns = build_patterns_from_xyz_lists(xyz_sets, allow_zero_after_start=allow_zero_after_start)
    if not patterns:
        return "<div class='card'><h3>Örüntüleme</h3><div>Örüntü bulunamadı.</div></div>"
    def _build_state_for_seq(seq: List[int]) -> Dict[str, Any]:
        st: Dict[str, Any] = {
            "mode": "free",
            "sign": None,
            "dir": None,
            "pos": None,
            "allow_zero_next": False,
            "prev": None,
            "seq": [],
        }
        for i, v in enumerate(seq):
            st = _advance_state(st, v, i, allow_zero_after_start)
        return st

    domain = {-3, -2, -1, 0, 1, 2, 3}
    # 1) Üçlü kümeleri (0'sız) ve dosya uyumunu baz alarak blok rengi ata (başlangıç index'i -> renk)
    triple_starts: Dict[Tuple[int, int], str] = {}
    if file_names and len(file_names) >= 3:
        triples: Dict[Tuple[int, int, int, str, str, str], List[Tuple[int, int]]] = {}
        for li, seq in enumerate(patterns):
            for i in range(0, max(0, len(seq) - 2)):
                a, b, c = seq[i], seq[i+1], seq[i+2]
                if 0 in (a, b, c):
                    continue
                f1 = file_names[i] if i < len(file_names) else None
                f2 = file_names[i+1] if i+1 < len(file_names) else None
                f3 = file_names[i+2] if i+2 < len(file_names) else None
                if not (f1 and f2 and f3):
                    continue
                key = (a, b, c, f1, f2, f3)
                triples.setdefault(key, []).append((li, i))
        # Renkleri ata (yalnız en az 2 yerde geçen üçlüler)
        for key, occs in triples.items():
            if len(occs) < 2:
                continue
            s = str(key)
            try:
                import hashlib
                hv = int(hashlib.md5(s.encode('utf-8')).hexdigest()[:6], 16)
            except Exception:
                hv = abs(hash(s))
            hue = hv % 360
            # Daha saydam bir opaklık: alpha ~ 0.28, biraz daha koyu lightness ile
            color = f"hsla({hue}, 85%, 60%, 0.28)"
            for li, i in occs:
                # Başlangıç konumuna rengi ata (çakışmalarda ilk kazanır)
                triple_starts.setdefault((li, i), color)

    lines: List[str] = []
    for idx_line, seq in enumerate(patterns):
        parts: List[str] = []
        i = 0
        while i < len(seq):
            name = None
            if file_names and 0 <= i < len(file_names):
                name = file_names[i]
            tip = name or ""
            if joker_indices and i in joker_indices:
                tip = (tip + " (Joker)").strip()
            v = seq[i]
            token = html.escape(_fmt_off(v))
            if tip:
                def token_html(idx:int) -> str:
                    nm = file_names[idx] if file_names and 0 <= idx < len(file_names) else ""
                    tp = nm or ""
                    if joker_indices and idx in joker_indices:
                        tp = (tp + " (Joker)").strip()
                    tk = html.escape(_fmt_off(seq[idx]))
                    if tp:
                        return (
                            f"<span class='pat-token' title='{html.escape(tp)}' data-tip='{html.escape(tp)}'>{tk}</span>"
                        )
                    return f"<span class='pat-token'>{tk}</span>"
            else:
                def token_html(idx:int) -> str:
                    tk = html.escape(_fmt_off(seq[idx]))
                    return f"<span class='pat-token'>{tk}</span>"

            # Eğer bu pozisyon üçlü başlangıcı ise, üç tokenı ve iki virgülü tek blokta boya
            color = triple_starts.get((idx_line, i))
            if color and i + 2 < len(seq):
                block = (
                    f"<span style='background-color:{html.escape(color)}; border-radius:4px; padding:0 3px;'>"
                    f"{token_html(i)}, {token_html(i+1)}, {token_html(i+2)}"
                    f"</span>"
                )
                parts.append(block)
                i += 3
                if i < len(seq):
                    parts.append(", ")
                continue
            # aksi halde tek token
            parts.append(token_html(i))
            i += 1
            if i < len(seq):
                parts.append(", ")
        label = ", ".join(parts)
        st = _build_state_for_seq(seq)
        opts = _allowed_values_for_state(st, domain, allow_zero_after_start)
        cont = ", ".join(_fmt_off(v) for v in opts) if opts else "-"
        lines.append(f"<div class='pat-line'>{label} (devam: {html.escape(cont)})</div>")
    # Son değerlerin özeti (benzersiz, sıralı)
    last_vals = [seq[-1] for seq in patterns if seq]
    order = { -3:0, -2:1, -1:2, 0:3, 1:4, 2:5, 3:6 }
    unique_last_sorted = []
    seen = set()
    for v in sorted(last_vals, key=lambda x: order.get(x, 99)):
        if v not in seen:
            seen.add(v)
            unique_last_sorted.append(v)
    last_line = "<div><strong>Son değerler:</strong> " + (
        ", ".join(_fmt_off(v) for v in unique_last_sorted) if unique_last_sorted else "-"
    ) + "</div>"
    info = f"<div><strong>Toplam örüntü:</strong> {len(patterns)} (ilk {min(len(patterns), PATTERN_MAX_PATHS)})</div>"
    seq_info = f"<div><strong>Sequence:</strong> {html.escape(sequence_name)}</div>" if sequence_name else ""
    return "<div class='card'><h3>Örüntüleme</h3>" + info + seq_info + last_line + "".join(lines) + "</div>"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_FILES = 50

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


def format_price(value: float) -> str:
    s = f"{value:.6f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


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
      /* Pattern tokens */
      .pat-line{{margin:2px 0;}}
      .pat-token{{display:inline-block; padding:0 2px; border-bottom:1px dashed #aaa; cursor:help; position:relative;}}
      .pat-token[data-tip]:hover::after{{
        content: attr(data-tip);
        position:absolute;
        bottom:100%;
        left:0;
        transform: translateY(-4px);
        background:rgba(0,0,0,0.85);
        color:#fff;
        padding:4px 6px;
        border-radius:4px;
        white-space:nowrap;
        font-size:12px;
        z-index:20;
      }}
    </style>
  </head>
  <body>
    <header>
      <h2>app48</h2>
    </header>
    <nav class='tabs'>
      <a href='/' class='{ 'active' if active_tab=="analyze" else '' }'>Analiz</a>
      <a href='/convert' class='{ 'active' if active_tab=="convert" else '' }'>12-48</a>
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
            <div>48m</div>
          </div>
          <div>
            <label>Dizi</label>
            <select name='sequence'>
              <option value='S1' selected>S1</option>
              <option value='S2'>S2</option>
            </select>
          </div>
          <div>
            <label>Girdi TZ</label>
            <select name='input_tz'>
              <option value='UTC-5'>UTC-5</option>
              <option value='UTC-4' selected>UTC-4</option>
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
    return page("app48", body, active_tab="analyze")


def render_convert_index() -> bytes:
    body = """
    <div class='card'>
      <form method='post' action='/convert' enctype='multipart/form-data'>
        <div class='row'>
          <div>
            <label>CSV (12m, UTC-5)</label>
            <input type='file' name='csv' accept='.csv,text/csv' required multiple />
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>48m'e Dönüştür</button>
        </div>
      </form>
    </div>
    <p>Girdi UTC-5 12 dakikalık mumlar olmalıdır. Çıktı UTC-4 48 dakikalık mumlar olarak indirilir (4 × 12m = 1 × 48m).</p>
    <p>Birden fazla CSV seçersen her biri 48m'e dönüştürülür; birden fazla dosya seçildiğinde sonuçlar ZIP paketi olarak indirilir.</p>
    """
    return page("app48 - 12m to 48m", body, active_tab="convert")


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
            <div>48m</div>
          </div>
          <div>
            <label>Girdi TZ</label>
            <select name='input_tz'>
              <option value='UTC-5'>UTC-5</option>
              <option value='UTC-4' selected>UTC-4</option>
            </select>
          </div>
          <div>
            <label>Filtre</label>
            <div>
              <label><input type='checkbox' name='only_syn' /> Yalnız sentetik</label>
              <label><input type='checkbox' name='only_real' /> Yalnız gerçek</label>
            </div>
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>DC'leri Listele</button>
        </div>
      </form>
    </div>
    <p>Not: DC tespiti 48m akışına göre, sentetik 18:00 & 18:48 eklenerek yapılır.</p>
    """
    return page("app48 - DC List", body, active_tab="dc")


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
            <div>48m</div>
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
          <button type='submit'>Oluştur</button>
        </div>
      </form>
    </div>
    <p>Matrix: Tüm offsetler (-3..+3) için saatler ve (veri yoksa) tahminler.</p>
    """
    return page("app48 - Matrix", body, active_tab="matrix")


def render_iou_form() -> str:
    """IOU formunu HTML string olarak döndürür (sonuç sayfasında kullanım için)"""
    return """
    <div class='card'>
      <form method='post' action='/iou' enctype='multipart/form-data'>
        <div class='row'>
          <div>
            <label>CSV</label>
            <input type='file' name='csv' accept='.csv,text/csv' required multiple />
          </div>
          <div>
            <label>Zaman Dilimi</label>
            <div>48m</div>
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
          <label style='display:flex; align-items:center; gap:8px;'>
            <input type='checkbox' name='pattern_mode' />
            <span>Örüntüleme</span>
          </label>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>IOU Tara</button>
        </div>
      </form>
    </div>
    <p>IOU taraması, limit eşiğini aşan ve aynı işaretli OC/PrevOC değerlerine sahip mumları gösterir. Birden fazla CSV seçebilir, sonuçları dosya bazlı inceleyebilirsin.</p>
    """


def render_iou_index() -> bytes:
    body = render_iou_form()
    return page("app48 - IOU", body, active_tab="iou")


class AppHandler(BaseHTTPRequestHandler):
    server_version = "Candles48/1.0"
    sys_version = ""
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
                container = fields.setdefault(name, {"files": []})
                container.setdefault("files", []).append(entry)
                if "data" not in container:
                    container["data"] = payload
                    container["filename"] = filename
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
            _add_security_headers(self)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if self.path.startswith("/dc"):
            body = render_dc_index()
        elif self.path.startswith("/convert"):
            body = render_convert_index()
        elif self.path.startswith("/matrix"):
            body = render_matrix_index()
        elif self.path.startswith("/iou"):
            body = render_iou_index()
        else:
            body = render_index()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        _add_security_headers(self)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/analyze", "/dc", "/matrix", "/convert", "/iou"):
            self.send_error(404)
            return
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
            form = self._parse_multipart()

            file_item = form.get("csv")
            if not file_item or "data" not in file_item:
                raise ValueError("CSV yüklenmedi")

            raw = file_item["data"]
            text = raw.decode("utf-8", errors="replace")

            files_list = file_item.get("files") or [{"filename": file_item.get("filename"), "data": file_item.get("data")}]  # type: ignore[arg-type]
            if len(files_list) > MAX_FILES:
                self.send_response(413)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(b"Too many files (max 50).")
                return

            sequence = (form.get("sequence", {}).get("value") or "S1").strip()
            tz_s = (form.get("input_tz", {}).get("value") or "UTC-4").strip()
            offset_s = (form.get("offset", {}).get("value") or "0").strip() if self.path == "/analyze" else "0"
            show_dc = ("show_dc" in form) if self.path == "/analyze" else False
            only_syn = ("only_syn" in form) if self.path == "/dc" else False
            only_real = ("only_real" in form) if self.path == "/dc" else False

            candles = load_candles_from_text(text)
            if not candles:
                raise ValueError("Veri boş veya çözümlenemedi")

            if self.path == "/convert":
                outputs: List[Tuple[str, bytes]] = []
                used_names: set[str] = set()

                for entry in files_list:
                    entry_data = entry.get("data")
                    if isinstance(entry_data, (bytes, bytearray)):
                        text_entry = entry_data.decode("utf-8", errors="replace")
                    else:
                        text_entry = str(entry_data)
                    try:
                        candles_entry = load_candles_from_text(text_entry)
                    except ValueError as exc:
                        name = entry.get("filename") or "dosya"
                        raise ValueError(f"{name}: {exc}")
                    if not candles_entry:
                        name = entry.get("filename") or "dosya"
                        raise ValueError(f"{name}: Veri boş veya çözümlenemedi")
                    tf_est = estimate_timeframe_minutes(candles_entry)
                    if tf_est is None or abs(tf_est - 12) > 0.6:
                        name = entry.get("filename") or "dosya"
                        raise ValueError(f"{name}: Girdi 12 dakikalık akış gibi görünmüyor")
                    shifted, _ = adjust_to_output_tz(candles_entry, "UTC-5")
                    converted = convert_12m_to_48m(shifted)

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
                    data_bytes = buffer.getvalue().encode("utf-8")
                    download_name = _sanitize_csv_filename(entry.get("filename") or "converted", "_48m.csv")
                    counter = 1
                    while download_name in used_names:
                        stem, ext = (download_name.rsplit(".", 1) + [""])[:2]
                        download_name = (stem[:100] or "converted") + f"_{counter}." + (ext or "csv")
                        counter += 1
                    used_names.add(download_name)
                    outputs.append((download_name, data_bytes))

                if len(outputs) == 1:
                    download_name, data_bytes = outputs[0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
                    self.send_header("Content-Length", str(len(data_bytes)))
                    _add_security_headers(self)
                    self.end_headers()
                    self.wfile.write(data_bytes)
                    return

                bundle = io.BytesIO()
                with ZipFile(bundle, "w", ZIP_DEFLATED) as zf:
                    for name, payload in outputs:
                        zf.writestr(name, payload)
                zip_bytes = bundle.getvalue()
                bundle_name = "converted_48m_bundle.zip"

                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{bundle_name}"')
                self.send_header("Content-Length", str(len(zip_bytes)))
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(zip_bytes)
                return

            if self.path == "/iou":
                limit_raw = (form.get("limit", {}).get("value") or "0").strip()
                tol_raw = (form.get("tolerance", {}).get("value") or str(IOU_TOLERANCE)).strip()
                xyz_enabled = "xyz_mode" in form
                summary_mode = "xyz_summary" in form
                pattern_enabled = "pattern_mode" in form
                confirm_iou = "confirm_iou" in form
                
                # Önceki sonuçları al (eğer varsa)
                previous_results_html = form.get("previous_results_html", {}).get("value", "")
                if previous_results_html:
                    try:
                        previous_results_html = base64.b64decode(previous_results_html.encode("ascii")).decode("utf-8")
                    except Exception:
                        previous_results_html = ""
                
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

                sequence = (form.get("sequence", {}).get("value") or "S1").strip() or "S1"
                tz_value = tz_s or "UTC-5"

                # Alternatif (2. adım) dosya içeriği: csv_b64_{i} + csv_name_{i}
                b64_entries: List[Dict[str, Any]] = []
                k = 0
                while True:
                    kb = f"csv_b64_{k}"
                    kn = f"csv_name_{k}"
                    if kb in form and kn in form:
                        b64_val = form[kb].get("value") or ""
                        name_val = form[kn].get("value") or f"uploaded_{k}.csv"
                        try:
                            data_bytes = base64.b64decode(b64_val.encode("ascii"), validate=True)
                        except Exception:
                            data_bytes = b64_val.encode("utf-8", errors="replace")
                        b64_entries.append({"filename": name_val, "data": data_bytes})
                        k += 1
                        continue
                    break

                # İlk adım: Joker seçimi ekranı
                if not confirm_iou and files_list:
                    idx = 0
                    hidden_fields: List[str] = []
                    file_rows: List[str] = []
                    for entry in files_list:
                        name = entry.get("filename") or f"uploaded_{idx}.csv"
                        raw_bytes = entry.get("data")
                        if isinstance(raw_bytes, str):
                            raw_bytes = raw_bytes.encode("utf-8", errors="replace")
                        b64 = base64.b64encode(raw_bytes or b"").decode("ascii")
                        hidden_fields.append(f"<input type='hidden' name='csv_b64_{idx}' value='{html.escape(b64)}'>")
                        hidden_fields.append(f"<input type='hidden' name='csv_name_{idx}' value='{html.escape(name)}'>")
                        file_rows.append(
                            f"<tr><td>{idx+1}</td><td>{html.escape(name)}</td>"
                            f"<td><label style='display:flex;gap:8px;align-items:center;'><input type='checkbox' name='joker_{idx}' /> Joker</label></td></tr>"
                        )
                        idx += 1

                    def _hidden_bool(name: str, enabled: bool) -> str:
                        return f"<input type='hidden' name='{name}' value='1'>" if enabled else ""

                    preserved = [
                        f"<input type='hidden' name='sequence' value='{html.escape(sequence)}'>",
                        f"<input type='hidden' name='input_tz' value='{html.escape(tz_value)}'>",
                        f"<input type='hidden' name='limit' value='{html.escape(str(limit_val))}'>",
                        f"<input type='hidden' name='tolerance' value='{html.escape(str(tolerance_val))}'>",
                        _hidden_bool("xyz_mode", xyz_enabled),
                        _hidden_bool("xyz_summary", summary_mode),
                        _hidden_bool("pattern_mode", pattern_enabled),
                        "<input type='hidden' name='confirm_iou' value='1'>",
                    ]
                    
                    # Önceki sonuçları da koru (eğer varsa)
                    if previous_results_html:
                        # previous_results_html zaten decode edilmiş durumda
                        encoded = base64.b64encode(previous_results_html.encode("utf-8")).decode("ascii")
                        preserved.append(f"<input type='hidden' name='previous_results_html' value='{html.escape(encoded)}'>")

                    table = (
                        "<table><thead><tr><th>#</th><th>Dosya</th><th>Joker</th></tr></thead>"
                        f"<tbody>{''.join(file_rows)}</tbody></table>"
                    )
                    
                    # Önceki sonuçları da göster (eğer varsa)
                    previous_section = ""
                    if previous_results_html:
                        # previous_results_html zaten body_without_form'dan geliyor, formlar yok
                        previous_section = (
                            "<div style='margin-bottom:32px; padding-bottom:24px; border-bottom:2px solid #ddd;'>"
                            "<h3 style='color:#888; margin-bottom:16px;'>Önceki Analizler</h3>"
                            f"{previous_results_html}"
                            "</div>"
                        )
                    
                    body = (
                        previous_section +
                        "<div class='card'>"
                        "<h3>Joker Seçimi</h3>"
                        "<div>Analize başlamadan önce 'Joker' dosyaları seç. Joker dosyalar XYZ kümesinde tüm offsetleri (-3..+3) içerir.</div>"
                        f"<form method='post' action='/iou' enctype='multipart/form-data'>"
                        + table
                        + "".join(hidden_fields + preserved)
                        + "<div style='margin-top:12px;'><button type='submit'>Analizi Başlat</button></div>"
                        + "</form>"
                        + "</div>"
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    _add_security_headers(self)
                    self.end_headers()
                    self.wfile.write(page("app48 IOU - Joker Seçimi", body, active_tab="iou"))
                    return

                effective_entries = b64_entries if b64_entries else files_list
                joker_indices: Set[int] = set()
                j = 0
                # varsa joker_* işaretlerini topla
                while True:
                    key = f"joker_{j}"
                    if key in form:
                        joker_indices.add(j)
                        j += 1
                        continue
                    # limit: mevcut entry sayısına kadar dene
                    if j < len(effective_entries):
                        j += 1
                        continue
                    break

                sections: List[str] = []
                summary_entries: List[Dict[str, Any]] = []
                all_xyz_sets: List[Set[int]] = []
                all_file_names: List[str] = []
                start_tod = parse_tod("18:00")

                for idx_entry, entry in enumerate(effective_entries):
                    entry_data = entry.get("data")
                    if isinstance(entry_data, (bytes, bytearray)):
                        text_entry = entry_data.decode("utf-8", errors="replace")
                    else:
                        text_entry = str(entry_data)
                    candles_entry = load_candles_from_text(text_entry)
                    name = entry.get("filename") or "uploaded.csv"
                    if not candles_entry:
                        raise ValueError(f"{name}: Veri boş veya çözümlenemedi")

                    candles_norm, tz_label_entry = adjust_to_output_tz(candles_entry, tz_value)
                    base_idx_entry, align_status = find_start_index(candles_norm, start_tod)
                    start_day = candles_norm[base_idx_entry].ts.date() if 0 <= base_idx_entry < len(candles_norm) else None
                    candles_syn, added = insert_synthetic_48m(candles_norm, start_day)

                    report = detect_iou_candles(candles_syn, sequence, limit_val, tolerance=tolerance_val)

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
                            tag = "syn" if getattr(hit, "synthetic", False) else "real"
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
                                    offset_has_non_news[item.offset] = True
                                    elim_label = f"{hit.ts.strftime('%Y-%m-%d %H:%M:%S')} (seq {hit.seq_value}, {tag})"
                                    bucket = offset_eliminations.setdefault(item.offset, [])
                                    if elim_label not in bucket:
                                        bucket.append(elim_label)
                            rows.append(
                                f"<tr><td>{off_label}</td><td>{hit.seq_value}</td><td>{hit.idx}</td>"
                                f"<td>{html.escape(ts_s)}</td><td>{html.escape(oc_label)}</td>"
                                f"<td>{html.escape(prev_label)}</td><td>{tag}</td><td>{dc_info}</td>"
                                f"<td>{news_cell_html}</td></tr>"
                            )

                    base_offsets = [-3, -2, -1, 0, 1, 2, 3]
                    xyz_offsets = [o for o in base_offsets if not offset_has_non_news.get(o, False)] if xyz_enabled else base_offsets
                    # Joker: XYZ tam kapsam
                    if idx_entry in joker_indices:
                        xyz_offsets = base_offsets[:]
                    xyz_text = ", ".join((f"+{o}" if o > 0 else str(o)) for o in xyz_offsets) if xyz_offsets else "-"
                    all_xyz_sets.append(set(xyz_offsets))
                    all_file_names.append(name)

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
                            "elimination_html": elimination_cell,
                        })
                    else:
                        xyz_line = ""
                        if xyz_enabled:
                            joker_tag = " (Joker)" if idx_entry in joker_indices else ""
                            xyz_line = f"<div><strong>XYZ Kümesi{joker_tag}:</strong> {html.escape(xyz_text)}</div>"

                        info = (
                            f"<div class='card'>"
                            f"<h3>{html.escape(name)}</h3>"
                            f"<div><strong>Data:</strong> {len(candles_syn)} candles</div>"
                            f"<div><strong>Range:</strong> {html.escape(candles_syn[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles_syn[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>"
                            f"<div><strong>TZ:</strong> {html.escape(tz_label_entry)}</div>"
                            f"<div><strong>Sequence:</strong> {html.escape(report.sequence)}</div>"
                            f"<div><strong>Limit:</strong> {report.limit:.5f}</div>"
                            f"<div><strong>Tolerans:</strong> {tolerance_val:.5f}</div>"
                            f"<div><strong>Base(18:00):</strong> idx={report.base_idx} status={html.escape(report.base_status)} ts={html.escape(report.base_ts.strftime('%Y-%m-%d %H:%M:%S')) if report.base_ts else '-'} </div>"
                            f"<div><strong>Offset durumları:</strong> {html.escape(', '.join(offset_statuses)) if offset_statuses else '-'} </div>"
                            f"<div><strong>Offset IOU sayıları:</strong> {html.escape(', '.join(offset_counts)) if offset_counts else '-'} </div>"
                            f"<div><strong>Toplam IOU:</strong> {total_hits}</div>"
                            f"{xyz_line}"
                            f"<div><strong>Sentetik eklenen:</strong> {added}</div>"
                            f"</div>"
                        )

                        if rows:
                            table = "<table><thead><tr><th>Offset</th><th>Seq</th><th>Index</th><th>Timestamp</th><th>OC</th><th>PrevOC</th><th>Tag</th><th>DC</th><th>Haber</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
                        else:
                            table = "<p>IOU mum bulunamadı.</p>"

                        sections.append(info + table)

                if summary_mode:
                    header = "<tr><th>Dosya</th><th>XYZ Kümesi</th><th>Elenen Offsetler</th></tr>"
                    rows_summary = []
                    for entry in summary_entries:
                        elim_html = entry["elimination_html"] or "-"
                        rows_summary.append(
                            "<tr>"
                            f"<td>{html.escape(entry['name'])}</td>"
                            f"<td>{html.escape(entry['xyz_text'])}</td>"
                            f"<td>{elim_html}</td>"
                            "</tr>"
                        )
                    table = "<table><thead>" + header + "</thead><tbody>" + "".join(rows_summary) + "</tbody></table>"
                    pattern_html = render_pattern_panel(
                        all_xyz_sets,
                        allow_zero_after_start=True,
                        file_names=all_file_names,
                        joker_indices=joker_indices,
                        sequence_name=sequence,
                    ) if pattern_enabled else ""
                    current_result = "<div class='card'>" + table + "</div>" + pattern_html
                else:
                    # Non-summary: tüm dosya kartları + varsa örüntü paneli
                    if pattern_enabled:
                        sections.append(
                            render_pattern_panel(
                                all_xyz_sets,
                                allow_zero_after_start=True,
                                file_names=all_file_names,
                                joker_indices=joker_indices,
                                sequence_name=sequence,
                            )
                        )
                    current_result = "\n".join(sections)
                
                # Yeni analiz sonucunu bir bölüm içine al
                from datetime import datetime
                result_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                result_section = (
                    f"<div id='result_{result_id}' style='margin-bottom:32px; padding-bottom:24px; border-bottom:2px solid #ddd;'>"
                    f"<h3 style='color:#0366d6; margin-bottom:16px;'>Analiz #{result_id}</h3>"
                    f"{current_result}"
                    f"</div>"
                )
                
                # Önceki sonuçları ve yeni sonucu birleştir (ilk analiz üstte kalsın)
                if previous_results_html:
                    body_without_form = previous_results_html + result_section
                else:
                    body_without_form = result_section
                
                # Sonuçların altına tekrar IOU formunu ekle (önceki sonuçları da hidden field olarak taşı)
                # Önce body_without_form'u encode edip sakla (form eklenmeden önceki hali)
                body_encoded = base64.b64encode(body_without_form.encode("utf-8")).decode("ascii")
                
                form_html = render_iou_form()
                # Form içindeki form tag'ini kaldırıp sadece içeriği al
                form_content = form_html.replace("<form method='post' action='/iou' enctype='multipart/form-data'>", "").replace("</form>", "").strip()
                
                form_section = (
                    "<hr style='margin:32px 0; border:none; border-top:2px solid #ddd;'>"
                    "<h2 style='margin-top:24px;'>Yeni Analiz</h2>"
                    "<div class='card'>"
                    "<form method='post' action='/iou' enctype='multipart/form-data'>"
                    f"<input type='hidden' name='previous_results_html' value='{body_encoded}'>"
                    + form_content +
                    "</form>"
                    "</div>"
                )
                
                # Final body: önceki sonuçlar + yeni sonuç + form
                body = body_without_form + form_section
                
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(page("app48 IOU", body, active_tab="iou"))
                return

            # Normalize to UTC-4 if needed
            candles, tz_label = adjust_to_output_tz(candles, tz_s)

            # Analiz/matrix için sentetik ekleme
            start_tod = parse_tod("18:00")
            base_idx, align_status = find_start_index(candles, start_tod)
            start_day = candles[base_idx].ts.date() if 0 <= base_idx < len(candles) else None
            candles, added = insert_synthetic_48m(candles, start_day)

            if self.path == "/analyze":
                # Re-find after insertion
                base_idx, align_status = find_start_index(candles, start_tod)
                try:
                    off = int(offset_s)
                except Exception:
                    off = 0
                if off < -3 or off > 3:
                    off = 0
                seq_values = SEQUENCES.get(sequence, SEQUENCES["S2"])[:]

                dc_flags_all = compute_dc_flags(candles)
                alignment = compute_offset_alignment(candles, dc_flags_all, base_idx, seq_values, off, minutes_per_step=48)
                start_idx = alignment.start_idx
                start_ref_ts = alignment.start_ref_ts

                def predicted_ts_for(v: int) -> str:
                    # 48m prediction - DC'leri dikkate al
                    first = seq_values[0]
                    use_target = alignment.missing_steps and v <= alignment.missing_steps
                    
                    if not use_target:
                        # Son dizideki bilinen değeri bul
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
                            # DC'leri dikkate al
                            non_dc_steps_from_last_known_to_end = 0
                            for i in range(last_known_idx + 1, actual_last_idx + 1):
                                is_dc = dc_flags_all[i] if i < len(dc_flags_all) else False
                                if not is_dc:
                                    non_dc_steps_from_last_known_to_end += 1
                            steps_from_end_to_v = (v - last_known_v) - non_dc_steps_from_last_known_to_end
                            return (actual_last_candle_ts + __import__('datetime').timedelta(minutes=48 * steps_from_end_to_v)).strftime("%Y-%m-%d %H:%M:%S")
                    
                    delta_steps = max(0, v - first)
                    base_ts = alignment.target_ts if use_target else start_ref_ts
                    return (base_ts + __import__('datetime').timedelta(minutes=48*delta_steps)).strftime("%Y-%m-%d %H:%M:%S")

                rows_html = []
                for v, hit in zip(seq_values, alignment.hits):
                    idx = hit.idx
                    ts = hit.ts
                    if idx is None or ts is None or not (0 <= idx < len(candles)):
                        pred_ts = predicted_ts_for(v)
                        pred_cell = f"{pred_ts} (pred, OC -, PrevOC -)"
                        if show_dc:
                            rows_html.append(f"<tr><td>{v}</td><td>-</td><td>{html.escape(pred_cell)}</td><td>-</td></tr>")
                        else:
                            rows_html.append(f"<tr><td>{v}</td><td>-</td><td>{html.escape(pred_cell)}</td></tr>")
                        continue
                    ts_s = ts.strftime("%Y-%m-%d %H:%M:%S")
                    syn_tag = " <em>(syn)</em>" if hit.synthetic else ""
                    pip_label = format_pip(candles[idx].close - candles[idx].open)
                    prev_label = format_pip(candles[idx - 1].close - candles[idx - 1].open) if idx - 1 >= 0 else "-"
                    ts_with_pip = f"{ts_s} (OC {pip_label}, PrevOC {prev_label}){syn_tag}"
                    if show_dc:
                        dc = dc_flags_all[idx]
                        dc_label = f"{dc}"
                        if hit.used_dc:
                            dc_label += " (rule)"
                        rows_html.append(f"<tr><td>{v}</td><td>{idx}</td><td>{html.escape(ts_with_pip)}</td><td>{dc_label}</td></tr>")
                    else:
                        rows_html.append(f"<tr><td>{v}</td><td>{idx}</td><td>{html.escape(ts_with_pip)}</td></tr>")

                target_s = html.escape(alignment.target_ts.strftime('%Y-%m-%d %H:%M:%S')) if alignment.target_ts else "-"
                actual_s = html.escape(alignment.actual_ts.strftime('%Y-%m-%d %H:%M:%S')) if alignment.actual_ts else "-"
                start_idx_s = str(start_idx) if start_idx is not None else "-"

                info_lines = [
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>",
                    f"<div><strong>Zaman Dilimi:</strong> 48m</div>",
                    f"<div><strong>Range:</strong> {html.escape(candles[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>",
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>",
                    f"<div><strong>Synthetic:</strong> inserted {added} candles (days after start)</div>",
                    f"<div><strong>Start:</strong> base_idx={base_idx} ts={html.escape(candles[base_idx].ts.strftime('%Y-%m-%d %H:%M:%S'))} ({align_status}); offset={off} =&gt; target_ts={target_s} ({alignment.offset_status}) idx={start_idx_s} actual_ts={actual_s} missing_steps={alignment.missing_steps}</div>",
                    f"<div><strong>Sequence:</strong> {html.escape(sequence)} {html.escape(str(seq_values))}</div>",
                ]

                if show_dc:
                    header = "<tr><th>Seq</th><th>Index</th><th>Timestamp</th><th>DC</th></tr>"
                else:
                    header = "<tr><th>Seq</th><th>Index</th><th>Timestamp</th></tr>"

                table = f"<table><thead>{header}</thead><tbody>{''.join(rows_html)}</tbody></table>"
                body = "<div class='card'>" + "".join(info_lines) + "</div>" + table
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(page("app48 sonuçlar", body, active_tab="analyze"))
            elif self.path == "/dc":
                # DC list branch
                flags = compute_dc_flags(candles)
                rows_html = []
                count = 0
                for i, c in enumerate(candles):
                    if not flags[i]:
                        continue
                    if only_syn and not getattr(c, "synthetic", False):
                        continue
                    if only_real and getattr(c, "synthetic", False):
                        continue
                    tag = "syn" if getattr(c, "synthetic", False) else "real"
                    ts = c.ts.strftime("%Y-%m-%d %H:%M:%S")
                    rows_html.append(f"<tr><td>{i}</td><td>{html.escape(ts)}</td><td>{tag}</td><td>{c.open}</td><td>{c.high}</td><td>{c.low}</td><td>{c.close}</td></tr>")
                    count += 1

                header = "<tr><th>Index</th><th>Timestamp</th><th>Tag</th><th>Open</th><th>High</th><th>Low</th><th>Close</th></tr>"
                table = f"<table><thead>{header}</thead><tbody>{''.join(rows_html)}</tbody></table>"
                info = (
                    f"<div class='card'>"
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>"
                    f"<div><strong>Zaman Dilimi:</strong> 48m</div>"
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>"
                    f"<div><strong>DC count:</strong> {count}</div>"
                    f"</div>"
                )
                body = info + table
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(page("app48 DC List", body, active_tab="dc"))
            elif self.path == "/matrix":
                # Matrix branch
                seq_values = SEQUENCES.get(sequence or "S2", SEQUENCES["S2"])[:]
                base_idx, align_status = find_start_index(candles, start_tod)
                dc_flags_all = compute_dc_flags(candles)
                offsets = [-3, -2, -1, 0, 1, 2, 3]
                per_offset = {
                    o: compute_offset_alignment(candles, dc_flags_all, base_idx, seq_values, o, minutes_per_step=48)
                    for o in offsets
                }

                rows = []
                for vi, v in enumerate(seq_values):
                    cells = [f"<td>{v}</td>"]
                    for o in offsets:
                        align_o = per_offset[o]
                        hit = align_o.hits[vi] if vi < len(align_o.hits) else None
                        idx = hit.idx if hit else None
                        ts = hit.ts if hit else None
                        if idx is not None and ts is not None and 0 <= idx < len(candles):
                            ts_s = ts.strftime('%Y-%m-%d %H:%M:%S')
                            oc_label = format_pip(candles[idx].close - candles[idx].open)
                            prev_label = format_pip(candles[idx - 1].close - candles[idx - 1].open) if idx - 1 >= 0 else "-"
                            label = f"{ts_s} (OC {oc_label}, PrevOC {prev_label})"
                            if hit.synthetic:
                                label += " (syn)"
                            if hit.used_dc:
                                label += " (DC)"
                            cells.append(f"<td>{html.escape(label)}</td>")
                        else:
                            first = seq_values[0]
                            delta_steps = max(0, v - first)
                            use_target = align_o.missing_steps and v <= align_o.missing_steps
                            base_ts = align_o.target_ts if use_target else align_o.start_ref_ts
                            pred_ts = (base_ts + __import__('datetime').timedelta(minutes=48*delta_steps)).strftime('%Y-%m-%d %H:%M:%S')
                            cells.append(f"<td>{html.escape(pred_ts)} (pred, OC -, PrevOC -)</td>")
                    rows.append(f"<tr>{''.join(cells)}</tr>")

                header_cells = ''.join(f"<th>{'+'+str(o) if o>0 else str(o)}</th>" for o in offsets)
                thead = f"<tr><th>Seq</th>{header_cells}</tr>"
                table = f"<table><thead>{thead}</thead><tbody>{''.join(rows)}</tbody></table>"

                status_summary = ', '.join(
                    f"{('+' + str(o)) if o > 0 else str(o)}: {per_offset[o].offset_status}"
                    for o in offsets
                )

                info = (
                    f"<div class='card'>"
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>"
                    f"<div><strong>Zaman Dilimi:</strong> 48m</div>"
                    f"<div><strong>Range:</strong> {html.escape(candles[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>"
                    f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>"
                    f"<div><strong>Synthetic:</strong> inserted {added} candles (days after start)</div>"
                    f"<div><strong>Sequence:</strong> {html.escape(sequence or 'S2')}</div>"
                    f"<div><strong>Offset durumları:</strong> {html.escape(status_summary)}</div>"
                    f"</div>"
                )

                body = info + table
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(page("app48 - Matrix", body, active_tab="matrix"))
            else:
                self.send_error(400)
                return
        except Exception as e:
            msg = html.escape(str(e) or "Bilinmeyen hata")
            self.send_response(400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            _add_security_headers(self)
            self.end_headers()
            self.wfile.write(page("Hata", f"<p>Hata: {msg}</p><p><a href='/'>&larr; Geri</a></p>"))

    def log_message(self, format, *args):
        pass


def run(host: str, port: int):
    httpd = HTTPServer((host, port), AppHandler)
    print(f"app48 web: http://{host}:{port}/")
    httpd.serve_forever()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app48.web", description="app48 için basit web arayüzü")
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu adresi (vars: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2020, help="Port (vars: 2020)")
    args = parser.parse_args(argv)
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
