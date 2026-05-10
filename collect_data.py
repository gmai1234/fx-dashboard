#!/usr/bin/env python3
"""
FX Dashboard Data Collector — Frankfurter (ECB) + FRED hybrid
- DXY: FRED DTWEXBGS (Broad USD Index, 미국 정부)
- USD/KRW·JPY·CNY·CHF·EUR/USD·GBP/USD: Frankfurter v1 API (ECB reference rate, 매일 갱신)

Output: fx_data.js (window.FX_DATA = {...})
Env: FRED_API_KEY (DXY 만 필요)
"""

import json, os, sys, time
import urllib.request, urllib.parse, urllib.error
from datetime import datetime, timezone, timedelta

FRED_API_KEY = os.environ.get("FRED_API_KEY", "").strip()
OBSERVATION_LIMIT = 800
OUTPUT_PATH = "fx_data.js"


def fetch_fred(series_id, max_retries=4):
    url_base = "https://api.stlouisfed.org/fred/series/observations"
    params = {"series_id": series_id, "api_key": FRED_API_KEY, "file_type": "json",
              "limit": OBSERVATION_LIMIT, "sort_order": "desc"}
    url = url_base + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = json.loads(resp.read().decode())
                clean = []
                for o in data.get("observations", []):
                    v = o.get("value", "")
                    if v in (".", "", None): continue
                    try: clean.append({"date": o["date"], "value": float(v)})
                    except (ValueError, TypeError): continue
                return clean
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"FRED({series_id}) failed: {last_err}")


def fetch_frankfurter(symbols, days=1095, max_retries=3):
    """Frankfurter v1 API: ECB reference rate, USD base, historical range."""
    end_date = datetime.utcnow().strftime("%Y-%m-%d")
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    url = (f"https://api.frankfurter.dev/v1/{start_date}..{end_date}"
           f"?base=USD&symbols={','.join(symbols)}")
    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            # 응답: {amount, base, start_date, end_date, rates: {date: {sym: rate}}}
            # 변환: {sym: [{date, value}]} desc by date
            result = {sym: [] for sym in symbols}
            for date_str, rates in data.get("rates", {}).items():
                for sym, rate in rates.items():
                    if rate is not None:
                        result[sym].append({"date": date_str, "value": float(rate)})
            for sym in result:
                result[sym].sort(key=lambda o: o["date"], reverse=True)
                result[sym] = result[sym][:OBSERVATION_LIMIT]
            return result
        except Exception as e:
            last_err = e
            print(f"  Frankfurter retry {attempt+1}: {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Frankfurter failed: {last_err}")


def main():
    if not FRED_API_KEY:
        print("ERROR: FRED_API_KEY not set (DXY fetch 용 필요)", file=sys.stderr)
        sys.exit(1)

    series_data = {}

    # DXY: FRED (미국 정부 공식 broad USD index)
    print("[1/2] Fetching DXY from FRED (DTWEXBGS)...")
    try:
        series_data["DXY"] = fetch_fred("DTWEXBGS")
        print(f"  ✓ DXY: {len(series_data['DXY'])} obs (latest: {series_data['DXY'][0]['date'] if series_data['DXY'] else 'N/A'})")
    except Exception as e:
        print(f"  ✗ DXY FRED 실패: {e}", file=sys.stderr)
        series_data["DXY"] = []

    # 6 환율: Frankfurter (ECB reference, 매일 갱신)
    print("[2/2] Fetching 6 currencies from Frankfurter (ECB)...")
    try:
        fr = fetch_frankfurter(["KRW", "JPY", "CNY", "CHF", "EUR", "GBP"])
        # KRW, JPY, CNY, CHF: USD base 라 자연 USD/X
        series_data["USD_KRW"] = fr.get("KRW", [])
        series_data["USD_JPY"] = fr.get("JPY", [])
        series_data["USD_CNY"] = fr.get("CNY", [])
        series_data["USD_CHF"] = fr.get("CHF", [])
        # EUR, GBP: 응답이 1 USD = X EUR/GBP → EUR/USD = 1/value
        series_data["EUR_USD"] = [
            {"date": o["date"], "value": 1.0/o["value"]}
            for o in fr.get("EUR", []) if o["value"]
        ]
        series_data["GBP_USD"] = [
            {"date": o["date"], "value": 1.0/o["value"]}
            for o in fr.get("GBP", []) if o["value"]
        ]
        for k in ["USD_KRW", "USD_JPY", "USD_CNY", "USD_CHF", "EUR_USD", "GBP_USD"]:
            n = len(series_data.get(k, []))
            latest = series_data[k][0]['date'] if series_data.get(k) else 'N/A'
            print(f"  ✓ {k}: {n} obs (latest: {latest})")
    except Exception as e:
        print(f"  ✗ Frankfurter 실패: {e}", file=sys.stderr)
        sys.exit(1)

    # Required check
    if not series_data.get("DXY") or not series_data.get("USD_KRW"):
        print("ABORT: required series (DXY, USD_KRW) empty.", file=sys.stderr)
        sys.exit(1)

    metadata = {
        "DXY":     {"id": "DTWEXBGS",         "label": "Broad USD Index",  "source": "FRED"},
        "USD_KRW": {"id": "frankfurter:KRW",  "label": "USD/KRW",          "source": "Frankfurter (ECB)"},
        "USD_JPY": {"id": "frankfurter:JPY",  "label": "USD/JPY",          "source": "Frankfurter (ECB)"},
        "USD_CNY": {"id": "frankfurter:CNY",  "label": "USD/CNY",          "source": "Frankfurter (ECB)"},
        "USD_CHF": {"id": "frankfurter:CHF",  "label": "USD/CHF",          "source": "Frankfurter (ECB)"},
        "EUR_USD": {"id": "frankfurter:EUR",  "label": "EUR/USD",          "source": "Frankfurter (ECB) [reversed]"},
        "GBP_USD": {"id": "frankfurter:GBP",  "label": "GBP/USD",          "source": "Frankfurter (ECB) [reversed]"},
    }

    payload = {
        "series": series_data,
        "metadata": metadata,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }

    js = "window.FX_DATA = " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + ";\n"
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(js)

    print(f"\nWrote {OUTPUT_PATH} ({len(js)/1024:.1f} KB)")


if __name__ == "__main__":
    main()
