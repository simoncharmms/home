#!/usr/bin/env python3
"""
Daily cron script: fetch Arabica (KC=F) 30-day price data from Yahoo Finance
and update the chart data embedded in coffee.html via GitHub API.

Price unit: USX cents/lb (Yahoo Finance native)
1 lb ≈ 0.4536 kg → price per kg = (price_cents / 100) * (1 / 0.4536) USD/kg
"""

import json
import sys
import base64
import os
from datetime import datetime, timezone

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
if not GITHUB_TOKEN:
    print("ERROR: GITHUB_TOKEN environment variable not set", file=sys.stderr)
    sys.exit(1)
REPO = "simoncharmms/home"
FILE_PATH = "coffee.html"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/KC%3DF?interval=1d&range=1mo"

def _session():
    """Return a requests.Session with standard headers."""
    if _requests is None:
        raise RuntimeError("requests library not installed — run: pip install requests")
    s = _requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"})
    return s


def fetch_arabica_data():
    """Fetch 30-day Arabica futures data from Yahoo Finance."""
    s = _session()
    resp = s.get(YAHOO_URL, headers={"Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    try:
        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
        meta = result["meta"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(f"Unexpected Yahoo Finance response structure: {e}") from e

    labels = []
    prices = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        labels.append(dt.strftime("%b %-d"))
        # Convert USX cents/lb → USD/kg
        usd_per_kg = (close / 100) / 0.4536
        prices.append(round(usd_per_kg, 2))

    current_price = prices[-1] if prices else 0
    prev_price = prices[-2] if len(prices) > 1 else current_price
    change = round(current_price - prev_price, 2)
    change_pct = round((change / prev_price) * 100, 2) if prev_price else 0
    today = datetime.now(timezone.utc).strftime("%d %b %Y")

    return {
        "labels": labels,
        "prices": prices,
        "current": current_price,
        "change": change,
        "change_pct": change_pct,
        "updated": today
    }

def get_file_from_github():
    """Get current coffee.html content and SHA from GitHub."""
    url = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    s = _session()
    s.headers.update({
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    })
    resp = s.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "content" not in data:
        raise RuntimeError(f"GitHub API error: {data.get('message', data)}")
    content = base64.b64decode(data["content"]).decode("utf-8")
    sha = data["sha"]
    return content, sha

def inject_chart_data(html, arabica):
    """Replace the chart data block in coffee.html with fresh data."""
    labels_json = json.dumps(arabica["labels"])
    prices_json = json.dumps(arabica["prices"])
    current = f'{arabica["current"]:.2f}'
    change = arabica["change"]
    change_pct = arabica["change_pct"]
    arrow = "▲" if change >= 0 else "▼"
    change_cls = "up" if change >= 0 else "down"
    updated = arabica["updated"]

    # Replace the ARABICA_DATA block (we inject a marked section)
    start_marker = "/* @@ARABICA_DATA_START@@ */"
    end_marker = "/* @@ARABICA_DATA_END@@ */"

    new_block = f"""{start_marker}
        // Auto-updated — {updated}
        var _arabicaLabels = {labels_json};
        var _arabicaPrices = {prices_json};
        var _arabicaCurrent = '{current}';
        var _arabicaChange = '{arrow} {abs(change):.2f} ({change_pct:+.2f}%)';
        var _arabicaChangeCls = '{change_cls}';
        var _arabicaUpdated = 'Arabica (KC) futures · USD/kg · Yahoo Finance · Updated {updated}';
        {end_marker}"""

    if start_marker in html and end_marker in html:
        # Replace existing block
        start_idx = html.index(start_marker)
        end_idx = html.index(end_marker) + len(end_marker)
        html = html[:start_idx] + new_block + html[end_idx:]
    else:
        # First run — inject before loadArabicaPrice function
        inject_before = "async function loadArabicaPrice()"
        if inject_before in html:
            html = html.replace(inject_before, new_block + "\n\n    " + inject_before)
        else:
            print("ERROR: Could not find injection point in coffee.html", file=sys.stderr)
            sys.exit(1)

    # Also update the loadArabicaPrice function to use injected data
    old_fn_start = "async function loadArabicaPrice() {"
    new_fn = """async function loadArabicaPrice() {
    // Use pre-fetched real data if available
    if (typeof _arabicaLabels !== 'undefined' && _arabicaLabels.length > 0) {
        document.getElementById('currentPrice').textContent = _arabicaCurrent + ' $/kg';
        const changeEl = document.getElementById('priceChange');
        changeEl.textContent = _arabicaChange;
        changeEl.className = 'chart-change ' + _arabicaChangeCls;
        document.getElementById('chartMeta').textContent = _arabicaUpdated;
        renderArabicaChart(_arabicaLabels, _arabicaPrices);
        return;
    }
    // Fallback to simulated data"""

    if old_fn_start in html and "// Use pre-fetched real data" not in html:
        html = html.replace(old_fn_start, new_fn)

    return html

def push_to_github(content, sha):
    """Push updated coffee.html to GitHub."""
    url = f"https://api.github.com/repos/{REPO}/contents/{FILE_PATH}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s = _session()
    s.headers.update({
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    })
    payload = {
        "message": f"chore(coffee): update Arabica price chart [{today}]",
        "content": base64.b64encode(content.encode("utf-8")).decode("utf-8"),
        "sha": sha,
        "branch": "main"
    }
    resp = s.put(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "commit" not in data:
        raise RuntimeError(f"GitHub push error: {data.get('message', data)}")
    return data["commit"]["sha"]

if __name__ == "__main__":
    print("Fetching Arabica price data...")
    arabica = fetch_arabica_data()
    print(f"  Current: ${arabica['current']:.2f}/kg  Change: {arabica['change']:+.2f}  ({len(arabica['prices'])} data points)")

    print("Fetching coffee.html from GitHub...")
    html, sha = get_file_from_github()
    print(f"  File SHA: {sha[:8]}")

    print("Injecting chart data...")
    updated_html = inject_chart_data(html, arabica)

    print("Pushing to GitHub...")
    commit_sha = push_to_github(updated_html, sha)
    print(f"  Committed: {commit_sha[:8]}")
    print("Done ✓")
