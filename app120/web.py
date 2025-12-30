from http.server import HTTPServer, BaseHTTPRequestHandler
import argparse
import os
import secrets
import shutil
import time
import html
import io
import csv
import base64
import json
from typing import List, Optional, Dict, Any, Type, Set, Tuple
from urllib.parse import urlsplit, parse_qs

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
    detect_iov_candles,
    detect_iou_candles,
    compute_prevoc_sum_report,
)
from .main import (
    Candle as ConverterCandle,
    estimate_timeframe_minutes,
    adjust_to_output_tz,
    convert_60m_to_120m,
    format_price,
)
from email.parser import BytesParser
from email.policy import default as email_default
from datetime import timedelta
from zipfile import ZipFile, ZIP_DEFLATED
from typing import Tuple

from news_loader import find_news_for_timestamp

IOU_TOLERANCE = 0.0

# --- Örüntüleme Yardımcıları ---

# Sınırlar kaldırıldı: None => limitsiz (beam ve çıktı sayısı)
PATTERN_MAX_PATHS: Optional[int] = None
PATTERN_BEAM_WIDTH: Optional[int] = None

APP120_STATE_DIR = os.environ.get("APP120_STATE_DIR", "/tmp/app120_state")
try:
    APP120_STATE_TTL_SECONDS = int(os.environ.get("APP120_STATE_TTL_SECONDS", str(6 * 3600)) or str(6 * 3600))
except Exception:
    APP120_STATE_TTL_SECONDS = 6 * 3600


def _safe_state_token(token: str) -> str:
    tok = (token or "").strip()
    if not tok or len(tok) > 128:
        return ""
    for ch in tok:
        if not (ch.isalnum() or ch in ("-", "_")):
            return ""
    return tok


def _ensure_state_dir() -> str:
    root = APP120_STATE_DIR
    try:
        os.makedirs(root, exist_ok=True)
    except Exception:
        pass
    return root


def _cleanup_state_dir(now: Optional[float] = None) -> None:
    root = _ensure_state_dir()
    ttl = APP120_STATE_TTL_SECONDS
    if ttl <= 0:
        return
    t = time.time() if now is None else now
    try:
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            state_path = os.path.join(path, "state.json")
            try:
                mtime = os.path.getmtime(state_path) if os.path.exists(state_path) else os.path.getmtime(path)
            except Exception:
                continue
            if t - mtime > ttl:
                shutil.rmtree(path, ignore_errors=True)
    except Exception:
        return


def _new_state_token() -> str:
    return secrets.token_hex(16)


def _state_paths(token: str) -> Tuple[str, str, str]:
    root = _ensure_state_dir()
    base = os.path.join(root, token)
    uploads_dir = os.path.join(base, "uploads")
    state_path = os.path.join(base, "state.json")
    return base, uploads_dir, state_path


def _read_state(token: str) -> Optional[Dict[str, Any]]:
    tok = _safe_state_token(token)
    if not tok:
        return None
    _cleanup_state_dir()
    _, _, state_path = _state_paths(tok)
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _write_state(token: str, state: Dict[str, Any]) -> None:
    tok = _safe_state_token(token)
    if not tok:
        return
    _cleanup_state_dir()
    base, _, state_path = _state_paths(tok)
    try:
        os.makedirs(base, exist_ok=True)
        tmp_path = state_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp_path, state_path)
    except Exception:
        return


def _store_state_uploads(token: str, files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tok = _safe_state_token(token)
    if not tok:
        return []
    base, uploads_dir, _ = _state_paths(tok)
    try:
        os.makedirs(uploads_dir, exist_ok=True)
    except Exception:
        return []
    try:
        for old_name in os.listdir(uploads_dir):
            old_path = os.path.join(uploads_dir, old_name)
            if os.path.isfile(old_path):
                os.remove(old_path)
    except Exception:
        pass

    uploads: List[Dict[str, Any]] = []
    for idx, entry in enumerate(files):
        filename = entry.get("filename") or f"uploaded_{idx}.csv"
        raw = entry.get("data")
        if isinstance(raw, str):
            raw_bytes = raw.encode("utf-8", errors="replace")
        elif isinstance(raw, (bytes, bytearray)):
            raw_bytes = bytes(raw)
        else:
            raw_bytes = b""
        out_path = os.path.join(uploads_dir, f"file_{idx}.csv")
        try:
            with open(out_path, "wb") as f:
                f.write(raw_bytes)
        except Exception:
            continue
        uploads.append({"filename": str(filename), "path": out_path})

    try:
        os.makedirs(base, exist_ok=True)
    except Exception:
        pass
    return uploads


def _load_state_upload_entries(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    uploads = state.get("uploads")
    if not isinstance(uploads, list):
        return []
    out: List[Dict[str, Any]] = []
    for entry in uploads:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        name = entry.get("filename") or "uploaded.csv"
        if not isinstance(path, str) or not path:
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
        except Exception:
            continue
        out.append({"filename": str(name), "data": data})
    return out


def _fmt_off(v: int) -> str:
    return f"+{v}" if v > 0 else str(v)


def _sign(v: int) -> int:
    if v > 0:
        return 1
    if v < 0:
        return -1
    return 0


def _allowed_values_for_mirror_state(state: Dict[str, Any], choices: Set[int]) -> List[int]:
    prev = state.get("prev")
    seq = state.get("seq") or []
    phases = state.get("phases")

    # "Ayna örüntü" = 0, (±1,±2,±3,±2,±1), 0, ... ; her 0'dan sonra işaret serbest.
    # Bunu deterministik döngü yerine küçük bir NFA ile temsil ediyoruz.
    # phase = bu grafikteki olası düğümler kümesi.
    nodes = [
        {"v": 0, "next": [1, 6]},  # 0 -> +1(up) | -1(up)
        {"v": 1, "next": [2]},     # +1 up -> +2 up
        {"v": 2, "next": [3]},     # +2 up -> +3
        {"v": 3, "next": [4]},     # +3 -> +2 down
        {"v": 2, "next": [5]},     # +2 down -> +1 down
        {"v": 1, "next": [0]},     # +1 down -> 0
        {"v": -1, "next": [7]},    # -1 up -> -2 up
        {"v": -2, "next": [8]},    # -2 up -> -3
        {"v": -3, "next": [9]},    # -3 -> -2 down
        {"v": -2, "next": [10]},   # -2 down -> -1 down
        {"v": -1, "next": [0]},    # -1 down -> 0
    ]
    value_to_nodes: Dict[int, List[int]] = {}
    for i, node in enumerate(nodes):
        value_to_nodes.setdefault(int(node["v"]), []).append(i)

    allowed: Set[int] = set()
    if not seq:
        allowed = set(choices)
    else:
        if not isinstance(phases, list):
            phases = []
        if not phases and prev is not None:
            phases = value_to_nodes.get(int(prev), [])
        for p in phases:
            try:
                p_int = int(p)
            except Exception:
                continue
            if not (0 <= p_int < len(nodes)):
                continue
            for n_idx in nodes[p_int]["next"]:
                nxt_val = int(nodes[int(n_idx)]["v"])
                if nxt_val in choices:
                    allowed.add(nxt_val)

    if prev is not None and prev in allowed:
        allowed.discard(prev)

    order = {-3: 0, -2: 1, -1: 2, 0: 3, 1: 4, 2: 5, 3: 6}
    return sorted(list(allowed), key=lambda v: order.get(v, 99))


def _advance_mirror_state(state: Dict[str, Any], value: int) -> Dict[str, Any]:
    prev = state.get("prev")
    seq = list(state.get("seq") or [])
    phases = state.get("phases")

    nodes = [
        {"v": 0, "next": [1, 6]},
        {"v": 1, "next": [2]},
        {"v": 2, "next": [3]},
        {"v": 3, "next": [4]},
        {"v": 2, "next": [5]},
        {"v": 1, "next": [0]},
        {"v": -1, "next": [7]},
        {"v": -2, "next": [8]},
        {"v": -3, "next": [9]},
        {"v": -2, "next": [10]},
        {"v": -1, "next": [0]},
    ]
    value_to_nodes: Dict[int, List[int]] = {}
    for i, node in enumerate(nodes):
        value_to_nodes.setdefault(int(node["v"]), []).append(i)

    if not isinstance(phases, list):
        phases = []

    if not seq:
        new_phases = value_to_nodes.get(int(value), [])
    else:
        if not phases and prev is not None:
            phases = value_to_nodes.get(int(prev), [])
        new_phases_set: Set[int] = set()
        for p in phases:
            try:
                p_int = int(p)
            except Exception:
                continue
            if not (0 <= p_int < len(nodes)):
                continue
            for n_idx in nodes[p_int]["next"]:
                if int(nodes[int(n_idx)]["v"]) == int(value):
                    new_phases_set.add(int(n_idx))
        new_phases = sorted(new_phases_set)

    return {
        "kind": "mirror",
        "phases": new_phases,
        "prev": value,
        "seq": seq + [value],
    }


def _allowed_values_for_state(state: Dict[str, Any], choices: Set[int], allow_zero_after_start: bool) -> List[int]:
    if state.get("kind") == "mirror":
        return _allowed_values_for_mirror_state(state, choices)
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
    if state.get("kind") == "mirror":
        return _advance_mirror_state(state, value)
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


PATTERN_DOMAIN = {-3, -2, -1, 0, 1, 2, 3}


def _initial_pattern_state(*, mirror_mode: bool = False) -> Dict[str, Any]:
    if mirror_mode:
        return {
            "kind": "mirror",
            "phases": [],
            "prev": None,
            "seq": [],
        }
    return {
        "kind": "classic",
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


def _continuation_options_for_sequence(seq: List[int], allow_zero_after_start: bool) -> List[int]:
    state = _initial_pattern_state()
    for idx, value in enumerate(seq):
        allowed = _allowed_values_for_state(state, PATTERN_DOMAIN, allow_zero_after_start)
        if value not in allowed:
            return []
        state = _advance_state(state, value, idx, allow_zero_after_start)
    return _allowed_values_for_state(state, PATTERN_DOMAIN, allow_zero_after_start)


def _infer_pattern_group_width(pattern_group: List[List[int]]) -> int:
    for seq in pattern_group:
        if seq:
            return len(seq)
    return 0


def build_chained_pattern_sequences(
    pattern_groups: List[List[List[int]]],
    allow_zero_after_start: bool,
    mirror_mode: bool = False,
    max_paths: Optional[int] = PATTERN_MAX_PATHS,
    beam_width: Optional[int] = PATTERN_BEAM_WIDTH,
) -> Tuple[List[List[int]], int]:
    if not pattern_groups:
        return [], 0
    states: List[Dict[str, Any]] = [_initial_pattern_state(mirror_mode=mirror_mode)]
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
                    # Basit beam budaması
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


def _find_mirror_chain_highlights(seq: List[int]) -> Set[int]:
    highlights: Set[int] = set()
    n = len(seq)
    if n == 0:
        return highlights
    zeros: List[int] = [i for i, v in enumerate(seq) if v == 0]
    if len(zeros) < 2:
        return highlights
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
    g = 0
    while g < len(groups):
        run_sign = groups[g]["sign"]
        r = g
        while r + 1 < len(groups):
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
            for k in range(g, r + 1):
                highlights.update(groups[k]["idxs"])
            for k in range(g + 1, r + 1):
                z_boundary = zeros[k]
                if 0 <= z_boundary < n:
                    highlights.add(z_boundary)
        g = r + 1
    return highlights


def render_combined_pattern_panel(
    pattern_groups: List[List[List[int]]],
    meta_groups: List[Dict[str, Any]],
    allow_zero_after_start: bool,
    mirror_mode: bool = False,
) -> str:
    group_count = len(pattern_groups)
    if group_count < 2:
        return ""
    combined, total_unique = build_chained_pattern_sequences(
        pattern_groups,
        allow_zero_after_start=allow_zero_after_start,
        mirror_mode=mirror_mode,
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
        flat_loss_totals: List[Optional[List[Optional[float]]]] = []
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
            raw_jokers = meta.get("joker_indices") if isinstance(meta, dict) else None
            if isinstance(raw_jokers, list):
                for j in raw_jokers:
                    try:
                        j_int = int(j)
                    except Exception:
                        continue
                    flat_joker_indices.add(cursor + j_int)

            raw_loss = meta.get("loss_totals") if isinstance(meta, dict) else None
            loss_rows: List[Optional[List[Optional[float]]]] = []
            if isinstance(raw_loss, list):
                for row in raw_loss:
                    if not isinstance(row, list):
                        loss_rows.append(None)
                        continue
                    vals: List[Optional[float]] = []
                    for v in row[:7]:
                        try:
                            vals.append(float(v))
                        except Exception:
                            vals.append(None)
                    if len(vals) < 7:
                        vals.extend([None] * (7 - len(vals)))
                    loss_rows.append(vals)

            if length_for_cursor:
                if width:
                    if len(loss_rows) < width:
                        loss_rows.extend([None] * (width - len(loss_rows)))
                    elif len(loss_rows) > width:
                        loss_rows = loss_rows[:width]
                else:
                    if len(loss_rows) < length_for_cursor:
                        loss_rows.extend([None] * (length_for_cursor - len(loss_rows)))
                    elif len(loss_rows) > length_for_cursor:
                        loss_rows = loss_rows[:length_for_cursor]
                flat_loss_totals.extend(loss_rows if loss_rows else [None] * length_for_cursor)

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

        def _pattern_loss_total(seq: List[int]) -> Optional[float]:
            if not flat_loss_totals:
                return None
            if len(seq) > len(flat_loss_totals):
                return None
            total = 0.0
            for pos, off in enumerate(seq):
                row = flat_loss_totals[pos]
                if row is None:
                    return None
                idx = int(off) + 3
                if idx < 0 or idx >= len(row):
                    return None
                v = row[idx]
                if v is None:
                    return None
                total += float(v)
            return total

        def _render_group(patterns: List[List[int]]) -> str:
            pattern_highlights: List[Set[int]] = [
                _find_mirror_chain_highlights(seq) for seq in patterns
            ]
            losses = [_pattern_loss_total(seq) for seq in patterns]
            use_losses = any(v is not None for v in losses)
            return render_pattern_panel(
                [],
                allow_zero_after_start=allow_zero_after_start,
                mirror_mode=mirror_mode,
                file_names=flat_names if flat_names else None,
                joker_indices=flat_joker_indices if flat_joker_indices else None,
                sequence_name=None,
                precomputed_patterns=patterns,
                highlight_positions=pattern_highlights,
                pattern_line_losses=losses if use_losses else None,
            )

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


def build_patterns_from_xyz_lists(
    xyz_sets: List[Set[int]],
    allow_zero_after_start: bool,
    mirror_mode: bool = False,
    max_paths: Optional[int] = PATTERN_MAX_PATHS,
    beam_width: Optional[int] = PATTERN_BEAM_WIDTH,
) -> List[List[int]]:
    if not xyz_sets:
        return []
    # Başlangıç durumu
    states: List[Dict[str, Any]] = [_initial_pattern_state(mirror_mode=mirror_mode)]
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

    results: List[List[int]] = [
        st["seq"] for st in states if len(st.get("seq", [])) == len(xyz_sets)
    ]
    if max_paths is not None:
        return results[:max_paths]
    return results


def render_pattern_panel(
    xyz_sets: List[Set[int]],
    allow_zero_after_start: bool,
    mirror_mode: bool = False,
    file_names: Optional[List[str]] = None,
    joker_indices: Optional[Set[int]] = None,
    sequence_name: Optional[str] = None,
    precomputed_patterns: Optional[List[List[int]]] = None,
    highlight_positions: Optional[List[Set[int]]] = None,
    pattern_line_losses: Optional[List[Optional[float]]] = None,
) -> str:
    patterns = (
        precomputed_patterns
        if precomputed_patterns is not None
        else build_patterns_from_xyz_lists(
            xyz_sets,
            allow_zero_after_start=allow_zero_after_start,
            mirror_mode=mirror_mode,
        )
    )
    if not patterns:
        return "<div class='card'><h3>Örüntüleme</h3><div>Örüntü bulunamadı.</div></div>"
    def _build_state_for_seq(seq: List[int]) -> Dict[str, Any]:
        st: Dict[str, Any] = _initial_pattern_state(mirror_mode=mirror_mode)
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
        label = "".join(parts)
        st = _build_state_for_seq(seq)
        opts = _allowed_values_for_state(st, domain, allow_zero_after_start)
        cont = ", ".join(_fmt_off(v) for v in opts) if opts else "-"
        number_html = f"<span style='display:inline-block; min-width:1.8em; font-weight:bold;'>{idx_line + 1}.</span>"
        loss_html = ""
        if pattern_line_losses is not None and idx_line < len(pattern_line_losses):
            loss_val = pattern_line_losses[idx_line]
            loss_html = f" <span style='color:#555;'>loss: {html.escape(format_pip(loss_val))}</span>"
        lines.append(f"<div class='pat-line'>{number_html} {label} (devam: {html.escape(cont)}){loss_html}</div>")
    # Son değerlerin özeti (benzersiz, sıralı)
    last_vals = [seq[-1] for seq in patterns if seq]
    order = {-3: 0, -2: 1, -1: 2, 0: 3, 1: 4, 2: 5, 3: 6}
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
MAX_FILES = 50
MAX_FILES_CONVERTER = 100

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
    close_key = pick("close (last)", "close", "last", "c", "close last", "close(last)", "latest", "price", "close price", "last price")
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
      <h2>app120</h2>
    </header>
    <nav class='tabs'>
      <a href='/' class='{ 'active' if active_tab=="analyze" else '' }'>Analiz</a>
      <a href='/dc' class='{ 'active' if active_tab=="dc" else '' }'>DC List</a>
      <a href='/matrix' class='{ 'active' if active_tab=="matrix" else '' }'>Matrix</a>
      <a href='/iov' class='{ 'active' if active_tab=="iov" else '' }'>IOV Tarama</a>
      <a href='/iou' class='{ 'active' if active_tab=="iou" else '' }'>IOU Tarama</a>
      <a href='/loss' class='{ 'active' if active_tab=="loss" else '' }'>loss</a>
      <a href='/converter' class='{ 'active' if active_tab=="converter" else '' }'>60→120 Converter</a>
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
            <div>120m</div>
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
              <option value='S3'>S3</option>
              <option value='S4'>S4</option>
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
    <p><strong>Not:</strong> DC istisnaları: 18:00 DC değildir; 20:00 (Pazar hariç) DC olamaz; Cuma 16:00 DC sayılmaz. IOU kısıtları: 16:00 ve 18:00 IOU değildir; 20:00 tüm günlerde IOU değildir; ayrıca <strong>Pazar günü hiçbir mum IOU olamaz</strong>.</p>
    """
    return page("app120", body, active_tab="analyze")

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
            <div>120m</div>
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
    <p>Not: app120 sayımında DC'ler her zaman atlanır; bu sayfada tüm DC'ler listelenir.</p>
    <p><strong>Önemli:</strong> DC: 18:00 her zaman dışlanır; 20:00 yalnız Pazar hariç DC olabilir; Cuma 16:00 DC sayılmaz. IOU: 16:00 ve 18:00 her gün yok; 20:00 tüm günlerde IOU olamaz; <strong>Pazar günü IOU yoktur</strong>.</p>
    """
    return page("app120 - DC List", body, active_tab="dc")

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
            <div>120m</div>
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
              <option value='S3'>S3</option>
              <option value='S4'>S4</option>
            </select>
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>Matrix Göster</button>
        </div>
      </form>
    </div>
    """
    return page("app120 - Matrix", body, active_tab="matrix")

def render_iov_index() -> bytes:
    body = """
    <div class='card'>
      <form method='post' action='/iov' enctype='multipart/form-data'>
        <div class='row'>
          <div>
            <label>CSV</label>
            <input type='file' name='csv' accept='.csv,text/csv' required multiple />
          </div>
          <div>
            <label>Zaman Dilimi</label>
            <div>120m</div>
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
              <option value='S3'>S3</option>
              <option value='S4'>S4</option>
            </select>
          </div>
          <div>
            <label>Limit (|OC|, |PrevOC|)</label>
            <input type='number' step='0.0001' min='0' value='0.1' name='limit' />
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>IOV Tara</button>
        </div>
      </form>
    </div>
    <p>Limit, mumun OC ve PrevOC değerlerinin mutlak değeri için eşik belirler. Değeri aşan ve zıt işaretli çiftler IOV olarak raporlanır. Aynı anda birden fazla CSV seçebilirsin.</p>
    """
    return page("app120 - IOV", body, active_tab="iov")

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
            <div>120m</div>
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
              <option value='S3'>S3</option>
              <option value='S4'>S4</option>
            </select>
          </div>
          <div>
            <label>Limit (|OC|, |PrevOC|)</label>
            <input type='number' step='0.0001' min='0' value='0.1' name='limit' />
          </div>
          <div>
            <label>Tolerans</label>
            <div>0</div>
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
          <label style='display:flex; align-items:center; gap:8px;'>
            <input type='checkbox' name='pattern_mirror_mode' />
            <span>Ayna örüntü (0, ±1,±2,±3,±2,±1, 0...)</span>
          </label>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>IOU Tara</button>
        </div>
      </form>
    </div>
    <p>IOU mumlar, limit üzerindeki OC ve PrevOC değerlerinin aynı işareti paylaştığı durumlarda raporlanır. Aynı anda birden fazla CSV seçebilirsin.</p>
    <p><strong>Not:</strong> 16:00 ve 18:00 mumları IOU üretmez; 20:00 mumları tüm günlerde IOU olamaz; <strong>Pazar günü IOU yoktur</strong>.</p>
    """

def _render_iou_followup_form(state_token: str) -> str:
    form_html = render_iou_form()
    form_content = form_html.replace("<form method='post' action='/iou' enctype='multipart/form-data'>", "").replace("</form>", "").strip()
    token_input = f"<input type='hidden' name='state_token' value='{html.escape(state_token)}'>" if state_token else ""
    return (
        "<hr style='margin:32px 0; border:none; border-top:2px solid #ddd;'>"
        "<h2 style='margin-top:24px;'>Yeni Analiz</h2>"
        "<div class='card'>"
        "<form method='post' action='/iou' enctype='multipart/form-data'>"
        + token_input
        + form_content +
        "</form>"
        "</div>"
    )

def render_iou_index() -> bytes:
    body = render_iou_form()
    return page("app120 - IOU", body, active_tab="iou")

def render_loss_form(
    *,
    default_sequence: str = "S1",
    default_limit: float = 0.1,
    previous_results_html_encoded: Optional[str] = None,
) -> str:
    seq_key = (default_sequence or "S1").strip().upper()
    if seq_key not in SEQUENCES:
        seq_key = "S1"

    hidden_prev = ""
    if previous_results_html_encoded:
        hidden_prev = (
            f"<input type='hidden' name='previous_results_html' value='{html.escape(previous_results_html_encoded)}'>"
        )

    selected_s1 = " selected" if seq_key == "S1" else ""
    selected_s2 = " selected" if seq_key == "S2" else ""
    selected_s3 = " selected" if seq_key == "S3" else ""
    selected_s4 = " selected" if seq_key == "S4" else ""

    return f"""
    <div class='card'>
      <form method='post' action='/loss' enctype='multipart/form-data'>
        {hidden_prev}
        <div class='row'>
          <div>
            <label>CSV</label>
            <input type='file' name='csv' accept='.csv,text/csv' required multiple />
          </div>
          <div>
            <label>Zaman Dilimi</label>
            <div>120m</div>
          </div>
          <div>
            <label>Dizi</label>
            <select name='sequence'>
              <option value='S1'{selected_s1}>S1</option>
              <option value='S2'{selected_s2}>S2</option>
              <option value='S3'{selected_s3}>S3</option>
              <option value='S4'{selected_s4}>S4</option>
            </select>
          </div>
          <div>
            <label>Limit (|PrevOC|)</label>
            <input type='number' step='0.0001' min='0' value='{html.escape(str(default_limit))}' name='limit' />
          </div>
          <div>
            <label>Tolerans</label>
            <div>0</div>
          </div>
        </div>
        <div style='margin-top:12px;'>
          <button type='submit'>Hesapla</button>
        </div>
      </form>
    </div>
    <p>
      Mantık (app90 ile aynı): <code>|PrevOC| ≥ limit</code> ise katkı hesaplanır.
      OC ve PrevOC zıt işaretliyse <code>+abs(OC)</code>, aynı işaretliyse <code>-abs(OC)</code>.
      Tolerans sabit <code>0</code>. Aynı anda birden fazla CSV seçebilirsin.
    </p>
    """

def render_loss_index() -> bytes:
    body = render_loss_form()
    return page("app120 - loss", body, active_tab="loss")

def render_converter_index() -> bytes:
    body = """
    <div class='card'>
      <form method='post' action='/converter' enctype='multipart/form-data'>
        <label>CSV (60m, UTC-5)</label>
        <input type='file' name='csv' accept='.csv,text/csv' required multiple />
        <div style='margin-top:12px;'>
          <button type='submit'>120m'e Dönüştür</button>
        </div>
      </form>
    </div>
    <p>Girdi UTC-5 60 dakikalık mumlar olmalıdır. Çıktı UTC-4 120 dakikalık mumlar olarak indirilir (2 × 60m = 1 × 120m).</p>
    <p>Birden fazla CSV seçersen her biri 120m'e dönüştürülür; birden fazla dosya seçildiğinde sonuçlar ZIP paketi olarak indirilir.</p>
    """
    return page("app120 - Converter", body, active_tab="converter")

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

class App120Handler(BaseHTTPRequestHandler):
    server_version = "Candles120/1.0"
    sys_version = ""
    def do_GET(self):
        parsed = urlsplit(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        asset = try_load_asset(path)
        if asset:
            payload, content_type = asset
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            _add_security_headers(self)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if path == "/":
            body = render_analyze_index()
        elif path == "/dc":
            body = render_dc_index()
        elif path == "/matrix":
            body = render_matrix_index()
        elif path == "/iov":
            body = render_iov_index()
        elif path == "/iou":
            state_token = ""
            if "state" in query:
                state_token = _safe_state_token(query.get("state", [""])[0])
            body_html = render_iou_form()
            if state_token:
                iou_state = _read_state(state_token)
                if iou_state:
                    previous_results_html = iou_state.get("previous_results_html")
                    if isinstance(previous_results_html, str) and previous_results_html:
                        body_html = previous_results_html + _render_iou_followup_form(state_token)
            body = page("app120 - IOU", body_html, active_tab="iou")
        elif path == "/loss":
            body = render_loss_index()
        elif path == "/converter":
            body = render_converter_index()
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
            file_field = form.get("csv") or {}
            files = [entry for entry in file_field.get("files", []) if entry.get("data") is not None]
            if not files and self.path != "/iou":
                raise ValueError("CSV dosyası bulunamadı")
            max_files = MAX_FILES_CONVERTER if self.path == "/converter" else MAX_FILES
            if len(files) > max_files:
                self.send_response(413)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(f"Too many files (max {max_files}).".encode("utf-8"))
                return

            def decode_entry(entry: Dict[str, Any]) -> str:
                raw = entry.get("data")
                if isinstance(raw, (bytes, bytearray)):
                    return raw.decode("utf-8", errors="replace")
                return str(raw)

            if self.path == "/converter":
                outputs: List[Tuple[str, bytes]] = []
                used_names: set[str] = set()

                for entry in files:
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
                    if tf_est is None or abs(tf_est - 60) > 1.0:
                        name = entry.get("filename") or "dosya"
                        raise ValueError(f"{name}: Girdi 60 dakikalık akış gibi görünmüyor")
                    shifted, _ = adjust_to_output_tz(candles_entry, "UTC-5")
                    converted = convert_60m_to_120m(shifted)

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
                    download_name = _sanitize_csv_filename(entry.get("filename") or "converted", "_120m.csv")
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
                bundle_name = "converted_120m_bundle.zip"

                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{bundle_name}"')
                self.send_header("Content-Length", str(len(zip_bytes)))
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(zip_bytes)
                return

            sequence = (form.get("sequence", {}).get("value") or "S1").strip() if self.path in ("/analyze", "/matrix", "/iov", "/iou") else "S1"
            offset_s = (form.get("offset", {}).get("value") or "0").strip() if self.path == "/analyze" else "0"
            show_dc = ("show_dc" in form) if self.path == "/analyze" else False
            tz_label_sel = (form.get("input_tz", {}).get("value") or "UTC-4").strip()

            tz_norm = tz_label_sel.upper().replace(" ", "")
            tz_label = "UTC-4 -> UTC-4 (+0h)"
            tz_shift = timedelta(hours=1) if tz_norm in {"UTC-5", "UTC-05", "UTC-05:00", "-05:00"} else timedelta(0)
            if tz_shift:
                tz_label = "UTC-5 -> UTC-4 (+1h)"

            def load_counter_candles(entry: Dict[str, Any]) -> List[CounterCandle]:
                text_local = decode_entry(entry)
                try:
                    candles_local = load_candles_from_text(text_local, CounterCandle)
                except ValueError as exc:
                    name = entry.get("filename") or "dosya"
                    raise ValueError(f"{name}: {exc}")
                if not candles_local:
                    name = entry.get("filename") or "dosya"
                    raise ValueError(f"{name}: Veri boş veya çözümlenemedi")
                if tz_shift:
                    candles_local = [
                        CounterCandle(
                            ts=c.ts + tz_shift,
                            open=c.open,
                            high=c.high,
                            low=c.low,
                            close=c.close,
                        )
                        for c in candles_local
                    ]
                return candles_local

            if self.path in ("/iov", "/iou"):
                limit_raw = (form.get("limit", {}).get("value") or "0").strip()
                try:
                    limit_val = float(limit_raw)
                except Exception:
                    limit_val = 0.0
                limit_val = abs(limit_val)
                detector = detect_iov_candles if self.path == "/iov" else detect_iou_candles
                metric_label = "IOV" if self.path == "/iov" else "IOU"
                xyz_enabled = metric_label == "IOU" and "xyz_mode" in form
                summary_mode = metric_label == "IOU" and "xyz_summary" in form
                pattern_enabled = metric_label == "IOU" and "pattern_mode" in form
                pattern_mirror_mode = metric_label == "IOU" and "pattern_mirror_mode" in form
                confirm_iou = metric_label == "IOU" and "confirm_iou" in form
                 
                # Önceki sonuçları al (eğer varsa) - sadece IOU için
                state_token = ""
                iou_state: Optional[Dict[str, Any]] = None
                previous_results_html = ""
                pattern_payload_obj: Any = None
                pattern_groups_history: List[List[List[int]]] = []
                pattern_meta_history: List[Dict[str, Any]] = []
                pattern_allow_zero_after_start = True
                if metric_label == "IOU":
                    state_token = (form.get("state_token", {}).get("value") or "").strip()
                    iou_state = _read_state(state_token) if state_token else None
                    if iou_state:
                        prev_val = iou_state.get("previous_results_html")
                        if isinstance(prev_val, str):
                            previous_results_html = prev_val
                        pattern_payload_obj = iou_state.get("pattern_payload")
                    else:
                        legacy_prev = form.get("previous_results_html", {}).get("value", "")
                        if legacy_prev:
                            try:
                                previous_results_html = base64.b64decode(legacy_prev.encode("ascii")).decode("utf-8")
                            except Exception:
                                previous_results_html = ""
                        legacy_payload = form.get("previous_pattern_payload", {}).get("value", "")
                        if legacy_payload:
                            try:
                                decoded_payload = base64.b64decode(legacy_payload.encode("ascii"))
                                pattern_payload_obj = json.loads(decoded_payload.decode("utf-8"))
                            except Exception:
                                pattern_payload_obj = None
                        if (previous_results_html or pattern_payload_obj is not None) and not state_token:
                            state_token = _new_state_token()
                            iou_state = {"previous_results_html": previous_results_html, "pattern_payload": pattern_payload_obj}
                            _write_state(state_token, iou_state)

                    payload_obj = pattern_payload_obj
                    if payload_obj is not None:
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
                                    joker_out = []
                                    for j in jokers:
                                        try:
                                            joker_out.append(int(j))
                                        except Exception:
                                            continue
                                else:
                                    joker_out = []
                                seq_out = ""
                                seq_raw = meta.get("sequence")
                                if isinstance(seq_raw, str):
                                    seq_out = seq_raw.strip()
                                limit_out: Optional[float] = None
                                limit_raw = meta.get("limit")
                                try:
                                    if limit_raw is not None:
                                        limit_out = float(limit_raw)
                                except Exception:
                                    limit_out = None
                                loss_raw = meta.get("loss_totals")
                                loss_out: List[List[Optional[float]]] = []
                                if isinstance(loss_raw, list):
                                    for row in loss_raw:
                                        if not isinstance(row, list):
                                            continue
                                        vals: List[Optional[float]] = []
                                        for v in row[:7]:
                                            try:
                                                vals.append(float(v))
                                            except Exception:
                                                vals.append(None)
                                        if len(vals) < 7:
                                            vals.extend([None] * (7 - len(vals)))
                                        loss_out.append(vals)
                                pattern_meta_history.append(
                                    {
                                        "file_names": names_out,
                                        "joker_indices": joker_out,
                                        "sequence": seq_out,
                                        "limit": limit_out,
                                        "loss_totals": loss_out,
                                    }
                                )
                        if len(pattern_meta_history) < len(pattern_groups_history):
                            pattern_meta_history.extend({} for _ in range(len(pattern_groups_history) - len(pattern_meta_history)))
                        elif len(pattern_meta_history) > len(pattern_groups_history):
                            pattern_meta_history = pattern_meta_history[:len(pattern_groups_history)]
                
                tolerance_val = 0.0
                limit_margin = limit_val

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

                # İlk adım: Joker seçimi ekranı (yalnız IOU için)
                if metric_label == "IOU" and not confirm_iou and files:
                    if not state_token:
                        state_token = _new_state_token()
                    if iou_state is None:
                        iou_state = {}
                    uploads = _store_state_uploads(state_token, files)
                    iou_state["uploads"] = uploads
                    iou_state["previous_results_html"] = previous_results_html
                    iou_state["pattern_payload"] = {
                        "groups": pattern_groups_history,
                        "allow_zero_after_start": pattern_allow_zero_after_start,
                        "mirror_mode": pattern_mirror_mode,
                        "meta": pattern_meta_history,
                    }
                    _write_state(state_token, iou_state)
                    idx = 0
                    file_rows: List[str] = []
                    for entry in uploads:
                        name = entry.get("filename") or f"uploaded_{idx}.csv"
                        file_rows.append(
                            f"<tr><td>{idx+1}</td><td>{html.escape(name)}</td>"
                            f"<td><label style='display:flex;gap:8px;align-items:center;'><input type='checkbox' name='joker_{idx}' /> Joker</label></td></tr>"
                        )
                        idx += 1

                    def _hidden_bool(name: str, enabled: bool) -> str:
                        return f"<input type='hidden' name='{name}' value='1'>" if enabled else ""

                    sequence_val = (form.get("sequence", {}).get("value") or "S1").strip() or "S1"
                    tz_val = (form.get("input_tz", {}).get("value") or "UTC-4").strip()
                    preserved = [
                        f"<input type='hidden' name='state_token' value='{html.escape(state_token)}'>",
                        f"<input type='hidden' name='sequence' value='{html.escape(sequence_val)}'>",
                        f"<input type='hidden' name='input_tz' value='{html.escape(tz_val)}'>",
                        f"<input type='hidden' name='limit' value='{html.escape(str(limit_val))}'>",
                        _hidden_bool("xyz_mode", xyz_enabled),
                        _hidden_bool("xyz_summary", summary_mode),
                        _hidden_bool("pattern_mode", pattern_enabled),
                        _hidden_bool("pattern_mirror_mode", pattern_mirror_mode),
                        "<input type='hidden' name='confirm_iou' value='1'>",
                    ]
                    
                    table = (
                        "<table><thead><tr><th>#</th><th>Dosya</th><th>Joker</th></tr></thead>"
                        f"<tbody>{''.join(file_rows)}</tbody></table>"
                    )
                    
                    # Önceki sonuçları da göster (eğer varsa)
                    previous_section = ""
                    if previous_results_html:
                        # previous_results_html zaten decode edilmiş durumda
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
                        + "".join(preserved)
                        + "<div style='margin-top:12px;'><button type='submit'>Analizi Başlat</button></div>"
                        + "</form>"
                        + "</div>"
                    )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    _add_security_headers(self)
                    self.end_headers()
                    self.wfile.write(page("app120 IOU - Joker Seçimi", body, active_tab="iou"))
                    return

                if metric_label == "IOU" and confirm_iou and iou_state:
                    effective_entries = _load_state_upload_entries(iou_state)
                else:
                    effective_entries = b64_entries if b64_entries and metric_label == "IOU" else files
                if not effective_entries:
                    raise ValueError("CSV dosyası bulunamadı")
                joker_indices: Set[int] = set()
                if metric_label == "IOU":
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
                loss_totals_for_files: List[List[Optional[float]]] = []
                collect_branch_loss = bool(metric_label == "IOU" and pattern_enabled)
                for entry in effective_entries:
                    candles = load_counter_candles(entry)
                    if metric_label == "IOU":
                        report = detector(candles, sequence, limit_val, tolerance=tolerance_val)
                    else:
                        report = detector(candles, sequence, limit_val)
                    if collect_branch_loss:
                        loss_report = compute_prevoc_sum_report(
                            candles,
                            report.sequence,
                            report.limit,
                            0.0,
                            minutes_per_step=MINUTES_PER_STEP,
                        )
                        totals_by_offset = {o.offset: o.total for o in loss_report.offsets}
                        loss_totals_for_files.append(
                            [totals_by_offset.get(off) for off in (-3, -2, -1, 0, 1, 2, 3)]
                        )

                    offset_statuses = []
                    offset_counts = []
                    total_hits = 0
                    rows_html = []
                    offset_has_non_news: Dict[int, bool] = {}
                    offset_eliminations: Dict[int, List[str]] = {}
                    for item in report.offsets:
                        off_label = f"{('+' + str(item.offset)) if item.offset > 0 else str(item.offset)}"
                        status = item.offset_status or "-"
                        status_label = f"{off_label}: {status}"
                        if item.missing_steps:
                            status_label += f" (missing {item.missing_steps})"
                        offset_statuses.append(status_label)
                        offset_counts.append(f"{off_label}: {len(item.hits)}")
                        total_hits += len(item.hits)

                        if item.hits:
                            for hit in item.hits:
                                ts_s = hit.ts.strftime('%Y-%m-%d %H:%M:%S')
                                oc_label = format_pip(hit.oc)
                                prev_label = format_pip(hit.prev_oc)
                                dc_info = "True" if hit.dc_flag else "False"
                                if hit.used_dc:
                                    dc_info += " (rule)"

                                cells = [
                                    f"<td>{off_label}</td>",
                                    f"<td>{hit.seq_value}</td>",
                                    f"<td>{hit.idx}</td>",
                                    f"<td>{html.escape(ts_s)}</td>",
                                    f"<td>{html.escape(oc_label)}</td>",
                                    f"<td>{html.escape(prev_label)}</td>",
                                    f"<td>{dc_info}</td>",
                                ]

                                if metric_label == "IOU":
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
                                            elim_label = f"{hit.ts.strftime('%Y-%m-%d %H:%M:%S')} (seq {hit.seq_value})"
                                            bucket = offset_eliminations.setdefault(item.offset, [])
                                            if elim_label not in bucket:
                                                bucket.append(elim_label)
                                    cells.append(f"<td>{news_cell_html}</td>")

                                rows_html.append("<tr>" + "".join(cells) + "</tr>")

                    filename = entry.get("filename") or "uploaded.csv"
                    xyz_text = "-"
                    if metric_label == "IOU":
                        base_offsets = [-3, -2, -1, 0, 1, 2, 3]
                        xyz_offsets = [o for o in base_offsets if not offset_has_non_news.get(o, False)] if xyz_enabled else base_offsets
                        # Joker: XYZ tam kapsam
                        entry_idx = effective_entries.index(entry) if entry in effective_entries else -1
                        if entry_idx >= 0 and entry_idx in joker_indices:
                            xyz_offsets = base_offsets[:]
                        xyz_text = ", ".join((f"+{o}" if o > 0 else str(o)) for o in xyz_offsets) if xyz_offsets else "-"
                        all_xyz_sets.append(set(xyz_offsets))
                        all_file_names.append(filename)

                    if summary_mode:
                        elimination_rows = []
                        for offset in sorted(offset_eliminations.keys()):
                            offset_label = f"+{offset}" if offset > 0 else str(offset)
                            ts_joined = ", ".join(offset_eliminations[offset])
                            elimination_rows.append(f"{offset_label}: {ts_joined}")
                        elimination_cell = "<br>".join(html.escape(row) for row in elimination_rows) if elimination_rows else "-"
                        summary_entries.append({
                            "name": filename,
                            "xyz_text": xyz_text,
                            "elimination_html": elimination_cell,
                        })
                    else:
                        xyz_line = ""
                        if metric_label == "IOU" and xyz_enabled:
                            entry_idx = effective_entries.index(entry) if entry in effective_entries else -1
                            joker_tag = " (Joker)" if entry_idx >= 0 and entry_idx in joker_indices else ""
                            xyz_line = f"<div><strong>XYZ Kümesi{joker_tag}:</strong> {html.escape(xyz_text)}</div>"

                        info = (
                            f"<div class='card'>"
                            f"<h3>{html.escape(filename)}</h3>"
                            f"<div><strong>Data:</strong> {len(candles)} candles</div>"
                            f"<div><strong>Range:</strong> {html.escape(candles[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>"
                            f"<div><strong>TZ:</strong> {html.escape(tz_label)}</div>"
                            f"<div><strong>Sequence:</strong> {html.escape(report.sequence)}</div>"
                            f"<div><strong>Limit:</strong> {report.limit:.5f}</div>"
                            "<div><strong>Tolerans:</strong> 0</div>"
                            f"<div><strong>Base(18:00):</strong> idx={report.base_idx} status={html.escape(report.base_status)} ts={html.escape(report.base_ts.strftime('%Y-%m-%d %H:%M:%S')) if report.base_ts else '-'} </div>"
                            f"<div><strong>Offset durumları:</strong> {html.escape(', '.join(offset_statuses)) if offset_statuses else '-'} </div>"
                            f"<div><strong>Offset {metric_label} sayıları:</strong> {html.escape(', '.join(offset_counts)) if offset_counts else '-'} </div>"
                            f"<div><strong>Toplam {metric_label}:</strong> {total_hits}</div>"
                            f"{xyz_line}"
                            f"</div>"
                        )

                        if rows_html:
                            if metric_label == "IOU":
                                header = "<table><thead><tr><th>Offset</th><th>Seq</th><th>Index</th><th>Timestamp</th><th>OC</th><th>PrevOC</th><th>DC</th><th>Haber</th></tr></thead><tbody>"
                            else:
                                header = "<table><thead><tr><th>Offset</th><th>Seq</th><th>Index</th><th>Timestamp</th><th>OC</th><th>PrevOC</th><th>DC</th></tr></thead><tbody>"
                            table = header + "".join(rows_html) + "</tbody></table>"
                        else:
                            table = f"<p>{metric_label} mum bulunamadı.</p>"

                        sections.append(info + table)

                pattern_panel_html = ""
                combined_panel_html = ""
                if pattern_enabled:
                    current_patterns = build_patterns_from_xyz_lists(
                        all_xyz_sets,
                        allow_zero_after_start=pattern_allow_zero_after_start,
                        mirror_mode=pattern_mirror_mode,
                    )
                    current_meta = {
                        "file_names": all_file_names[:],
                        "joker_indices": sorted(joker_indices) if joker_indices else [],
                        "sequence": sequence,
                        "limit": limit_val,
                    }
                    if collect_branch_loss and loss_totals_for_files:
                        current_meta["loss_totals"] = loss_totals_for_files
                    pattern_panel_html = render_pattern_panel(
                        all_xyz_sets,
                        allow_zero_after_start=pattern_allow_zero_after_start,
                        mirror_mode=pattern_mirror_mode,
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
                        mirror_mode=pattern_mirror_mode,
                    )
                    pattern_groups_history = updated_history
                    pattern_meta_history = updated_meta_history

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
                    pattern_html = pattern_panel_html if pattern_panel_html else ""
                    current_result = "<div class='card'>" + table + "</div>" + pattern_html
                    if combined_panel_html:
                        current_result += combined_panel_html
                else:
                    # Non-summary: tüm dosya kartları + varsa örüntü paneli
                    if pattern_panel_html:
                        sections.append(pattern_panel_html)
                    if combined_panel_html:
                        sections.append(combined_panel_html)
                    current_result = "\n".join(sections)
                
                # Yeni analiz sonucunu bir bölüm içine al (yalnız IOU için)
                if metric_label == "IOU":
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
                    
                    if not state_token:
                        state_token = _new_state_token()
                    if iou_state is None:
                        iou_state = {}
                    iou_state["previous_results_html"] = body_without_form
                    iou_state["pattern_payload"] = {
                        "groups": pattern_groups_history,
                        "allow_zero_after_start": pattern_allow_zero_after_start,
                        "mirror_mode": pattern_mirror_mode,
                        "meta": pattern_meta_history,
                    }
                    _write_state(state_token, iou_state)
                    self.send_response(303)
                    self.send_header("Location", f"/iou?state={state_token}")
                    _add_security_headers(self)
                    self.end_headers()
                    return
                else:
                    # IOV için normal davranış
                    body = current_result
                
                tab_key = "iov" if self.path == "/iov" else "iou"
                title = f"app120 {metric_label}"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(page(title, body, active_tab=tab_key))
                return

            if self.path == "/loss":
                sequence = (form.get("sequence", {}).get("value") or "S1").strip() or "S1"
                limit_raw = (form.get("limit", {}).get("value") or "0").strip()

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

                offsets_order = [-3, -2, -1, 0, 1, 2, 3]
                aggregate_stats = {
                    o: {"total": 0.0, "hits": 0, "pos": 0, "neg": 0}
                    for o in offsets_order
                }

                cards: List[str] = []
                for entry in files:
                    name = entry.get("filename") or "dosya"
                    candles_entry = load_counter_candles(entry)
                    tf_est = estimate_timeframe_minutes(candles_entry)
                    tf_est_label = f"{tf_est:.2f}m" if tf_est is not None else "-"
                    if tf_est is None or abs(tf_est - MINUTES_PER_STEP) > 1.0:
                        raise ValueError(f"{name}: Girdi 120 dakikalık akış gibi görünmüyor")

                    try:
                        report = compute_prevoc_sum_report(
                            candles_entry,
                            sequence,
                            limit_val,
                            0.0,
                            minutes_per_step=MINUTES_PER_STEP,
                        )
                    except ValueError as exc:
                        raise ValueError(f"{name}: {exc}")

                    total_sum = sum(item.total for item in report.offsets)
                    effective_threshold = limit_val

                    info_lines = [
                        f"<div><strong>Dosya:</strong> {html.escape(name)}</div>",
                        f"<div><strong>Data:</strong> {len(candles_entry)} candles</div>",
                        f"<div><strong>Range:</strong> {html.escape(candles_entry[0].ts.strftime('%Y-%m-%d %H:%M:%S'))} -> {html.escape(candles_entry[-1].ts.strftime('%Y-%m-%d %H:%M:%S'))}</div>",
                        f"<div><strong>Girdi TZ:</strong> {html.escape(tz_label)}</div>",
                        f"<div><strong>TF:</strong> 120m | est={html.escape(tf_est_label)}</div>",
                        f"<div><strong>Dizi:</strong> {html.escape(report.sequence)}</div>",
                        f"<div><strong>Limit:</strong> {limit_val:.5f}</div>",
                        "<div><strong>Tolerans:</strong> 0</div>",
                        f"<div><strong>Eşik (|PrevOC|):</strong> {effective_threshold:.5f}</div>",
                        f"<div><strong>Genel toplam:</strong> {format_pip(total_sum)}</div>",
                    ]

                    summary_rows: List[str] = []
                    detail_sections: List[str] = []
                    for item in report.offsets:
                        label = f"+{item.offset}" if item.offset > 0 else str(item.offset)
                        contributions = item.contributions
                        hit_count = len(contributions)
                        pos_count = sum(1 for contrib in contributions if contrib.contribution > 0)
                        neg_count = sum(1 for contrib in contributions if contrib.contribution < 0)
                        ts_ref = item.actual_ts or item.target_ts
                        ts_label = ts_ref.strftime("%Y-%m-%d %H:%M:%S") if ts_ref else "-"
                        status_bits = [item.offset_status]
                        if item.missing_steps:
                            status_bits.append(f"missing {item.missing_steps}")
                        status_label = ", ".join(bit for bit in status_bits if bit)
                        summary_rows.append(
                            f"<tr><td>{label}</td><td>{hit_count}</td><td>{pos_count}</td><td>{neg_count}</td>"
                            f"<td>{format_pip(item.total)}</td><td>{html.escape(status_label)}</td><td>{html.escape(ts_label)}</td></tr>"
                        )

                        if contributions:
                            detail_rows = []
                            for contrib in contributions:
                                ts_s = contrib.ts.strftime("%Y-%m-%d %H:%M:%S")
                                rule_label = "Zıt (+)" if contrib.contribution > 0 else "Aynı (-)"
                                detail_rows.append(
                                    "<tr>"
                                    f"<td>{contrib.seq_value}</td>"
                                    f"<td>{contrib.idx}</td>"
                                    f"<td>{html.escape(ts_s)}</td>"
                                    f"<td>{format_pip(contrib.prev_oc)}</td>"
                                    f"<td>{format_pip(contrib.oc)}</td>"
                                    f"<td>{rule_label}</td>"
                                    f"<td>{format_pip(contrib.contribution)}</td>"
                                    f"<td>{'Evet' if contrib.dc_flag else 'Hayır'}</td>"
                                    "</tr>"
                                )
                        else:
                            detail_rows = ["<tr><td colspan='8'>Uygun mum bulunamadı.</td></tr>"]

                        detail_table = (
                            "<table><thead><tr><th>Seq</th><th>Index</th><th>Timestamp</th><th>PrevOC</th>"
                            "<th>OC</th><th>Kural</th><th>Katkı</th><th>DC</th></tr></thead>"
                            f"<tbody>{''.join(detail_rows)}</tbody>"
                            "<tfoot>"
                            "<tr>"
                            "<td colspan='6'><strong>Ara Toplam</strong></td>"
                            f"<td><strong>{format_pip(item.total)}</strong></td>"
                            "<td></td>"
                            "</tr>"
                            "</tfoot>"
                            "</table>"
                        )
                        detail_summary = (
                            f"{label} detay — Hit {hit_count}, Zıt {pos_count}, Aynı {neg_count}, Toplam {format_pip(item.total)}"
                        )
                        detail_sections.append(
                            f"<details{' open' if item.offset == 0 else ''}><summary>{html.escape(detail_summary)}</summary>{detail_table}</details>"
                        )

                        agg_entry = aggregate_stats.setdefault(item.offset, {"total": 0.0, "hits": 0, "pos": 0, "neg": 0})
                        agg_entry["total"] += item.total
                        agg_entry["hits"] += hit_count
                        agg_entry["pos"] += pos_count
                        agg_entry["neg"] += neg_count

                    summary_table = (
                        "<table><thead>"
                        "<tr><th>Offset</th><th>Hit</th><th>Zıt (+)</th><th>Aynı (-)</th><th>Toplam</th><th>Durum</th><th>Referans TS</th></tr>"
                        "</thead><tbody>"
                        + "".join(summary_rows)
                        + "</tbody></table>"
                    )

                    card_html = (
                        "<div class='card'>"
                        f"<h3>{html.escape(name)}</h3>"
                        + "".join(info_lines)
                        + summary_table
                        + "".join(detail_sections)
                        + "</div>"
                    )
                    cards.append(card_html)

                aggregate_rows = []
                total_all = 0.0
                for offset in offsets_order:
                    stats = aggregate_stats.get(offset, {"total": 0.0, "hits": 0, "pos": 0, "neg": 0})
                    total_all += stats["total"]
                    label = f"+{offset}" if offset > 0 else str(offset)
                    aggregate_rows.append(
                        f"<tr><td>{label}</td><td>{stats['hits']}</td><td>{stats['pos']}</td><td>{stats['neg']}</td><td>{format_pip(stats['total'])}</td></tr>"
                    )
                aggregate_table = (
                    "<table><thead><tr><th>Offset</th><th>Hit</th><th>Zıt (+)</th><th>Aynı (-)</th><th>Toplam</th></tr></thead>"
                    f"<tbody>{''.join(aggregate_rows)}</tbody></table>"
                )
                aggregate_card = (
                    "<div class='card'>"
                    "<h3>Toplam Özet</h3>"
                    f"<div><strong>Dosya sayısı:</strong> {len(files)}</div>"
                    f"<div><strong>Genel toplam:</strong> {format_pip(total_all)}</div>"
                    + aggregate_table
                    + "</div>"
                )

                current_result = "".join(cards) + aggregate_card

                from datetime import datetime
                result_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                result_section = (
                    f"<div id='result_{result_id}' style='margin-bottom:32px; padding-bottom:24px; border-bottom:2px solid #ddd;'>"
                    f"<h3 style='color:#0366d6; margin-bottom:16px;'>Analiz #{result_id}</h3>"
                    f"{current_result}"
                    f"</div>"
                )

                if previous_results_html:
                    body_without_form = previous_results_html + result_section
                else:
                    body_without_form = result_section

                body_encoded = base64.b64encode(body_without_form.encode("utf-8")).decode("ascii")
                form_section = (
                    "<hr style='margin:32px 0; border:none; border-top:2px solid #ddd;'>"
                    "<h2 style='margin-top:24px;'>Yeni Analiz</h2>"
                    + render_loss_form(
                        default_sequence=sequence,
                        default_limit=limit_val,
                        previous_results_html_encoded=body_encoded,
                    )
                )

                body = body_without_form + form_section

                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                _add_security_headers(self)
                self.end_headers()
                self.wfile.write(page("app120 - loss", body, active_tab="loss"))
                return

            if self.path == "/analyze":
                entry = files[0]
                candles = load_counter_candles(entry)
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
                    f"<div><strong>Zaman Dilimi:</strong> 120m</div>",
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
                self.wfile.write(page("app120 sonuçlar", body, active_tab="analyze"))
                return

            entry = files[0]
            candles = load_counter_candles(entry)
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
                    f"<div><strong>Zaman Dilimi:</strong> 120m</div>"
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
                self.wfile.write(page("app120 DC List", body, active_tab="dc"))
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
                    f"<div><strong>Zaman Dilimi:</strong> 120m</div>"
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
                self.wfile.write(page("app120 Matrix", body, active_tab="matrix"))
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
    httpd = HTTPServer((host, port), App120Handler)
    print(f"app120 web: http://{host}:{port}/")
    httpd.serve_forever()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app120.web", description="app120 için birleşik web arayüzü")
    parser.add_argument("--host", default="127.0.0.1", help="Sunucu adresi (vars: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=2120, help="Port (vars: 2120)")
    args = parser.parse_args(argv)
    run(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
