import json
import os
import time
from datetime import date, timedelta
from facebook_business.api import FacebookAdsApi
from facebook_business.adobjects.adaccount import AdAccount
from facebook_business.exceptions import FacebookRequestError

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


# Větší stránky = méně volání API (výchozích 25 řádků vyčerpá limit požadavků)
PAGE = {"limit": 500}


def month_ranges(date_from, date_to):
    """[(od, do)] po kalendářních měsících v rozsahu date_from–date_to (ISO stringy)."""
    d, end, out = date.fromisoformat(date_from), date.fromisoformat(date_to), []
    while d <= end:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        out.append((d.isoformat(), min(nxt - timedelta(days=1), end).isoformat()))
        d = nxt
    return out


def insights_with_retry(account, params, attempts=4):
    """Stáhne všechny řádky insights; při dočasné chybě Meta API to zkusí znovu."""
    for i in range(attempts):
        try:
            return list(account.get_insights(params={**PAGE, **params}))
        except FacebookRequestError as e:
            if i == attempts - 1:
                raise
            print(f"  Meta API chyba ({e.api_error_message()}), zkouším znovu…")
            time.sleep(15 * (i + 1))


# effective_status z Meta → active / paused / ended
def norm_status(effective_status, end_time):
    st = (effective_status or "").upper()
    if end_time and end_time[:10] < date.today().isoformat():
        return "ended"
    if st in ("DELETED", "ARCHIVED"):
        return "ended"
    if "PAUSED" in st:
        return "paused"
    return "active"


def fetch():
    date_from = date.today().replace(month=1, day=1).isoformat()
    date_to   = (date.today() - timedelta(days=1)).isoformat()
    account   = AdAccount(AD_ACCOUNT_ID)

    # 1. Objectives kampaní
    camp_objectives = {}
    camp_status     = {}   # campaign_id → (status, end_date)
    for camp in account.get_campaigns(fields=["id", "name", "objective", "effective_status", "stop_time"], params=PAGE):
        if is_vl(camp.get("name", "")):
            camp_objectives[camp["id"]] = camp.get("objective", "")
            stop = camp.get("stop_time")
            camp_status[camp["id"]] = (norm_status(camp.get("effective_status"), stop), stop[:10] if stop else None)

    # 2. Reklamní sestavy (adsets) – lehký listing, nikoli insights
    camp_adsets = {}   # campaign_id → [{id, name}]
    for adset in account.get_ad_sets(fields=["id", "name", "campaign_id", "effective_status", "end_time"], params=PAGE):
        cid = adset.get("campaign_id")
        if cid not in camp_objectives:
            continue
        camp_adsets.setdefault(cid, [])
        # přidat jen pokud ještě není (může se vrátit duplicitně)
        if not any(a["id"] == adset["id"] for a in camp_adsets[cid]):
            end = adset.get("end_time")
            camp_adsets[cid].append({"id": adset["id"], "name": adset.get("name", ""),
                                     "status": norm_status(adset.get("effective_status"), end),
                                     "end_date": end[:10] if end else None})

    # 3a. Agregovaný reach za celé období (pro správné CPM – bez time_increment)
    #     sum(denní reach) > celkový reach kvůli opakované deduplicaci
    period_reach = {}  # campaign_id → celkový reach za period
    agg_params = {
        "time_range": {"since": date_from, "until": date_to},
        "level":      "campaign",
        "fields":     ["campaign_id", "reach"],
    }
    for row in account.get_insights(params={**PAGE, **agg_params}):
        cid = row.get("campaign_id")
        if camp_objectives.get(cid) in ("OUTCOME_AWARENESS", "BRAND_AWARENESS", "REACH"):
            period_reach[cid] = int(row.get("reach", 0))

    # 3b. Denní insights na úrovni kampaně
    params = {
        "time_range":     {"since": date_from, "until": date_to},
        "time_increment": 1,
        "level":          "campaign",
        "fields": [
            "campaign_id", "campaign_name",
            "impressions", "clicks", "spend", "reach", "actions",
        ],
    }
    insights = account.get_insights(params={**PAGE, **params})

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

    # 4a. Agregovaný reach za celé období na úrovni sestav (pro Dosah sestavy)
    adset_period_reach = {}  # adset_id → celkový reach za period
    agg_params_adset = {
        "time_range": {"since": date_from, "until": date_to},
        "level":      "adset",
        "fields":     ["adset_id", "campaign_id", "reach"],
    }
    for row in account.get_insights(params={**PAGE, **agg_params_adset}):
        if camp_objectives.get(row.get("campaign_id")) in ("OUTCOME_AWARENESS", "BRAND_AWARENESS", "REACH"):
            adset_period_reach[row.get("adset_id")] = int(row.get("reach", 0))

    # 4b. Denní insights na úrovni sestav (= jednotlivé produkty / kampaně v rámci značky)
    #     Po měsících + opakování – celoroční dotaz Meta občas odmítne ("Service temporarily unavailable")
    adset_rows = []
    for m_from, m_to in month_ranges(date_from, date_to):
        adset_rows += insights_with_retry(account, {
            "time_range":     {"since": m_from, "until": m_to},
            "time_increment": 1,
            "level":          "adset",
            "fields": [
                "campaign_id", "adset_id", "adset_name",
                "impressions", "clicks", "spend", "reach", "actions",
            ],
        })
    adset_daily = {}  # adset_id → [daily]
    for row in adset_rows:
        cid = row.get("campaign_id")
        if cid not in camp_objectives:
            continue
        spend = round(float(row.get("spend", 0)), 2)
        reach = int(row.get("reach", 0))
        _, count = get_result(row.get("actions", []), reach, camp_objectives[cid])
        adset_daily.setdefault(row.get("adset_id"), []).append({
            "date":        row.get("date_start"),
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "spend_czk":   spend,
            "results":     count,
        })

    result = []
    for cid, camp in campaigns.items():
        camp["daily"].sort(key=lambda x: x["date"])
        pr = period_reach.get(cid)  # celkový unique reach za celé období (pro správné CPM)
        adsets = []
        for a in camp_adsets.get(cid, []):
            daily = sorted(adset_daily.get(a["id"], []), key=lambda x: x["date"])
            adsets.append({**a, "period_reach": adset_period_reach.get(a["id"]), "daily": daily})
        # Kampaň „běží", jen pokud běží aspoň jedna její sestava (kampaň bývá ACTIVE i po skončení sestav)
        status, end_date = camp_status.get(cid, ("active", None))
        if status == "active":
            sts = {a["status"] for a in adsets}   # bez sestav kampaň běžet nemůže → ended
            status = "active" if "active" in sts else "paused" if "paused" in sts else "ended"
        result.append({
            "id":           cid,
            "name":         camp["name"],
            "status":       status,
            "end_date":     end_date,
            "objective":    camp["objective"],
            "result_type":  camp["result_type"] or "–",
            "period_reach": pr,  # pro Dosah kampaně: správné CPM = spend/period_reach*1000
            "adsets":       adsets,
            "daily":        camp["daily"],
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
