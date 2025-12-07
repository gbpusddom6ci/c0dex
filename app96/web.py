from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import html
import io
import csv
import base64
import json
from typing import List, Optional, Dict, Any, Type, Set, Tuple

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
    is_forbidden_iou_time,
)
from .main import (
    Candle as ConverterCandle,
    estimate_timeframe_minutes,
    adjust_to_output_tz,
    convert_12m_to_96m,
    format_price,
)
from email.parser import BytesParser
from email.policy import default as email_default
from datetime import timedelta, time as dtime
from zipfile import ZipFile, ZIP_DEFLATED

from news_loader import find_news_for_timestamp

IOU_TOLERANCE = 0.005

# --- Örüntüleme Yardımcıları ---

# Sınırlar kaldırıldı: None => limitsiz (beam ve çıktı sayısı)
PATTERN_MAX_PATHS: Optional[int] = None
PATTERN_BEAM_WIDTH: Optional[int] = None

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


def build_patterns_from_xyz_lists(
    xyz_sets: List[Set[int]],
    allow_zero_after_start: bool,
    max_paths: Optional[int] = PATTERN_MAX_PATHS,
    beam_width: Optional[int] = PATTERN_BEAM_WIDTH,
) -> List[List[int]]:
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
                if beam_width is not None and len(next_states) >= beam_width:
                    # Basit beam budaması
                    break
            if beam_width is not None and len(next_states) >= beam_width:
                break
        states = next_states
        if not states:
            break

    results: List[List[int]] = [st["seq"] for st in states if len(st.get("seq", [])) == len(xyz_sets)]
    if max_paths is not None:
        return results[:max_paths]
    return results


PATTERN_DOMAIN = {-3, -2, -1, 0, 1, 2, 3}


def _initial_pattern_state() -> Dict[str, Any]:
    return {
        "mode": "free",
        "sign": None,
        "dir": None,
        "pos": None,
        "allow_zero_next": False,
        "prev": None,
        "seq": [],
    }


def _apply_pattern_sequence(
    state: Dict[str, Any],
    pattern: List[int],
    start_len: int,
    allow_zero_after_start: bool,
) -> Optional[Dict[str, Any]]:
    st = state
    step_idx = start_len
    for value in pattern:
        allowed = _allowed_values_for_state(st, PATTERN_DOMAIN, allow_zero_after_start)
        if value not in allowed:
            return None
        st = _advance_state(st, value, step_idx, allow_zero_after_start)
        step_idx += 1
    return st


def _infer_pattern_group_width(pattern_group: List[List[int]]) -> int:
    for seq in pattern_group:
        if seq:
            return len(seq)
    return 0


def _find_mirror_chain_highlights(seq: List[int]) -> Set[int]:
    """app96 chained paneli için: en az 3 ardışık grup (0 ile ayrılan, aynı işaretli, 
    1-2-3 veya 3-2-1) + her ara 0'da ayna kuralı (sol/sağ komşu eşit) 
    sağlayan zinciri kırmızı/bold vurgulamak üzere indeksleri döndürür.
    Not: İlk ve son 0 vurgulanmaz; yalnız iç sınır 0'lar vurgulanır.
    """
    highlights: Set[int] = set()
    n = len(seq)
    if n == 0:
        return highlights
    zeros: List[int] = [i for i, v in enumerate(seq) if v == 0]
    if len(zeros) < 2:
        return highlights

    # Grupları çıkar: iki 0 arasında tam 3 token ve ±1/±2/±3 monotone + aynı işaret
    groups: List[Dict[str, Any]] = []
    for zi in range(len(zeros) - 1):
        a = zeros[zi]
        b = zeros[zi + 1]
        if b - a - 1 != 3:
            continue
        s0, s1, s2 = seq[a + 1], seq[a + 2], seq[b - 1]
        if 0 in (s0, s1, s2):
            continue
        sgn = 1 if s0 > 0 else -1
        if (s1 > 0) != (s0 > 0) or (s2 > 0) != (s0 > 0):
            continue
        abs_vals = [abs(s0), abs(s1), abs(s2)]
        if abs_vals == [1, 2, 3]:
            direction = "asc"
        elif abs_vals == [3, 2, 1]:
            direction = "desc"
        else:
            continue
        groups.append({
            "z_left": a,
            "z_right": b,
            "idxs": [a + 1, a + 2, b - 1],
            "sign": sgn,
            "dir": direction,
        })

    if not groups:
        return highlights

    # Ardışık zincir koşulu: aynı işaret ve ayna kuralı
    # ayna: 0 sınırında sol/sağ komşular eşit => seq[z-1] == seq[z+1]
    g = 0
    while g < len(groups):
        run_sign = groups[g]["sign"]
        r = g
        while r + 1 < len(groups):
            # ortak sınır 0: groups[r] ile groups[r+1] arasında zeros[r+1]
            z_boundary = zeros[r + 1]
            if not (0 <= z_boundary - 1 < n and 0 <= z_boundary + 1 < n):
                break
            if seq[z_boundary - 1] != seq[z_boundary + 1]:
                break
            if groups[r + 1]["sign"] != run_sign:
                break
            r += 1

        run_len = r - g + 1
        if run_len >= 3:
            # Bu aralıktaki tüm üçlü indekslerini vurgula
            for k in range(g, r + 1):
                highlights.update(groups[k]["idxs"])
            # İç sınır 0'ları da vurgula (ilk ve son 0 hariç)
            for k in range(g + 1, r + 1):
                z_boundary = zeros[k]
                if 0 <= z_boundary < n:
                    highlights.add(z_boundary)
        g = r + 1

    return highlights


def build_chained_pattern_sequences(
    pattern_groups: List[List[List[int]]],
    allow_zero_after_start: bool,
    max_paths: Optional[int] = PATTERN_MAX_PATHS,
    beam_width: Optional[int] = PATTERN_BEAM_WIDTH,
) -> Tuple[List[List[int]], int]:
    if not pattern_groups:
        return [], 0
    states: List[Dict[str, Any]] = [_initial_pattern_state()]
    for group in pattern_groups:
        next_states: List[Dict[str, Any]] = []
        for st in states:
            current_len = len(st.get("seq") or [])
            for pattern in group:
                new_state = _apply_pattern_sequence(st, pattern, current_len, allow_zero_after_start)
                if new_state is None:
                    continue
                next_states.append(new_state)
                if beam_width is not None and len(next_states) >= beam_width:
                    break
            if beam_width is not None and len(next_states) >= beam_width:
                break
        states = next_states
        if not states:
            break
    seen: Set[Tuple[int, ...]] = set()
    display: List[List[int]] = []
    total_unique = 0
    for st in states:
        seq = st.get("seq") or []
        key = tuple(seq)
        if key in seen:
            continue
        seen.add(key)
        total_unique += 1
        if max_paths is None or len(display) < max_paths:
            display.append(seq)
    return display, total_unique


def render_combined_pattern_panel(
    pattern_groups: List[List[List[int]]],
    meta_groups: List[Dict[str, Any]],
    allow_zero_after_start: bool,
) -> str:
    group_count = len(pattern_groups)
    if group_count < 2:
        return ""
    combined, total_unique = build_chained_pattern_sequences(
        pattern_groups,
        allow_zero_after_start=allow_zero_after_start,
    )
    summary_label = f"Toplu örüntüler (grup sayısı {group_count})"
    if total_unique == 0:
        inner = "<div style='margin-top:12px;'>Uygun birleşik örüntü bulunamadı.</div>"
    else:
        limit_note = ""
        if total_unique > len(combined):
            limit_note = f" (ilk {len(combined)})"
        info_line = f"<div><strong>Toplam birleşik örüntü:</strong> {total_unique}{limit_note}</div>"
        flat_names: List[str] = []
        flat_joker_indices: Set[int] = set()
        flat_meta_entries: List[Dict[str, Any]] = []
        cursor = 0
        group_widths = [_infer_pattern_group_width(group) for group in pattern_groups]
        for idx in range(group_count):
            width = group_widths[idx] if idx < len(group_widths) else 0
            meta = meta_groups[idx] if idx < len(meta_groups) else {}
            raw_names = meta.get("file_names") if isinstance(meta, dict) else None
            names = [str(n) for n in raw_names] if isinstance(raw_names, list) else []
            length_for_cursor = width or len(names)
            if width:
                if len(names) < width:
                    names = names + [""] * (width - len(names))
                elif len(names) > width:
                    names = names[:width]
            elif length_for_cursor and not names:
                names = [""] * length_for_cursor
            flat_names.extend(names)
            totals_list = meta.get("offset_totals") if isinstance(meta, dict) else None
            details_list = meta.get("offset_details") if isinstance(meta, dict) else None
            for pos in range(length_for_cursor):
                total_map: Dict[Any, Any] = {}
                detail_map: Dict[Any, Any] = {}
                if isinstance(totals_list, list) and pos < len(totals_list) and isinstance(totals_list[pos], dict):
                    total_map = totals_list[pos]
                if isinstance(details_list, list) and pos < len(details_list) and isinstance(details_list[pos], dict):
                    detail_map = details_list[pos]
                flat_meta_entries.append({
                    "totals": total_map,
                    "details": detail_map,
                })
            raw_jokers = meta.get("joker_indices") if isinstance(meta, dict) else None
            if isinstance(raw_jokers, list):
                for j in raw_jokers:
                    try:
                        j_int = int(j)
                    except Exception:
                        continue
                    flat_joker_indices.add(cursor + j_int)
            cursor += length_for_cursor
        grouped: Dict[int, List[List[int]]] = {}
        group_order: List[int] = []
        for seq in combined:
            if not seq:
                continue
            start_val = seq[0]
            if start_val not in grouped:
                grouped[start_val] = []
                group_order.append(start_val)
            grouped[start_val].append(seq)

        def _render_group(patterns: List[List[int]]) -> str:
            def _fmt_num(val: float) -> str:
                try:
                    return f"{float(val):+.5f}"
                except Exception:
                    return "-"

            def _offset_payload(pos: int, offset: int) -> Tuple[float, List[Dict[str, Any]]]:
                if pos < 0 or pos >= len(flat_meta_entries):
                    return 0.0, []
                meta_entry = flat_meta_entries[pos] if pos < len(flat_meta_entries) else {}
                totals_map = meta_entry.get("totals") if isinstance(meta_entry, dict) else {}
                details_map = meta_entry.get("details") if isinstance(meta_entry, dict) else {}
                val = 0.0
                if isinstance(totals_map, dict):
                    if offset in totals_map:
                        try:
                            val = float(totals_map[offset])
                        except Exception:
                            val = 0.0
                    elif str(offset) in totals_map:
                        try:
                            val = float(totals_map[str(offset)])
                        except Exception:
                            val = 0.0
                detail_list: List[Dict[str, Any]] = []
                if isinstance(details_map, dict):
                    payload = details_map.get(offset)
                    if payload is None:
                        payload = details_map.get(str(offset))
                    if isinstance(payload, list):
                        detail_list = payload
                return val, detail_list

            # Chained panel: özel vurgu (3+ ardışık "ayna" üçlü grupları) uygulansın
            pattern_highlights: List[Set[int]] = [
                _find_mirror_chain_highlights(seq) for seq in patterns
            ]
            pattern_html = render_pattern_panel(
                [],
                allow_zero_after_start=allow_zero_after_start,
                file_names=flat_names if flat_names else None,
                joker_indices=flat_joker_indices if flat_joker_indices else None,
                sequence_name=None,
                precomputed_patterns=patterns,
                highlight_positions=pattern_highlights,
            )
            totals_blocks: List[str] = []
            for idx_pat, seq in enumerate(patterns):
                per_file_lines: List[str] = []
                net_total = 0.0
                for pos, offset in enumerate(seq):
                    val, detail_entries = _offset_payload(pos, offset)
                    net_total += val
                    file_label = flat_names[pos] if pos < len(flat_names) and flat_names[pos] else f"Dosya {pos + 1}"
                    detail_lines: List[str] = []
                    for rec in detail_entries:
                        if isinstance(rec, dict) and "omitted" in rec:
                            omitted_count = int(rec.get("omitted", 0))
                            detail_lines.append(f"... (+{omitted_count} kayıt daha)")
                            continue
                        seq_v = rec.get("seq")
                        ts_val = rec.get("ts") or "-"
                        oc_val = rec.get("oc")
                        prev_val = rec.get("prev_oc")
                        contrib_val = rec.get("contribution")
                        detail_lines.append(
                            f"seq {seq_v} · {html.escape(str(ts_val))} · OC {_fmt_num(oc_val)} · PrevOC {_fmt_num(prev_val)} · katkı {_fmt_num(contrib_val)}"
                        )
                    if not detail_lines:
                        detail_lines.append("Kayıt yok")
                    per_file_lines.append(
                        f"<div><strong>{html.escape(file_label)}</strong> — offset {html.escape(_fmt_off(offset))}: {_fmt_num(val)}"
                        f"<br>{'<br>'.join(detail_lines)}</div>"
                    )
                summary = f"Net Total: {_fmt_num(net_total)} (örüntü {idx_pat + 1})"
                totals_blocks.append(
                    "<details style='margin-top:8px;'>"
                    f"<summary>{html.escape(summary)}</summary>"
                    "<div style='margin-top:6px; display:flex; flex-direction:column; gap:6px;'>"
                    + "".join(per_file_lines) +
                    "</div>"
                    "</details>"
                )
            totals_html = ""
            if totals_blocks:
                totals_html = (
                    "<div class='card' style='margin-top:12px;'>"
                    "<h4 style='margin:0 0 8px;'>Total Sum</h4>"
                    + "".join(totals_blocks) +
                    "</div>"
                )
            return pattern_html + totals_html

        grouped_lines: List[str] = []
        for start_val in group_order:
            patterns = grouped[start_val]
            panel_html = _render_group(patterns)
            summary = f"{_fmt_off(start_val)} ile başlayanlar ({len(patterns)})"
            grouped_lines.append(
                "<details>"
                f"<summary>{html.escape(summary)}</summary>"
                f"{panel_html}"
                "</details>"
            )
        inner = "<div style='margin-top:12px;'>" + info_line + "".join(grouped_lines) + "</div>"
    return (
        f"<details class='card' style='margin-top:16px;'>"
        f"<summary>{html.escape(summary_label)}</summary>"
        f"{inner}"
        "</details>"
    )


def render_pattern_panel(
    xyz_sets: List[Set[int]],
    allow_zero_after_start: bool,
    file_names: Optional[List[str]] = None,
    joker_indices: Optional[Set[int]] = None,
    sequence_name: Optional[str] = None,
    precomputed_patterns: Optional[List[List[int]]] = None,
    highlight_positions: Optional[List[Set[int]]] = None,
) -> str:
    patterns = (
        precomputed_patterns
        if precomputed_patterns is not None
        else build_patterns_from_xyz_lists(xyz_sets, allow_zero_after_start=allow_zero_after_start)
    )
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

    domain = PATTERN_DOMAIN
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
        highlight_set: Optional[Set[int]] = None
        if highlight_positions and idx_line < len(highlight_positions):
            highlight_set = highlight_positions[idx_line]
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
                    style = ""
                    if highlight_set is not None and idx in highlight_set:
                        style = " style='font-weight:700; font-style:italic;'"
                    if tp:
                        return (
                            f"<span class='pat-token' title='{html.escape(tp)}' data-tip='{html.escape(tp)}'{style}>{tk}</span>"
                        )
                    return f"<span class='pat-token'{style}>{tk}</span>"
            else:
                def token_html(idx:int) -> str:
                    tk = html.escape(_fmt_off(seq[idx]))
                    style = ""
                    if highlight_set is not None and idx in highlight_set:
                        style = " style='font-weight:700; font-style:italic;'"
                    return f"<span class='pat-token'{style}>{tk}</span>"

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
        number_html = f"<span style='display:inline-block; min-width:1.8em; font-weight:bold;'>{idx_line + 1}.</span>"
        lines.append(f"<div class='pat-line'>{number_html} {label} (devam: {html.escape(cont)})</div>")
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
    info = f"<div><strong>Toplam örüntü:</strong> {len(patterns)}</div>"
    seq_info = f"<div><strong>Sequence:</strong> {html.escape(sequence_name)}</div>" if sequence_name else ""
    return "<div class='card'><h3>Örüntüleme</h3>" + info + seq_info + last_line + "".join(lines) + "</div>"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB
MAX_FILES = 50
TOTAL_SUM_DETAIL_LIMIT = 200


def calculate_total_sums_for_candles(
    candles: List[CounterCandle],
    sequence: str,
    limit: float,
    allowed_offsets: Optional[Set[int]] = None,
    detail_limit: int = TOTAL_SUM_DETAIL_LIMIT,
    dc_flags: Optional[List[Optional[bool]]] = None,
    base_idx: Optional[int] = None,
) -> Tuple[Dict[int, float], Dict[int, List[Dict[str, Any]]]]:
    seq_key = (sequence or "S2").upper()
    if seq_key not in SEQUENCES:
        seq_key = "S2"
    seq_values = SEQUENCES[seq_key][:]
    skip_values = {1, 3} if seq_key == "S1" else {1, 5}
    dc_flags = dc_flags if dc_flags is not None else compute_dc_flags(candles)
    if base_idx is None:
        base_idx, _ = find_start_index(candles, DEFAULT_START_TOD)
    totals: Dict[int, float] = {}
    details: Dict[int, List[Dict[str, Any]]] = {}
    detail_limit = max(0, int(detail_limit)) if detail_limit is not None else 0
    for offset in range(-3, 4):
        if allowed_offsets is not None and offset not in allowed_offsets:
            continue
        alignment = compute_offset_alignment(candles, dc_flags, base_idx, seq_values, offset)
        for seq_val, alloc in zip(seq_values, alignment.hits):
            if seq_val in skip_values:
                continue
            idx = alloc.idx
            if idx is None or idx <= 0 or idx >= len(candles):
                continue
            ts = candles[idx].ts
            if is_forbidden_iou_time(ts):
                continue
            oc = candles[idx].close - candles[idx].open
            prev_oc = candles[idx - 1].close - candles[idx - 1].open
            if abs(prev_oc) <= limit:
                continue
            same_sign = (oc * prev_oc) > 0
            contribution = -abs(oc) if same_sign else abs(oc)
            totals[offset] = totals.get(offset, 0.0) + contribution
            if detail_limit == 0:
                continue
            bucket = details.setdefault(offset, [])
            if len(bucket) < detail_limit:
                bucket.append({
                    "seq": seq_val,
                    "ts": ts.strftime('%Y-%m-%d %H:%M:%S'),
                    "oc": oc,
                    "prev_oc": prev_oc,
                    "contribution": contribution,
                })
            elif len(bucket) == detail_limit:
                # Tek seferlik “kırpıldı” uyarısı
                bucket.append({"omitted": 1})
            else:
                last = bucket[-1]
                if isinstance(last, dict) and "omitted" in last:
                    last["omitted"] = int(last.get("omitted", 1)) + 1
                else:
                    bucket.append({"omitted": 1})
    return totals, details

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
      <h2>app96</h2>
    </header>
    <nav class='tabs'>
      <a href='/' class='{ 'active' if active_tab=="analyze" else '' }'>Analiz</a>
      <a href='/dc' class='{ 'active' if active_tab=="dc" else '' }'>DC List</a>
      <a href='/matrix' class='{ 'active' if active_tab=="matrix" else '' }'>Matrix</a>
      <a href='/converter' class='{ 'active' if active_tab=="converter" else '' }'>12→96 Converter</a>
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
            <div>96m</div>
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
    <p><strong>Not:</strong> 18:00, (Pazar hariç) 19:36 ve Cuma 16:24 mumları DC sayılmaz; aynı slotlar IOU taramasında da dışlanır.</p>
    """
    return page("app96", body, active_tab="analyze")


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
            <div>96m</div>
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
    <p><strong>Not:</strong> Liste, app96 için hesaplanan tüm DC mumlarını içerir. 18:00, (Pazar hariç) 19:36 ve Cuma 16:24 mumları DC dışındadır.</p>
    """
    return page("app96 - DC List", body, active_tab="dc")


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
            <div>96m</div>
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
    return page("app96 - Matrix", body, active_tab="matrix")


def render_converter_index() -> bytes:
    body = """
    <div class='card'>
      <form method='post' action='/converter' enctype='multipart/form-data'>
        <label>CSV (12m, UTC-5)</label>
        <input type='file' name='csv' accept='.csv,text/csv' required multiple />
        <div style='margin-top:12px;'>
          <button type='submit'>96m'e Dönüştür</button>
        </div>
      </form>
    </div>
    <p>Girdi UTC-5 12 dakikalık mumlar olmalıdır. Çıktı UTC-4 96 dakikalık mumlar olarak indirilir (8 × 12m = 1 × 96m).</p>
    <p>Birden fazla CSV seçersen her biri 96m'e dönüştürülür; birden fazla dosya seçildiğinde sonuçlar ZIP paketi olarak indirilir.</p>
    """
    return page("app96 - Converter", body, active_tab="converter")


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
            <div>96m</div>
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
    <p>IOU taraması, limit üzerindeki OC/PrevOC değerlerinin aynı işaretli olduğu mumları dosya bazında listeler. Çoklu CSV seçimini destekler.</p>
    <p><strong>Not:</strong> 18:00, (Pazar hariç) 19:36 ve Cuma 16:24 mumları IOU listesine dahil edilmez.</p>
    """


def render_iou_index() -> bytes:
    body = render_iou_form()
    return page("app96 - IOU", body, active_tab="iou")


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


class App96Handler(BaseHTTPRequestHandler):
    server_version = "Candles96/1.0"
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
            file_obj = form.get("csv") or {}
            file_entries = file_obj.get("files")
            if not file_entries:
                data_single = file_obj.get("data")
                if data_single is not None:
                    file_entries = [{"filename": file_obj.get("filename"), "data": data_single}]
                else:
                    file_entries = []
            files_list = [entry for entry in file_entries if entry.get("data") is not None]
            if not files_list and self.path != "/iou":
                raise ValueError("CSV dosyası bulunamadı")
            if len(files_list) > MAX_FILES:
                self.send_response(413)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(b"Too many files (max 50).")
                return

            if self.path == "/converter":
                outputs: List[Tuple[str, bytes]] = []
                used_names: set[str] = set()

                for entry in files_list:
                    entry_data = entry.get("data")
                    if isinstance(entry_data, (bytes, bytearray)):
                        text_entry = entry_data.decode("utf-8", errors="replace")
                    else:
                        text_entry = str(entry_data)
                    try:
                        candles_entry = load_candles_from_text(text_entry, ConverterCandle)
                    except ValueError as exc:
                        name = entry.get("filename") or "dosya"
                        raise ValueError(f"{name}: {exc}")
                    if not candles_entry:
                        name = entry.get("filename") or "dosya"
                        raise ValueError(f"{name}: Veri boş veya çözümlenemedi")
                    tf_est = estimate_timeframe_minutes(candles_entry)
                    if tf_est is None or abs(tf_est - 12) > 1.0:
                        name = entry.get("filename") or "dosya"
                        raise ValueError(f"{name}: Girdi 12 dakikalık akış gibi görünmüyor")
                    shifted, _ = adjust_to_output_tz(candles_entry, "UTC-5")
                    converted = convert_12m_to_96m(shifted)

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
                    download_name = _sanitize_csv_filename(entry.get("filename") or "converted", "_96m.csv")
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
                bundle_name = "converted_96m_bundle.zip"

                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{bundle_name}"')
                self.send_header("Content-Length", str(len(zip_bytes)))
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(zip_bytes)
                return

            if self.path == "/iou":
                sequence = (form.get("sequence", {}).get("value") or "S1").strip() or "S1"
                tz_value = (form.get("input_tz", {}).get("value") or "UTC-4").strip()
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
                pattern_payload_raw = form.get("previous_pattern_payload", {}).get("value", "")
                pattern_groups_history: List[List[List[int]]] = []
                pattern_meta_history: List[Dict[str, Any]] = []
                pattern_allow_zero_after_start = True
                if pattern_payload_raw:
                    try:
                        decoded_payload = base64.b64decode(pattern_payload_raw.encode("ascii"))
                        payload_obj = json.loads(decoded_payload.decode("utf-8"))
                    except Exception:
                        payload_obj = None
                    if isinstance(payload_obj, dict):
                        pattern_allow_zero_after_start = bool(payload_obj.get("allow_zero_after_start", True))
                        groups_data = payload_obj.get("groups", [])
                        meta_data = payload_obj.get("meta", [])
                    elif isinstance(payload_obj, list):
                        groups_data = payload_obj
                        meta_data = []
                    else:
                        groups_data = []
                        meta_data = []
                    if isinstance(groups_data, list):
                        for group in groups_data:
                            if not isinstance(group, list):
                                continue
                            normalized: List[List[int]] = []
                            for seq in group:
                                if not isinstance(seq, list):
                                    continue
                                try:
                                    normalized.append([int(v) for v in seq])
                                except Exception:
                                    continue
                            pattern_groups_history.append(normalized)
                    if isinstance(meta_data, list):
                        for meta in meta_data:
                            if not isinstance(meta, dict):
                                pattern_meta_history.append({})
                                continue
                            names = meta.get("file_names")
                            if isinstance(names, list):
                                names_out = [str(n) for n in names]
                            else:
                                names_out = []
                            jokers = meta.get("joker_indices")
                            if isinstance(jokers, list):
                                joker_out: List[int] = []
                                for j in jokers:
                                    try:
                                        joker_out.append(int(j))
                                    except Exception:
                                        continue
                            else:
                                joker_out = []
                            totals_raw = meta.get("offset_totals")
                            totals_out: List[Dict[int, float]] = []
                            if isinstance(totals_raw, list):
                                for item in totals_raw:
                                    totals_out.append(item if isinstance(item, dict) else {})
                            details_raw = meta.get("offset_details")
                            details_out: List[Dict[int, List[Dict[str, Any]]]] = []
                            if isinstance(details_raw, list):
                                for item in details_raw:
                                    details_out.append(item if isinstance(item, dict) else {})
                            pattern_meta_history.append({
                                "file_names": names_out,
                                "joker_indices": joker_out,
                                "offset_totals": totals_out,
                                "offset_details": details_out,
                            })
                    if len(pattern_meta_history) < len(pattern_groups_history):
                        pattern_meta_history.extend({} for _ in range(len(pattern_groups_history) - len(pattern_meta_history)))
                    elif len(pattern_meta_history) > len(pattern_groups_history):
                        pattern_meta_history = pattern_meta_history[:len(pattern_groups_history)]
                
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

                # Base64 encoded dosyaları decode et (joker seçimi sonrası)
                b64_entries: List[Dict[str, Any]] = []
                i = 0
                while True:
                    b64_key = f"csv_b64_{i}"
                    name_key = f"csv_name_{i}"
                    if b64_key not in form or name_key not in form:
                        break
                    b64_data = form.get(b64_key, {}).get("value", "")
                    filename = form.get(name_key, {}).get("value", f"file_{i}.csv")
                    try:
                        decoded = base64.b64decode(b64_data.encode("ascii") if isinstance(b64_data, str) else b64_data)
                        b64_entries.append({"filename": filename, "data": decoded})
                    except Exception:
                        pass
                    i += 1

                # Joker seçimi adımı: confirm_iou yoksa (tek dosya olsa bile)
                if not confirm_iou and files_list:
                    hidden_fields: List[str] = []
                    file_rows: List[str] = []
                    idx = 0
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
                    preserved.append(
                        f"<input type='hidden' name='previous_pattern_payload' value='{html.escape(pattern_payload_raw)}'>"
                    )

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
                    self.wfile.write(page("app96 IOU - Joker Seçimi", body, active_tab="iou"))
                    return

                effective_entries = b64_entries if b64_entries else files_list
                if not effective_entries:
                    raise ValueError("CSV dosyası bulunamadı")
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
                file_offset_totals: List[Dict[int, float]] = []
                file_offset_details: List[Dict[int, List[Dict[str, Any]]]] = []
                for idx_entry, entry in enumerate(effective_entries):
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

                    dc_flags_entry = compute_dc_flags(candles_entry)
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

                    # PrevOC-limit tabanlı total sum hesapla (sadece limit, tolerans yok)
                    offset_totals, offset_details = calculate_total_sums_for_candles(
                        candles_entry,
                        sequence,
                        limit_val,
                    )
                    file_offset_totals.append(offset_totals)
                    file_offset_details.append(offset_details)

                    base_offsets = [-3, -2, -1, 0, 1, 2, 3]
                    xyz_offsets = [o for o in base_offsets if not offset_has_non_news.get(o, False)] if xyz_enabled else base_offsets
                    # Joker: XYZ tam kapsam
                    if idx_entry in joker_indices:
                        xyz_offsets = base_offsets[:]
                    xyz_text = ", ".join((f"+{o}" if o > 0 else str(o)) for o in xyz_offsets) if xyz_offsets else "-"
                    all_xyz_sets.append(set(xyz_offsets))
                    all_file_names.append(name)
                    xyz_line = ""
                    if xyz_enabled and not summary_mode:
                        joker_tag = " (Joker)" if idx_entry in joker_indices else ""
                        xyz_line = f"<div><strong>XYZ Kümesi{joker_tag}:</strong> {html.escape(xyz_text)}</div>"

                    # PrevOC-limit tabanlı total sum hesapla (sadece limit, tolerans yok)
                    allowed_offs_for_totals: Optional[Set[int]] = set(xyz_offsets) if xyz_offsets else None
                    offset_totals, offset_details = calculate_total_sums_for_candles(
                        candles_entry,
                        sequence,
                        limit_val,
                        allowed_offsets=allowed_offs_for_totals,
                        detail_limit=TOTAL_SUM_DETAIL_LIMIT,
                        dc_flags=dc_flags_entry,
                        base_idx=report.base_idx if isinstance(report.base_idx, int) else None,
                    )
                    file_offset_totals.append(offset_totals)
                    file_offset_details.append(offset_details)

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

                pattern_panel_html = ""
                combined_panel_html = ""
                if pattern_enabled:
                    current_patterns = build_patterns_from_xyz_lists(
                        all_xyz_sets,
                        allow_zero_after_start=pattern_allow_zero_after_start,
                    )
                    current_meta = {
                        "file_names": all_file_names[:],
                        "joker_indices": sorted(joker_indices) if joker_indices else [],
                        "offset_totals": file_offset_totals[:],
                        "offset_details": file_offset_details[:],
                    }
                    pattern_panel_html = render_pattern_panel(
                        all_xyz_sets,
                        allow_zero_after_start=pattern_allow_zero_after_start,
                        file_names=all_file_names,
                        joker_indices=joker_indices,
                        sequence_name=sequence,
                        precomputed_patterns=current_patterns,
                    )
                    updated_history = pattern_groups_history[:] if pattern_groups_history else []
                    updated_history.append(current_patterns)
                    updated_meta_history = pattern_meta_history[:] if pattern_meta_history else []
                    updated_meta_history.append(current_meta)
                    combined_panel_html = render_combined_pattern_panel(
                        updated_history,
                        updated_meta_history,
                        allow_zero_after_start=pattern_allow_zero_after_start,
                    )
                    pattern_groups_history = updated_history
                    pattern_meta_history = updated_meta_history
                if len(pattern_meta_history) < len(pattern_groups_history):
                    pattern_meta_history.extend({} for _ in range(len(pattern_groups_history) - len(pattern_meta_history)))
                elif len(pattern_meta_history) > len(pattern_groups_history):
                    pattern_meta_history = pattern_meta_history[:len(pattern_groups_history)]

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
                    current_result = "<div class='card'>" + table + "</div>"
                    if pattern_panel_html:
                        current_result += pattern_panel_html
                    if combined_panel_html:
                        current_result += combined_panel_html
                else:
                    # Non-summary: tüm dosya kartları + varsa örüntü paneli
                    if pattern_panel_html:
                        sections.append(pattern_panel_html)
                    if combined_panel_html:
                        sections.append(combined_panel_html)
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
                pattern_payload_encoded = base64.b64encode(
                    json.dumps(
                        {
                            "groups": pattern_groups_history,
                            "allow_zero_after_start": pattern_allow_zero_after_start,
                            "meta": pattern_meta_history,
                        },
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).decode("ascii")
                
                form_html = render_iou_form()
                # Form içindeki form tag'ini kaldırıp sadece içeriği al
                form_content = form_html.replace("<form method='post' action='/iou' enctype='multipart/form-data'>", "").replace("</form>", "").strip()
                
                form_section = (
                    "<hr style='margin:32px 0; border:none; border-top:2px solid #ddd;'>"
                    "<h2 style='margin-top:24px;'>Yeni Analiz</h2>"
                    "<div class='card'>"
                    "<form method='post' action='/iou' enctype='multipart/form-data'>"
                    f"<input type='hidden' name='previous_results_html' value='{body_encoded}'>"
                    f"<input type='hidden' name='previous_pattern_payload' value='{html.escape(pattern_payload_encoded)}'>"
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
                self.wfile.write(page("app96 IOU", body, active_tab="iou"))
                return

            primary_entry = files_list[0]
            raw = primary_entry.get("data")
            text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)

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
                    f"<div><strong>Zaman Dilimi:</strong> 96m</div>",
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
                self.wfile.write(page("app96 sonuçlar", body, active_tab="analyze"))
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
                    f"<div><strong>Zaman Dilimi:</strong> 96m</div>"
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
                self.wfile.write(page("app96 DC List", body, active_tab="dc"))
                return

            if self.path == "/matrix":
                seq_values = SEQUENCES.get(sequence, SEQUENCES["S2"])[:]
                base_idx, align_status = find_start_index(candles, DEFAULT_START_TOD)
                offsets = [-3, -2, -1, 0, 1, 2, 3]
                per_offset = {o: compute_offset_alignment(candles, dc_flags, base_idx, seq_values, o) for o in offsets}

                header_cells = ''.join(f"<th>{'+'+str(o) if o>0 else str(o)}</th>" for o in offsets)
                rows = []
                # Her offset için ilk non-use_target tahmin hücresini vurgulamak üzere takip
                first_pred_marked: Dict[int, bool] = {o: False for o in offsets}
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
                            # İlk non-use_target tahmin hücresini yarı opak yeşil ile vurgula
                            highlight = False
                            if not use_target and not first_pred_marked.get(o, False):
                                highlight = True
                                first_pred_marked[o] = True
                            style_attr = " style=\"background: rgba(46, 204, 113, 0.25)\"" if highlight else ""
                            cells.append(f"<td{style_attr}>{html.escape(ts_pred.strftime('%Y-%m-%d %H:%M:%S'))} (pred, OC -, PrevOC -)</td>")
                    rows.append(f"<tr>{''.join(cells)}</tr>")

                status_summary = ', '.join(
                    f"{('+' + str(o)) if o > 0 else str(o)}: {per_offset[o].offset_status}"
                    for o in offsets
                )

                table = f"<table><thead><tr><th>Seq</th>{header_cells}</tr></thead><tbody>{''.join(rows)}</tbody></table>"
                info = (
                    f"<div class='card'>"
                    f"<div><strong>Data:</strong> {len(candles)} candles</div>"
                    f"<div><strong>Zaman Dilimi:</strong> 96m</div>"
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
                self.wfile.write(page("app96 Matrix", body, active_tab="matrix"))
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
    server = HTTPServer((host, port), App96Handler)
    print(f"app96 web: http://{host}:{port}/")
    server.serve_forever()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app96.web", description="app96 için birleşik web arayüzü")
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu adresi (vars: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2196, help="Port (vars: 2196)")
    args = parser.parse_args(argv)
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
