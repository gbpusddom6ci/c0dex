# C0dex Multi-Timeframe Analysis Suite

This repository bundles five timeframe-specific analysis applications—**app48**, **app72**, **app80**, **app120**, and **app321**—alongside shared tooling. Each app can ingest market candles, normalise timestamps, detect distorted candles (DC), align sequence offsets, and surface IOU/IOV signals through both CLI utilities and lightweight web interfaces.

Supporting packages round out the stack: `appsuite` exposes the apps under a single reverse proxy, `landing` provides a simple entry page, `calendar_md` converts Markdown economic calendars into JSON feeds, and `favicon` hosts shared assets. The goal is to give any maintainer everything needed to understand and operate the system without leaving this repository.

---

## Table of Contents

1. [Technology Stack](#technology-stack)
2. [Directory Layout](#directory-layout)
3. [Application Overview](#application-overview)
4. [Data Flow & Shared Rules](#data-flow--shared-rules)
5. [Distorted Candle Exceptions](#distorted-candle-exceptions)
6. [IOU Exclusions](#iou-exclusions)
7. [IOV / IOU Signal Engine](#iov--iou-signal-engine)
8. [News Integration](#news-integration)
9. [CLI & Web Usage](#cli--web-usage)
10. [Sample Datasets](#sample-datasets)
11. [Deployment Notes](#deployment-notes)
12. [Developer Tips](#developer-tips)

---

## Technology Stack

- Python **3.11** (`.python-version` locks the runtime for Render deployments).
- Standard-library-heavy codebase; web servers use `http.server` for minimal HTTP handling.
- `gunicorn` ready for production hosting (see `requirements.txt`).
- No pandas/numpy dependency; data processing relies on custom helpers.

---

## Directory Layout

```
app48/      48-minute analysis package (CLI + web)
app72/      72-minute analysis package
app80/      80-minute analysis package
app120/     120-minute analysis package
app321/     60-minute analysis package
appsuite/   Reverse proxy that aggregates every app
landing/    Static landing page with navigation cards
calendar_md/Markdown → JSON economic calendar converter (CLI + web)
economic_calendar/ Sample JSON calendars consumed by IOU pages
favicon/    Shared favicon + manifest assets
ornek/      Curated CSV samples for manual testing
```

Timeframe folders follow the same pattern:

- `counter.py` or `main.py`: CLI counters, converters, and prediction helpers.
- `web.py`: Lightweight HTTP server with HTML forms, multi-file upload, and result tables.
- `__init__.py`: Package initialiser and shared utilities.

---

## Application Overview

| App      | Timeframe | Port | Converter | Highlighted Rules |
|----------|-----------|------|-----------|-------------------|
| app48    | 48 min    | 2020 | 12→48     | Synthetic 18:00 & 18:48 candles; 18:00/18:48/19:36 excluded from DC & IOU |
| app72    | 72 min    | 2172 | 12→72     | 18:00, 19:12, 20:24 barred from DC; first-week Friday 16:48 excluded from IOU |
| app80    | 80 min    | 2180 | 20→80     | 18:00, 19:20, 20:40 and every Friday 16:40 excluded from DC & IOU |
| app120   | 120 min   | 2120 | 60→120    | 18:00 excluded from DC & IOU; 20:00 (except Sundays) and every Friday 16:00 excluded |
| app321   | 60 min    | 2019 | —         | 20:00 (non-Sunday) cannot be DC; 18:00/19:00/20:00 excluded from IOU |
| appsuite | —         | 2100 | —         | Hosts every app under one reverse proxy |
| landing  | —         | 2000 | —         | Provides cards and quick links |

Every web UI supports multi-file CSV uploads, IOU/IOV scan tabs, and news-driven annotations rendered per file card.

---

## Data Flow & Shared Rules

1. **CSV ingestion** – Column headers such as `Time`, `Open`, `High`, `Low`, `Close (Last)` are normalised (synonyms accepted). Invalid rows drop out; rows are sorted by timestamp.
2. **Timezone normalisation** – If the source is `UTC-5`, all timestamps shift forward by 60 minutes to align with `UTC-4`.
3. **Synthetic candles** – app48 injects daily 18:00 and 18:48 synthetic candles (except on the first day) to preserve the closing window.
4. **DC computation** – `compute_dc_flags` marks distorted candles and prevents back-to-back DCs. Positive offsets step forward until they land on a non-DC candle.
5. **Sequence alignment** – Both `S1` (1,3,7,...) and `S2` (1,5,9,...) sequences are supported. The container rule pins sequence indices to DC timestamps when required.
6. **OC / PrevOC** – `OC = Close - Open`; `PrevOC` is the previous candle’s OC. Predictive rows display `-` for both.
7. **Offset system** – The first 18:00 candle acts as the base. Offsets span `-3` through `+3`. Missing candles trigger `pred` timestamps derived from the offset cadence.

---

## Distorted Candle Exceptions

- Baseline rule: a candle is DC when `High ≤ prev.High`, `Low ≥ prev.Low`, and `Close` falls inside the previous candle’s `[Open, Close]` range; consecutive DCs are disallowed.
- The 18:00 base candle is never marked as DC unless a module explicitly overrides it (none do).
- **app48** – Candles between 13:12 and 19:36 are treated as normal; synthetic 18:00 and 18:48 candles are never DC.
- **app72** – 18:00, 19:12, 20:24, and Friday 16:00 are never DC.
- **app80** – 18:00, 19:20, 20:40, and every Friday 16:40 are never DC.
- **app120** – 18:00 and Friday 16:00 are never DC; 20:00 candles are only eligible on Sundays.
- **app321** – 20:00 (non-Sunday) is never DC; additionally, 13:00–20:00 on weekdays are treated as normal candles even if they match the DC formula.

---

## IOU Exclusions

Each IOU scan rejects the following timestamps outright:

- **app48** – 18:00, 18:48, 19:36.
- **app72** – 18:00, 19:12, 20:24, and the first-week Friday 16:48 candle within the two-week dataset.
- **app80** – 18:00, 19:20, 20:40, and every Friday 16:40 candle.
- **app120** – 18:00, 20:00 on non-Sundays, and every Friday 16:00 candle.
- **app321** – 18:00, 19:00, 20:00.

All IOU scans honour a `limit` plus optional `± tolerance`. A hit survives only if both `|OC|` and `|PrevOC|` exceed `limit + tolerance`.

---

## IOV / IOU Signal Engine

The CLI and web layers share the same signal pipeline:

1. Load CSV data, normalise timestamps, and apply DC exceptions.
2. Locate the first 18:00 base candle; adjust positive offsets to the next non-DC candle when needed.
3. Allocate sequence indices using the container rule whenever an index lands on a DC candle.
4. Compute `OC` and `PrevOC`, then filter:
   - **IOV** looks for opposite-signed pairs over the limit threshold.
   - **IOU** looks for same-signed pairs over `limit + tolerance`.
5. Render offset-grouped cards with `syn/real` flags and `(rule)` annotations when a value comes from the container rule.
6. When XYZ filtering is enabled, only offsets with qualifying news or slot overrides remain.

Limits default to positive thresholds; negative user input is converted via absolute value. With `limit = 0`, only non-zero OC values qualify.

---

## News Integration

All IOU tabs consume JSON calendars from `economic_calendar/` using `news_loader.py`:

- **Schema** – Recognised fields include `date`, `time`, `time_24h`, `title`, `currency`, `impact`, `all_day`, `recent_null`, `actual`, `forecast`, and `previous`. Missing fields degrade gracefully.
- **Category mapping**
  - `holiday` – Title contains “holiday” and the event is all-day with a null actual value.
  - `all-day` – All-day events that are not holidays (e.g., OPEC meetings, German Prelim CPI).
  - `speech` – Timed events with a null actual value.
  - `normal` – Standard data releases.
- Categories are informational; `holiday` and `all-day` records never cause XYZ filtering to drop a hit.
- Events tagged `recent_null=true` display with a `(null)` suffix to highlight pending data.
- app72 safeguards the special slots 16:48, 18:00, 19:12, and 20:24—these offsets survive even if no matching news is found.

XYZ filtering removes offsets with no effective news or protected slot. Holiday and all-day entries keep the offset but remain clearly labeled.

---

## CLI & Web Usage

Optional virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Web Interfaces

```bash
python3 -m landing.web      --host 0.0.0.0 --port 2000
python3 -m appsuite.web     --host 0.0.0.0 --port 2100
python3 -m app48.web        --host 0.0.0.0 --port 2020
python3 -m app72.web        --host 0.0.0.0 --port 2172
python3 -m app80.web        --host 0.0.0.0 --port 2180
python3 -m app120.web       --host 0.0.0.0 --port 2120
python3 -m app321.web       --host 0.0.0.0 --port 2019
python3 -m calendar_md.web  --host 0.0.0.0 --port 2300
```

Each interface supports multi-file upload, configurable IOU limits/tolerance, optional XYZ filtering, and CSV downloads where relevant.

### CLI Examples

```bash
# app120 analysis using sequence S2, +1 offset, and DC visibility
python3 -m app120.counter --csv data.csv --sequence S2 --offset +1 --show-dc

# 60 → 120 minute converter
python3 -m app120 --csv 60m.csv --input-tz UTC-5 --output 120m.csv

# app80 IOU scan with custom thresholds
python3 -m app80.counter --csv data.csv --sequence S1 --scan-iou --limit 0.08 --tolerance 0.005

# app48 prediction mode
python3 -m app48.main --csv data.csv --predict 49

# Markdown economic calendar → JSON
python3 -m calendar_md --input calendar.md --output economic_calendar/calendar.json --year 2025
```

All CLI tools expose `--help` for full argument listings.

---

## Sample Datasets

Automated fixtures are not bundled. Instead, `ornek/` contains manually curated CSVs representing real-world scenarios for every timeframe. Use them to validate IOU/IOV behaviour, news categories, and tolerance handling. Add your own datasets to the same directory as needed.

---

## Deployment Notes

- `render.yaml` and `Procfile` illustrate Render hosting commands.
- `railway.toml` covers Railway/Nixpacks defaults.
- `Dockerfile` bootstraps a minimal Python image that can launch the web services.
- Assign distinct ports per service in production; `appsuite` is the recommended way to serve everything through a single entrypoint.

---

## Developer Tips

- Run `python3 -m compileall .` for a quick syntax check; there is no unittest/pytest harness baked in.
- Multi-upload IOU tabs are the fastest way to sanity-check new data or tolerance changes.
- When extending to a new timeframe, treat `app120` as the reference implementation and reuse the shared DC/IOU helpers.
- Ignore `__pycache__` directories; they should not be committed.
- Keep calendar JSON feeds fresh. Null actual values drive the `speech` category, and the `all-day + null` combination marks informative all-day events without impacting XYZ filtering.

As a quick orientation exercise, start `landing.web` or `appsuite.web`, upload the samples under `ornek/`, and inspect the IOU cards. You will see DC exceptions, news labelling, and tolerance thresholds in action within minutes.
