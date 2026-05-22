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

# Objective → (český label, action_key nebo 'reach')
OBJECTIVE_MAP = {
    "OUTCOME_AWARENESS":     ("Dosah (za 1 000 úč.)",   "reach"),
    "OUTCOME_ENGAGEMENT":    ("Zájem o příspěvek",       "post_engagement"),
    "OUTCOME_TRAFFIC":       ("Zobrazení cílové str.",   "landing_page_view"),
    "OUTCOME_LEADS":         ("Lead",                    "offsite_conversion.fb_pixel_lead"),
    "OUTCOME_SALES":         ("Nákup",                   "offsite_conversion.fb_pixel_purchase"),
    "OUTCOME_APP_PROMOTION": ("Instalace aplikace",      "app_install"),
    "BRAND_AWARENESS":       ("Dosah (za 1 000 úč.)",   "reach"),
    "REACH":                 ("Dosah (za 1 000 úč.)",   "reach"),
    "POST_ENGAGEMENT":       ("Zájem o příspěvek",       "post_engagement"),
    "PAGE_LIKES":            ("Zájem o příspěvek",       "like"),
    "LINK_CLICKS":           ("Proklik",                 "link_click"),
    "LANDING_PAGE_VIEWS":    ("Zobrazení cílové str.",   "landing_page_view"),
    "LEAD_GENERATION":       ("Lead",                    "offsite_conversion.fb_pixel_lead"),
    "CONVERSIONS":           ("Nákup",                   "offsite_conversion.fb_pixel_purchase"),
    "VIDEO_VIEWS":           ("Zhlédnutí videa",         "video_view"),
    "MESSAGES":              ("Zprávy",                  "onsite_conversion.messaging_conversation_started_7d"),
}

FALLBACK_PRIORITY = [
    ("post_engagement",                     "Zájem o příspěvek"),
    ("page_engagement",                     "Zájem o příspěvek"),
    ("landing_page_view",                   "Zobrazení cílové str."),
    ("link_click",                          "Proklik"),
    ("offsite_conversion.fb_pixel_lead",    "Lead"),
    ("offsite_conversion.fb_pixel_purchase","Nákup"),
]

def get_result(actions_list, reach, objective):
    label, action_key = OBJECTIVE_MAP.get(objective or "", ("–", None))
    action_map = {a["action_type"]: int(a["value"]) for a in (actions_list or [])}

    if label == "–":
        for key, lbl in FALLBACK_PRIORITY:
            if action_map.get(key, 0) > 0:
                return lbl, action_map[key]
        return ("Dosah (za 1 000 úč.)", reach) if reach > 0 else ("–", 0)

    if action_key == "reach":
        return label, reach

    return label, action_map.get(action_key, 0)


def fetch():
    date_from = date.today().replace(month=1, day=1).isoformat()
    date_to   = (date.today() - timedelta(days=1)).isoformat()
    account   = AdAccount(AD_ACCOUNT_ID)

    # 1. Objectives kampaní
    camp_objectives = {}
    for camp in account.get_campaigns(fields=["id", "name", "objective"]):
        if is_vl(camp.get("name", "")):
            camp_objectives[camp["id"]] = camp.get("objective", "")

    # 2. Reklamní sestavy (adsets) – lehký listing, nikoli insights
    camp_adsets = {}   # campaign_id → [{id, name}]
    for adset in account.get_ad_sets(fields=["id", "name", "campaign_id"]):
        cid = adset.get("campaign_id")
        if cid not in camp_objectives:
            continue
        camp_adsets.setdefault(cid, [])
        # přidat jen pokud ještě není (může se vrátit duplicitně)
        if not any(a["id"] == adset["id"] for a in camp_adsets[cid]):
            camp_adsets[cid].append({"id": adset["id"], "name": adset.get("name", "")})

    # 3. Insights na úrovni kampaně (spolehlivější než adset-level)
    params = {
        "time_range":     {"since": date_from, "until": date_to},
        "time_increment": 1,
        "level":          "campaign",
        "fields": [
            "campaign_id", "campaign_name",
            "impressions", "clicks", "spend", "reach", "actions",
        ],
    }
    insights = account.get_insights(params=params)

    campaigns = {}
    for row in insights:
        camp_name = row.get("campaign_name", "")
        if not is_vl(camp_name):
            continue

        cid       = row.get("campaign_id")
        objective = camp_objectives.get(cid, "")

        if cid not in campaigns:
            campaigns[cid] = {
                "id": cid, "name": camp_name,
                "objective": objective, "result_type": None,
                "daily": [],
            }

        spend = round(float(row.get("spend", 0)), 2)
        reach = int(row.get("reach", 0))
        label, count = get_result(row.get("actions", []), reach, objective)
        is_dosah = label == "Dosah (za 1 000 úč.)"
        cpr = round(spend / count * 1000 if is_dosah else spend / count, 2) if count > 0 else 0

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

    result = []
    for cid, camp in campaigns.items():
        camp["daily"].sort(key=lambda x: x["date"])
        result.append({
            "id":          cid,
            "name":        camp["name"],
            "objective":   camp["objective"],
            "result_type": camp["result_type"] or "–",
            "adsets":      camp_adsets.get(cid, []),
            "daily":       camp["daily"],
        })

    return {
        "updated":   date.today().isoformat(),
        "period":    {"from": date_from, "to": date_to},
        "campaigns": result,
    }


if __name__ == "__main__":
    data = fetch()
    out = os.path.join(os.path.dirname(__file__), "..", "data", "meta.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Meta: {len(data['campaigns'])} kampaní")
