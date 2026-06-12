import json
import os
from datetime import date, timedelta
from google.ads.googleads.client import GoogleAdsClient

CUSTOMER_ID      = os.environ["GADS_CUSTOMER_ID"]
LOGIN_CUSTOMER_ID = os.environ["GADS_LOGIN_CUSTOMER_ID"]

def get_client():
    return GoogleAdsClient.load_from_dict({
        "developer_token":   os.environ["GADS_DEVELOPER_TOKEN"],
        "client_id":         os.environ["GADS_CLIENT_ID"],
        "client_secret":     os.environ["GADS_CLIENT_SECRET"],
        "refresh_token":     os.environ["GADS_REFRESH_TOKEN"],
        "login_customer_id": LOGIN_CUSTOMER_ID,
        "use_proto_plus":    True,
    })

def fetch():
    date_from = date.today().replace(month=1, day=1).isoformat()
    date_to   = (date.today() - timedelta(days=1)).isoformat()

    client  = get_client()
    service = client.get_service("GoogleAdsService")

    # Dotaz 1: všechny kampaně – základní metriky + typ kanálu
    query_all = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.advertising_channel_type,
            segments.date,
            metrics.impressions,
            metrics.clicks,
            metrics.cost_micros
        FROM campaign
        WHERE
            campaign.name LIKE '%VL%'
            AND campaign.name NOT LIKE '%comarketing%'
            AND campaign.name NOT LIKE '%Comarketing%'
            AND campaign.status != 'REMOVED'
            AND segments.date BETWEEN '{date_from}' AND '{date_to}'
        ORDER BY segments.date
    """

    # YT metriky: quartile rates (jediné dostupné přes tuto konfiguraci API)
    # video_views, view_rate, active_view_impressions, avg_frequency jsou omezeny
    # na úrovni developer tokenu – vrací "Unrecognized fields"
    yt_metrics = {}
    query_yt = f"""
        SELECT
            campaign.id,
            segments.date,
            metrics.video_quartile_p25_rate,
            metrics.video_quartile_p50_rate,
            metrics.video_quartile_p75_rate,
            metrics.video_quartile_p100_rate
        FROM ad_group
        WHERE
            campaign.advertising_channel_type = 'VIDEO'
            AND campaign.name LIKE '%VL%'
            AND campaign.status != 'REMOVED'
            AND segments.date BETWEEN '{date_from}' AND '{date_to}'
        ORDER BY segments.date
    """
    try:
        for row in service.search(customer_id=CUSTOMER_ID, query=query_yt):
            key = (str(row.campaign.id), row.segments.date)
            if key not in yt_metrics:
                yt_metrics[key] = {"q25_sum": 0, "q50_sum": 0, "q75_sum": 0, "q100_sum": 0, "cnt": 0}
            for attr, k in [("video_quartile_p25_rate","q25"), ("video_quartile_p50_rate","q50"),
                            ("video_quartile_p75_rate","q75"), ("video_quartile_p100_rate","q100")]:
                val = getattr(row.metrics, attr, 0) or 0
                if val:
                    yt_metrics[key][k + "_sum"] += val * 100
                    yt_metrics[key]["cnt"] += 1
        for v in yt_metrics.values():
            cnt = max(v["cnt"] // 4, 1)  # každý ze 4 quartilů přispívá
            v["q25"]  = round(v["q25_sum"]  / cnt, 1)
            v["q50"]  = round(v["q50_sum"]  / cnt, 1)
            v["q75"]  = round(v["q75_sum"]  / cnt, 1)
            v["q100"] = round(v["q100_sum"] / cnt, 1)
        print(f"  YT quartile rates: {len(yt_metrics)} záznamů")
    except Exception as e:
        print(f"  YT quartile rates nedostupne: {e}")

    campaigns = {}
    for row in service.search(customer_id=CUSTOMER_ID, query=query_all):
        cid  = str(row.campaign.id)
        name = row.campaign.name
        day  = row.segments.date
        channel_type = row.campaign.advertising_channel_type.name
        if cid not in campaigns:
            campaigns[cid] = {"id": cid, "name": name, "channel_type": channel_type, "daily": []}
        day_data = {
            "date":        day,
            "clicks":      row.metrics.clicks,
            "impressions": row.metrics.impressions,
            "spend_czk":   round(row.metrics.cost_micros / 1_000_000, 2),
        }
        yt = yt_metrics.get((cid, day))
        if yt:
            day_data.update(yt)
        campaigns[cid]["daily"].append(day_data)

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
    out = os.path.join(os.path.dirname(__file__), "..", "data", "gads.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Google Ads: {len(data['campaigns'])} kampaní")
