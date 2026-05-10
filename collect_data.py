#!/usr/bin/env python3
"""
FX Dashboard Data Collector — Frankfurter (ECB) primary + FRED fallback
- DXY: FRED DTWEXBGS (Broad USD Index, 미국 정부)
- USD/KRW·JPY·CNY·CHF·EUR/USD·GBP/USD: Frankfurter v1 API 시도 → 실패 시 FRED DEX* fallback

Output: fx_data.js (window.FX_DATA = {...})
Env: FRED_API_KEY (필수)
"""

import json, os, sys, time, traceback
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


def fetch_frankfurter(symbols, days=1095):
    """Frankfurter v1 API → {sym: [{date, value}]} desc."""
    end_date = datetime.utcnow().strftime("%Y-%m-%d")
    start_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    # 두 도메인 fallback (dev 우선, app 폴백)
    urls = [
        f"https://api.frankfurter.dev/v1/{start_date}..{end_date}?base=USD&symbols={','.join(symbols)}",
        f"https://api.frankfurter.app/{start_date}..{end_date}?from=USD&to={','.join(symbols)}",
    ]
    last_err = None
    for url in urls:
        try:
            print(f"  Try: {url[:80]}...", file=sys.stderr)
            req = urllib.request.Request(url, headers={"User-Agent": "fx-dashboard/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            print(f"  Got rates dict with {len(data.get('rates', {}))} dates", file=sys.stderr)
            result = {sym: [] for sym in symbols}
            for date_str, rates in data.get("rates", {}).items():
                for sym, rate in (rates.items() if isinstance(rates, dict) else []):
                    if rate is not None:
                        try:
                            result[sym].append({"date": date_str, "value": float(rate)})
                        except (ValueError, TypeError):
                            pass
            for sym in result:
                result[sym].sort(key=lambda o: o["date"], reverse=True)
                result[sym] = result[sym][:OBSERVATION_LIMIT]
            # 비어있으면 실패 처리 (다음 url 시도)
            if not any(result[sym] for sym in symbols):
                print(f"  All symbols empty — try next URL", file=sys.stderr)
                continue
            return result
        except Exception as e:
            last_err = e
            print(f"  URL failed: {type(e).__name__}: {e}", file=sys.stderr)
    raise RuntimeError(f"Frankfurter both URLs failed: {last_err}")


def main():
    if not FRED_API_KEY:
        print("ERROR: FRED_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    series_data = {}
    metadata = {}
    used_source = {}  # 어떤 source 가 실제 사용됐는지

    # DXY: FRED only
    print("[DXY] Fetching FRED DTWEXBGS...")
    try:
        series_data["DXY"] = fetch_fred("DTWEXBGS")
        used_source["DXY"] = "FRED"
        print(f"  ✓ DXY: {len(series_data['DXY'])} obs")
    except Exception as e:
        print(f"  ✗ DXY FRED 실패: {e}", file=sys.stderr)
        series_data["DXY"] = []
        used_source["DXY"] = "FAIL"

    # 6 환율: Frankfurter 우선, 실패 시 FRED
    fr_symbols = ["KRW", "JPY", "CNY", "CHF", "EUR", "GBP"]
    fr_data = None
    print(f"\n[6 currencies] Trying Frankfurter (ECB ref rate)...")
    try:
        fr_data = fetch_frankfurter(fr_symbols)
        print(f"  ✓ Frankfurter 성공")
    except Exception as e:
        print(f"  ✗ Frankfurter 실패: {e}", file=sys.stderr)
        traceback.print_exc()
        print(f"  → FRED DEX* fallback 사용", file=sys.stderr)

    # 매핑 — Frankfurter 사용 가능하면 그것, 아니면 FRED
    fr_to_us = {
        "USD_KRW": ("KRW", "DEXKOUS",  False),  # USD per X (Frankfurter base=USD natural)
        "USD_JPY": ("JPY", "DEXJPUS",  False),
        "USD_CNY": ("CNY", "DEXCHUS",  False),
        "USD_CHF": ("CHF", "DEXSZUS",  False),
        "EUR_USD": ("EUR", "DEXUSEU",  True),   # Frankfurter 응답 reverse 필요
        "GBP_USD": ("GBP", "DEXUSUK",  True),
    }

    for our_key, (fr_sym, fred_id, reverse) in fr_to_us.items():
        if fr_data and fr_data.get(fr_sym):
            obs = fr_data[fr_sym]
            if reverse:
                obs = [{"date": o["date"], "value": 1.0/o["value"]} for o in obs if o["value"]]
            series_data[our_key] = obs
            used_source[our_key] = "Frankfurter (ECB)"
            print(f"  ✓ {our_key}: {len(obs)} obs (Frankfurter)")
        else:
            try:
                series_data[our_key] = fetch_fred(fred_id)
                used_source[our_key] = "FRED (fallback)"
                print(f"  ↻ {our_key}: {len(series_data[our_key])} obs (FRED fallback)")
            except Exception as e:
                print(f"  ✗ {our_key} FRED 도 실패: {e}", file=sys.stderr)
                series_data[our_key] = []
                used_source[our_key] = "FAIL"

    # Required check
    if not series_data.get("DXY") or not series_data.get("USD_KRW"):
        print("ABORT: required series (DXY, USD_KRW) empty.", file=sys.stderr)
        sys.exit(1)

    metadata = {
        "DXY":     {"id": "DTWEXBGS", "label": "Broad USD Index", "source": used_source["DXY"]},
        "USD_KRW": {"id": "USD/KRW",  "label": "USD/KRW",         "source": used_source["USD_KRW"]},
        "USD_JPY": {"id": "USD/JPY",  "label": "USD/JPY",         "source": used_source["USD_JPY"]},
        "USD_CNY": {"id": "USD/CNY",  "label": "USD/CNY",         "source": used_source["USD_CNY"]},
        "USD_CHF": {"id": "USD/CHF",  "label": "USD/CHF",         "source": used_source["USD_CHF"]},
        "EUR_USD": {"id": "EUR/USD",  "label": "EUR/USD",         "source": used_source["EUR_USD"]},
        "GBP_USD": {"id": "GBP/USD",  "label": "GBP/USD",         "source": used_source["GBP_USD"]},
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
    print(f"Sources used: {used_source}")


if __name__ == "__main__":
    main()
