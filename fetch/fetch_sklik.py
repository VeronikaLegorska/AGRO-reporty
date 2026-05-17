import json
import os
import time
import xmlrpc.client
from datetime import date, timedelta, datetime
import calendar

TOKEN    = os.environ["SKLIK_TOKEN_AGRO"]
XMLRPC_URL = "https://api.sklik.cz/drak/RPC2"

def get_proxy_session():
    proxy  = xmlrpc.client.ServerProxy(XMLRPC_URL, encoding="utf-8")
    result = proxy.client.loginByToken(TOKEN)
    if result.get("status") != 200:
        raise RuntimeError(f"Sklik login selhal: {result}")
    return proxy, {"session": result["session"]}

def to_dt(d):
    return xmlrpc.client.DateTime(datetime.strptime(d, "%Y-%m-%d"))

def int_to_date(d):
    # 20260501 → "2026-05-01"
    return f"{d//10000}-{(d//100)%100:02d}-{d%100:02d}"

def is_vl(name):
    n = name.upper()
    return "VL" in n and "COMARKETING" not in n

PAGE_LIMIT = 150  # 150 rows * ~30 fields = 4500, pod limit 5000

def fetch_month(proxy, session, vl_ids, year, month):
    """Stáhne denní statistiky pro jeden měsíc s paginací."""
    last_day = calendar.monthrange(year, month)[1]
    date_from = f"{year}-{month:02d}-01"
    date_to   = f"{year}-{month:02d}-{last_day}"

    yesterday = (date.today() - timedelta(days=1)).isoformat()
    if date_to > yesterday:
        date_to = yesterday
    if date_from > yesterday:
        return {}

    restriction = {
        "dateFrom": to_dt(date_from),
        "dateTo":   to_dt(date_to),
        "ids":      vl_ids,
    }
    display = {"statGranularity": "daily"}

    r = proxy.campaigns.createReport(session, restriction, display)
    if r.get("status") not in (200, 206):
        print(f"  createReport selhal {year}-{month:02d}: {r.get('statusMessage')}")
        return {}

    report_id = r["reportId"]
    all_rows  = []
    offset    = 0

    # Počkáme než bude report připravený
    for _ in range(30):
        time.sleep(2)
        r2 = proxy.campaigns.readReport(session, report_id, {"offset": 0, "limit": PAGE_LIMIT})
        st = r2.get("status")
        if st in (200, 206):
            all_rows.extend(r2.get("report", []))
            offset = PAGE_LIMIT
            break
        if st == 406:
            # Překročen limit — zmenšíme
            print(f"  406 při prvním čtení, zkouším menší limit")
            break
    else:
        return {}

    # Paginace pro zbývající stránky
    while True:
        r2 = proxy.campaigns.readReport(session, report_id, {"offset": offset, "limit": PAGE_LIMIT})
        st = r2.get("status")
        if st not in (200, 206):
            break
        rows = r2.get("report", [])
        if not rows:
            break
        all_rows.extend(rows)
        if len(rows) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT

    return {"report": all_rows}

def fetch():
    today_dt  = date.today()
    date_from = today_dt.replace(month=1, day=1).isoformat()
    date_to   = (today_dt - timedelta(days=1)).isoformat()

    proxy, session = get_proxy_session()

    # Seznam VL kampaní
    camp_list = proxy.campaigns.list(session, {}, {"offset": 0, "limit": 500})
    if camp_list.get("status") != 200:
        raise RuntimeError(f"campaigns.list selhal: {camp_list}")

    vl_campaigns = {
        c["id"]: c["name"]
        for c in camp_list.get("campaigns", [])
        if is_vl(c.get("name", ""))
    }

    if not vl_campaigns:
        print("Sklik: žádné VL kampaně")
        return {"updated": today_dt.isoformat(), "period": {"from": date_from, "to": date_to}, "campaigns": []}

    vl_ids = list(vl_campaigns.keys())
    print(f"  Nalezeno {len(vl_ids)} VL kampaní")

    # Sbírám data měsíc po měsíci
    campaigns = {}  # id → {name, daily: {}}

    for month in range(1, today_dt.month + 1):
        print(f"  Stahuji {today_dt.year}-{month:02d}...", end=" ", flush=True)
        report = fetch_month(proxy, session, vl_ids, today_dt.year, month)
        rows   = report.get("report", [])
        print(f"{len(rows)} kampaní")

        for row in rows:
            cid  = str(row.get("id", ""))
            name = vl_campaigns.get(row.get("id"), cid)
            if cid not in campaigns:
                campaigns[cid] = {"id": cid, "name": name, "daily": {}}
            for stat in row.get("stats", []):
                day        = int_to_date(stat["date"])
                clicks     = int(stat.get("clicks", 0))
                impressions= int(stat.get("impressions", 0))
                spend_czk  = round(float(stat.get("clickMoney", 0)) / 100, 2)
                if day in campaigns[cid]["daily"]:
                    campaigns[cid]["daily"][day]["clicks"]      += clicks
                    campaigns[cid]["daily"][day]["impressions"] += impressions
                    campaigns[cid]["daily"][day]["spend_czk"]   += spend_czk
                else:
                    campaigns[cid]["daily"][day] = {"date": day, "clicks": clicks, "impressions": impressions, "spend_czk": spend_czk}

    # Převést daily dict na seřazený list
    result = []
    for c in campaigns.values():
        daily_list = sorted(c["daily"].values(), key=lambda x: x["date"])
        result.append({"id": c["id"], "name": c["name"], "daily": daily_list})
    result.sort(key=lambda x: sum(d["spend_czk"] for d in x["daily"]), reverse=True)

    return {
        "updated":   today_dt.isoformat(),
        "period":    {"from": date_from, "to": date_to},
        "campaigns": result,
    }

if __name__ == "__main__":
    data = fetch()
    out = os.path.join(os.path.dirname(__file__), "..", "data", "sklik.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    total = sum(sum(d["spend_czk"] for d in c["daily"]) for c in data["campaigns"])
    print(f"Sklik: {len(data['campaigns'])} kampaní, spend {round(total, 2)} Kč")
