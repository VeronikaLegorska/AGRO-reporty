"""
Fetch FB page fans + IG followers for Hrdivé zahrady.

Potřebné GitHub Secrets:
  META_PAGE_ACCESS_TOKEN  – Page Access Token s oprávněními:
                            pages_read_engagement, instagram_basic
  META_PAGE_ID            – ID Facebook stránky (číslo)
  META_IG_USER_ID         – ID Instagram Business Account (číslo)

Poznámky k Meta API:
  - FB page_fans (historické denní data) bylo odstraněno z API.
    Řešení: každý den uložíme aktuální fan_count jako snapshot.
  - IG follower_count: API vrací max posledních 30 dní.
"""

import os
import json
import requests
from datetime import date, timedelta, datetime

PAGE_TOKEN  = os.environ["META_PAGE_ACCESS_TOKEN"]
PAGE_ID     = os.environ["META_PAGE_ID"]
IG_USER_ID  = os.environ.get("META_IG_USER_ID", "")

DATA_FILE = "data/followers.json"
API_VER   = "v21.0"

# ── helpers ──────────────────────────────────────────────────────────────────

def load_existing():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"facebook": [], "instagram": []}


def upsert(lst, date_str, count):
    for item in lst:
        if item["date"] == date_str:
            item["count"] = count
            return
    lst.append({"date": date_str, "count": count})
    lst.sort(key=lambda x: x["date"])


def to_ts(d):
    """Převod date na Unix timestamp."""
    return int(datetime(d.year, d.month, d.day).timestamp())


def fetch_page_fan_count_today():
    """Aktuální počet FB fans přes /PAGE_ID?fields=fan_count (historická data API neposkytuje)."""
    url = (
        f"https://graph.facebook.com/{API_VER}/{PAGE_ID}"
        f"?fields=fan_count"
        f"&access_token={PAGE_TOKEN}"
    )
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        print(f"  FB fan_count error {r.status_code}: {r.text[:300]}")
        return None
    data = r.json()
    if "error" in data:
        print(f"  FB fan_count API error: {data['error']}")
        return None
    return data.get("fan_count")


def fetch_ig_followers_today():
    """Fetch aktuální počet IG followers."""
    if not IG_USER_ID:
        return None
    url = (
        f"https://graph.facebook.com/{API_VER}/{IG_USER_ID}"
        f"?fields=followers_count"
        f"&access_token={PAGE_TOKEN}"
    )
    r = requests.get(url, timeout=30)
    if r.status_code != 200:
        print(f"  IG followers_count error {r.status_code}: {r.text[:300]}")
        return None
    data = r.json()
    if "error" in data:
        print(f"  IG followers_count API error: {data['error']}")
        return None
    return data.get("followers_count")


def fetch_ig_followers_history(since_days=28):
    """Fetch daily IG followers via IG Insights. API limit: max posledních 30 dní bez dneška."""
    if not IG_USER_ID:
        return []
    since_days = min(since_days, 28)  # safe margin – API: [today-30, today-1] max
    end   = date.today() - timedelta(days=1)
    start = date.today() - timedelta(days=since_days)  # počítáme od dneška, ne od end
    url = (
        f"https://graph.facebook.com/{API_VER}/{IG_USER_ID}/insights"
        f"?metric=follower_count&period=day"
        f"&since={to_ts(start)}&until={to_ts(end)}"
        f"&access_token={PAGE_TOKEN}"
    )
    records = []
    while url:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            print(f"  IG Insights error {r.status_code}: {r.text[:300]}")
            break
        data = r.json()
        if "error" in data:
            print(f"  IG Insights API error: {data['error']}")
            break
        for item in data.get("data", []):
            if item.get("name") == "follower_count":
                for val in item.get("values", []):
                    d = val["end_time"][:10]
                    records.append({"date": d, "count": int(val["value"])})
        url = data.get("paging", {}).get("next")
    return records


# ── main ─────────────────────────────────────────────────────────────────────

existing = load_existing()
today_str = str(date.today())

# --- Facebook (denní snapshot aktuálního počtu) ---
print("Fetching FB fan_count ...")
fb_today = fetch_page_fan_count_today()
if fb_today is not None:
    upsert(existing["facebook"], today_str, fb_today)
    print(f"  → dnešní FB fans: {fb_today}")
print(f"  → celkem {len(existing['facebook'])} FB záznamů v historii")

# --- Instagram ---
print("Fetching IG follower_count ...")
ig_records = fetch_ig_followers_history(since_days=30)
for rec in ig_records:
    upsert(existing["instagram"], rec["date"], rec["count"])
print(f"  → {len(ig_records)} hodnot z IG Insights (posl. 30 dní)")

ig_today = fetch_ig_followers_today()
if ig_today is not None:
    upsert(existing["instagram"], today_str, ig_today)
    print(f"  → dnešní IG sledující: {ig_today}")

print(f"  → celkem {len(existing['instagram'])} IG záznamů v historii")

# --- Save ---
with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(existing, f, ensure_ascii=False, indent=2)
print("✓ data/followers.json uložen")
