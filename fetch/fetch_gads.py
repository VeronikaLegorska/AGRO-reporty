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

    # Dotaz 2: YT metriky z ad_group resource (video_views apod. nejsou v campaign resource)
    query_yt = f"""
        SELECT
            campaign.id,
            segments.date,
            metrics.video_views,
            metrics.average_cpv,
            metrics.video_view_rate,
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

    # Načti YT metriky indexované (campaign_id, date) – agreguj přes ad_group na kampaň
    yt_metrics = {}
    try:
        for row in service.search(customer_id=CUSTOMER_ID, query=query_yt):
            key = (str(row.campaign.id), row.segments.date)
            if key not in yt_metrics:
                yt_metrics[key] = {"video_views": 0, "cpv_cost": 0, "view_rate_sum": 0,
                                   "q25": 0, "q50": 0, "q75": 0, "q100": 0, "count": 0}
            yt_metrics[key]["video_views"] += row.metrics.video_views
            yt_metrics[key]["cpv_cost"]    += row.metrics.cost_micros
            if row.metrics.video_view_rate:
                yt_metrics[key]["view_rate_sum"] += row.metrics.video_view_rate * 100
                yt_metrics[key]["count"] += 1
            yt_metrics[key]["q25"]  += row.metrics.video_quartile_p25_rate * 100 if row.metrics.video_quartile_p25_rate else 0
            yt_metrics[key]["q50"]  += row.metrics.video_quartile_p50_rate * 100 if row.metrics.video_quartile_p50_rate else 0
            yt_metrics[key]["q75"]  += row.metrics.video_quartile_p75_rate * 100 if row.metrics.video_quartile_p75_rate else 0
            yt_metrics[key]["q100"] += row.metrics.video_quartile_p100_rate * 100 if row.metrics.video_quartile_p100_rate else 0
        # Normalizuj průměry
        for k, v in yt_metrics.items():
            cnt = max(v["count"], 1)
            v["view_rate"] = round(v["view_rate_sum"] / cnt, 2)
            v["average_cpv"] = round(v["cpv_cost"] / 1_000_000 / v["video_views"], 4) if v["video_views"] > 0 else 0
            v["q25"]  = round(v["q25"]  / cnt, 1)
            v["q50"]  = round(v["q50"]  / cnt, 1)
            v["q75"]  = round(v["q75"]  / cnt, 1)
            v["q100"] = round(v["q100"] / cnt, 1)
        print(f"  YT metriky: {len(yt_metrics)} záznamů")
    except Exception as e:
        print(f"  YT metriky nedostupné: {e}")

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
