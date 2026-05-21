import json
import os
from datetime import date, timedelta
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount

AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]

FacebookAdsApi.init(
    app_id=os.environ["META_APP_ID"],
    app_secret=os.environ["META_APP_SECRET"],
    access_token=os.environ["META_ACCESS_TOKEN"],
)

def is_vl(name):
    n = name.upper()
    return "VL" in n and "COMARKETING" not in n

def fetch():
    date_from = date.today().replace(month=1, day=1).isoformat()
    date_to   = (date.today() - timedelta(days=1)).isoformat()

    account = AdAccount(AD_ACCOUNT_ID)
    params = {
        "time_range": {"since": date_from, "until": date_to},
        "time_increment": 1,
        "level": "campaign",
        "fields": ["campaign_id", "campaign_name", "impressions", "clicks", "spend", "reach", "actions"],
    }
    insights = account.get_insights(params=params)

    # Mapování action_type → český label (v pořadí priority)
    RESULT_PRIORITY = [
        ("post_engagement",                    "Zájem o příspěvek"),
        ("page_engagement",                    "Zájem o příspěvek"),
        ("landing_page_view",                  "Zobrazení cílové str."),
        ("link_click",                         "Proklik"),
        ("offsite_conversion.fb_pixel_lead",   "Lead"),
        ("offsite_conversion.fb_pixel_purchase","Nákup"),
    ]

    def get_result(actions_list, reach):
        """Vrátí (label, count). Pro dosah vrátí reach count se speciálním labelem."""
        if actions_list:
            action_map = {a["action_type"]: int(a["value"]) for a in actions_list}
            for key, label in RESULT_PRIORITY:
                if key in action_map:
                    return label, action_map[key]
        if reach > 0:
            return "Dosah (za 1 000 úč.)", reach
        return "–", 0

    campaigns = {}
    for row in insights:
        name = row.get("campaign_name", "")
        if not is_vl(name):
            continue
        cid  = row.get("campaign_id")
        if cid not in campaigns:
            campaigns[cid] = {"id": cid, "name": name, "result_type": None, "daily": []}
        spend = round(float(row.get("spend", 0)), 2)
        reach = int(row.get("reach", 0))
        label, count = get_result(row.get("actions", []), reach)
        # Cena za výsledek — dosah = CPM (spend/reach*1000), ostatní = spend/count
        if label == "Dosah (za 1 000 úč.)" and count > 0:
            cpr = round(spend / count * 1000, 2)
        else:
            cpr = round(spend / count, 2) if count > 0 else 0
        # result_type uložíme na úrovni kampaně (první nenulový label)
        if campaigns[cid]["result_type"] is None and label != "–":
            campaigns[cid]["result_type"] = label
        campaigns[cid]["daily"].append({
            "date":            row.get("date_start"),
            "clicks":          int(row.get("clicks", 0)),
            "impressions":     int(row.get("impressions", 0)),
            "spend_czk":       spend,
            "results":         count,
            "cost_per_result": cpr,
        })

    result = list(campaigns.values())
    for c in result:
        c["daily"].sort(key=lambda x: x["date"])

    return {
        "updated": date.today().isoformat(),
        "period":  {"from": date_from, "to": date_to},
        "campaigns": result,
    }

if __name__ == "__main__":
    data = fetch()
    out = os.path.join(os.path.dirname(__file__), "..", "data", "meta.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Meta: {len(data['campaigns'])} kampaní")
