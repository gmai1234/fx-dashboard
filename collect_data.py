#!/usr/bin/env python3
"""
FX Dashboard Data Collector
- 7 FRED foreign exchange series + Broad USD Index
- Outputs fx_data.js (window.FX_DATA = {...})

Env:
  FRED_API_KEY (required) - set via GitHub Secrets

Safety:
  - DXY and USD_KRW are required. If empty, abort and keep existing fx_data.js.
"""

import json
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()

# 7 series. Each entry: (FRED series_id, display label, direction note)
# Note on FRED quoting conventions:
#   DTWEXBGS = Broad Trade-Weighted USD Index (level)
#   DEXKOUS  = KRW per USD (i.e. 1 USD = X KRW) — natural USD/KRW
#   DEXJPUS  = JPY per USD — natural USD/JPY
#   DEXCHUS  = CNY per USD — natural USD/CNY
#   DEXSZUS  = CHF per USD — natural USD/CHF
#   DEXUSEU  = USD per EUR — natural EUR/USD
#   DEXUSUK  = USD per GBP — natural GBP/USD
SERIES = [
    ("DXY",     "DTWEXBGS", "Broad USD Index"),
    ("USD_KRW", "DEXKOUS",  "USD/KRW"),
    ("USD_JPY", "DEXJPUS",  "USD/JPY"),
    ("USD_CNY", "DEXCHUS",  "USD/CNY"),
    ("USD_CHF", "DEXSZUS",  "USD/CHF"),
    ("EUR_USD", "DEXUSEU",  "EUR/USD"),
    ("GBP_USD", "DEXUSUK",  "GBP/USD"),
]

OBSERVATION_LIMIT = 800   # ~3 years daily
REQUIRED = {"DXY", "USD_KRW"}
OUTPUT_PATH = "fx_data.js"


def fetch_series(series_id, max_retries=4):
    """Return list of {date, value} for the given FRED series (desc by date)."""
    url_base = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": FRED_API_KEY,
        "file_type": "json",
        "limit": OBSERVATION_LIMIT,
        "sort_order": "desc",
    }
    url = url_base + "?" + urllib.parse.urlencode(params)

    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = json.loads(resp.read().decode())
                obs = data.get("observations", [])
                clean = []
                for o in obs:
                    v = o.get("value", "")
                    if v in (".", "", None):
                        continue
                    try:
                        clean.append({"date": o["date"], "value": float(v)})
                    except (ValueError, TypeError):
                        continue
                return clean
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  [{series_id}] retry {attempt+1}/{max_retries} after {wait}s: {e}", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"fetch_series({series_id}) failed: {last_err}")


def main():
    if not FRED_API_KEY:
        print("ERROR: FRED_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    print(f"Collecting {len(SERIES)} FX series from FRED...")

    series_data = {}
    with ThreadPoolExecutor(max_workers=4) as exe:
        futs = {
            exe.submit(fetch_series, sid): name
            for name, sid, _ in SERIES
        }
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                series_data[name] = fut.result()
                print(f"  ✓ {name}: {len(series_data[name])} obs")
            except Exception as e:
                print(f"  ✗ {name}: {e}", file=sys.stderr)
                series_data[name] = []

    # Required check
    for req in REQUIRED:
        if not series_data.get(req):
            print(f"ABORT: required series '{req}' empty. Keeping existing fx_data.js.", file=sys.stderr)
            sys.exit(1)

    # Build payload
    metadata = {name: {"id": sid, "label": label} for name, sid, label in SERIES}
    payload = {
        "series": series_data,
        "metadata": metadata,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }

    js_content = "window.FX_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(js_content)

    size_kb = len(js_content) / 1024
    print(f"Wrote {OUTPUT_PATH} ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
