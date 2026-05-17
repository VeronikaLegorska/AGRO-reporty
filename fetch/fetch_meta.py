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
        "fields": ["campaign_id", "campaign_name", "impressions", "clicks", "spend"],
    }
    insights = account.get_insights(params=params)

    campaigns = {}
    for row in insights:
        name = row.get("campaign_name", "")
        if not is_vl(name):
            continue
        cid = row.get("campaign_id")
        if cid not in campaigns:
            campaigns[cid] = {"id": cid, "name": name, "daily": []}
        campaigns[cid]["daily"].append({
            "date":        row.get("date_start"),
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "spend_czk":   round(float(row.get("spend", 0)), 2),
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
