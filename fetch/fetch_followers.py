"""
Fetch FB page fans + IG followers for Hrdivé zahrady.

Potřebné GitHub Secrets:
  META_PAGE_ACCESS_TOKEN  – Page Access Token s oprávněními:
                            pages_read_engagement, instagram_basic
  META_PAGE_ID            – ID Facebook stránky (číslo)
  META_IG_USER_ID         – ID Instagram Business Account (číslo)
"""

import os
import json
import requests
from datetime import date, timedelta, datetime

PAGE_TOKEN  = os.environ["META_PAGE_ACCESS_TOKEN"]
PAGE_ID     = os.environ["META_PAGE_ID"]
IG_USER_ID  = os.environ.get("META_IG_USER_ID", "")

DATA_FILE = "data/followers.json"

# page_fans funguje jen do v17; IG insights funguje v21+
API_VER_FB = "v17.0"
API_VER_IG = "v21.0"

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
    """Převod date na Unix timestamp (Meta API vyžaduje timestamp, ne string)."""
    return int(datetime(d.year, d.month, d.day).timestamp())


def fetch_page_fans_history(since_days=90):
    """Fetch daily page_fans metric z FB Insights (API v17 – v21+ tuto metriku nepodporuje)."""
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=since_days)
    url = (
        f"https://graph.facebook.com/{API_VER_FB}/{PAGE_ID}/insights"
        f"?metric=page_fans&period=day"
        f"&since={to_ts(start)}&until={to_ts(end)}"
        f"&access_token={PAGE_TOKEN}"
    )
    records = []
    while url:
        r = requests.get(url, timeout=30)
        if r.status_code != 200:
            print(f"  FB Insights error {r.status_code}: {r.text[:400]}")
            break
        data = r.json()
        if "error" in data:
            print(f"  FB Insights API error: {data['error']}")
            break
        for item in data.get("data", []):
            if item.get("name") == "page_fans":
                for val in item.get("values", []):
                    d = val["end_time"][:10]
                    records.append({"date": d, "count": int(val["value"])})
        url = data.get("paging", {}).get("next")
    return records


def fetch_ig_followers_today():
    """Fetch aktuální počet IG followers."""
    if not IG_USER_ID:
        return None
    url = (
        f"https://graph.facebook.com/{API_VER_IG}/{IG_USER_ID}"
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


def fetch_ig_followers_history(since_days=90):
    """Fetch daily IG followers via IG Insights. Max 30 dní na dotaz → loop po 30denních blocích."""
    if not IG_USER_ID:
        return []
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=since_days)

    records = []
    chunk_end = end
    while chunk_end >= start:
        chunk_start = max(start, chunk_end - timedelta(days=29))
        url = (
            f"https://graph.facebook.com/{API_VER_IG}/{IG_USER_ID}/insights"
            f"?metric=follower_count&period=day"
            f"&since={to_ts(chunk_start)}&until={to_ts(chunk_end)}"
            f"&access_token={PAGE_TOKEN}"
        )
        while url:
            r = requests.get(url, timeout=30)
            if r.status_code != 200:
                print(f"  IG Insights error {r.status_code} [{chunk_start}–{chunk_end}]: {r.text[:300]}")
                url = None
                break
            data = r.json()
            if "error" in data:
                print(f"  IG Insights API error: {data['error']}")
                url = None
                break
            for item in data.get("data", []):
                if item.get("name") == "follower_count":
                    for val in item.get("values", []):
                        d = val["end_time"][:10]
                        records.append({"date": d, "count": int(val["value"])})
            url = data.get("paging", {}).get("next")
        chunk_end = chunk_start - timedelta(days=1)

    return records


# ── main ─────────────────────────────────────────────────────────────────────

existing = load_existing()

# --- Facebook ---
print("Fetching FB page_fans ...")
fb_records = fetch_page_fans_history(since_days=90)
for rec in fb_records:
    upsert(existing["facebook"], rec["date"], rec["count"])
print(f"  → {len(fb_records)} hodnot z FB Insights, celkem {len(existing['facebook'])} v historii")

# --- Instagram ---
print("Fetching IG follower_count ...")
ig_records = fetch_ig_followers_history(since_days=90)
for rec in ig_records:
    upsert(existing["instagram"], rec["date"], rec["count"])

today_str = str(date.today())
ig_today = fetch_ig_followers_today()
if ig_today is not None:
    upsert(existing["instagram"], today_str, ig_today)
    print(f"  → dnešní IG sledující: {ig_today}")

print(f"  → celkem {len(existing['instagram'])} IG záznamů v historii")

# --- Save ---
with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(existing, f, ensure_ascii=False, indent=2)
print("✓ data/followers.json uložen")
