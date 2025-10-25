# WARP Guidance for c0dex

This document provides essential guidance for Warp instances working with the c0dex trading candle analysis suite.

## Quick Start Commands

### Running the Full Suite
```bash
# Run unified web interface (all apps via reverse proxy)
python -m appsuite.web --host 0.0.0.0 --port 8000

# Health check
curl localhost:8000/health
```

### Individual App Servers
```bash
# 120-minute analysis (port 2120)
python -m app120.web --host 0.0.0.0 --port 2120

# 80-minute analysis (port 2180)
python -m app80.web --host 0.0.0.0 --port 2180

# 72-minute analysis (port 2172)
python -m app72.web --host 0.0.0.0 --port 2172

# 48-minute analysis (port 2020)
python -m app48.web --host 0.0.0.0 --port 2020

# 60-minute analysis (port 2019)
python -m app321.web --host 0.0.0.0 --port 2019

# Calendar converter (port 2300)
python -m calendar_md.web --host 0.0.0.0 --port 2300

# Landing page (port 2000)
python -m landing.web --host 0.0.0.0 --port 2000
```

### CLI Analysis Examples
```bash
# 120m sequence analysis with DC flags
python -m app120.counter --csv data.csv --sequence S2 --offset +1 --show-dc

# Predict next 37 candles
python -m app120.counter --csv data.csv --predict 37

# 60m to 120m conversion
python -m app120 --csv 60m.csv --input-tz UTC-5 --output 120m.csv

# 20m to 80m conversion  
python -m app80.main --csv 20m.csv --input-tz UTC-5 --output 80m.csv

# 12m to 72m conversion
python -m app72.main --csv 12m.csv --input-tz UTC-5 --output 72m.csv
```

### Dependencies & Build
```bash
# Install dependencies (only gunicorn required)
pip install -r requirements.txt

# Docker build
docker build -t c0dex .

# Docker run
docker run -p 8080:8080 c0dex
```

## Architecture Overview

### High-Level Structure
- **appsuite/**: Unified reverse proxy serving all apps under single host
- **app120/**, **app80/**, **app72/**, **app48/**, **app321/**: Timeframe-specific analysis apps
- **calendar_md/**: Markdown to JSON calendar converter
- **landing/**: Simple landing page with app links
- **favicon/**: Shared static assets

### Core Apps (app48, app72, app80, app120, app321)
Each timeframe app provides:
- **CLI counter/analyzer** (`counter.py` or `main.py`)
- **Web interface** (`web.py`) with 4-6 tabs:
  - Analysis: Sequence counting, OC/PrevOC, DC detection
  - DC List: Raw distorted candle data
  - Matrix: All offset values in summary table
  - IOU Scanning: Same-sign OC/PrevOC detection with multi-file support
  - IOV Scanning: Opposite-sign detection (app120 only)
  - Converters: Timeframe conversion tools

### Data Flow
1. CSV upload → header normalization → timezone conversion (UTC-5 → UTC-4)
2. DC (Distorted Candle) flag computation with app-specific exceptions
3. Sequence alignment using S1/S2 arrays with offset calculations
4. OC (Close-Open) and PrevOC analysis
5. IOU/IOV signal detection with limit + tolerance thresholds
6. Optional news filtering (XYZ mode) for enhanced signal quality

## Key Concepts

### CSV Format
Required columns: `Time`, `Open`, `High`, `Low`, `Close (Last)` (synonyms supported)

### Sequences
- **S1**: `1, 3, 7, 13, 21, 31, 43, 57, 73, 91, 111, 133, 157`
- **S2**: `1, 5, 9, 17, 25, 37, 49, 65, 81, 101, 121, 145, 169`

### Distorted Candles (DC)
DC condition: `High ≤ prev.High`, `Low ≥ prev.Low`, `Close` within prev `[Open, Close]` range

**App-specific DC exceptions:**
- **app321**: 13:00-20:00 DCs treated as normal; 20:00 (except Sunday) never DC
- **app48**: 13:12-19:36 DCs treated as normal
- **app72**: 18:00, Fri 16:48, (except Sun) 19:12 & 20:24, Fri 16:00 never DC
- **app80**: (except Sun) 18:00, 19:20, 20:40; Fri 16:40 never DC
- **app120**: No exceptions except containment rule

### IOU/IOV Detection
- **IOU**: Same-sign OC/PrevOC pairs above `limit + tolerance` threshold
- **IOV**: Opposite-sign OC/PrevOC pairs above limit (app120 only)
- **Tolerance**: Default ±0.005, configurable in web forms
- **XYZ Filter**: Optional news-based filtering excluding non-news offsets

## Important Notes

### Multi-File Uploads
- Web forms support up to 25 CSV files simultaneously
- Results displayed in separate cards per file
- Same sequence/limit/timezone applied to all files

### Timezone Handling
- Input: UTC-4 or UTC-5 (if UTC-5 selected, +1 hour shift applied)
- Output: Always normalized to UTC-4
- Critical for accurate 18:00 base candle detection

### Special Features
- **app48**: Adds synthetic candles at 18:00/18:48 (except first day)
- **app120**: Full IOV/IOU scanning with multi-upload
- **News integration**: Economic calendar JSON files enhance IOU filtering

### Deployment
- **Render**: Uses `render.yaml` and `Procfile`
- **Railway**: Uses `railway.toml`
- **Docker**: Optimized `Dockerfile` for Fly.io
- **Python version**: 3.11+ required (`.python-version`)

## Testing
No explicit test framework detected. Use standard Python testing:
```bash
# If pytest available
pytest

# Standard unittest
python -m unittest discover
```

## Common Issues

1. **Port conflicts**: Each app uses specific ports - check if already in use
2. **Large file uploads**: Browser POST limits may apply with 25-file batches  
3. **Timezone confusion**: Always verify input timezone selection in web forms
4. **Missing 18:00 candles**: Base candle detection fails without proper market session data
5. **Tolerance misunderstanding**: Higher tolerance = fewer results (stricter filtering)

## Development Tips

- Use `appsuite.web` for unified development - it proxies all apps
- Check `/health` endpoint for service status
- CSV files should span multiple days for meaningful sequence analysis
- Monitor console logs for DC detection and sequence alignment issues
- XYZ news filtering requires proper economic calendar JSON structure

This codebase is optimized for financial timeframe analysis with sophisticated candle pattern detection and multi-app coordination via reverse proxy architecture.