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

    # YT metriky – každá skupina zvlášť, aby selhal jen minimální počet polí
    yt_metrics = {}

    def yt_query(fields_sql):
        return f"""
            SELECT campaign.id, segments.date, {fields_sql}
            FROM ad_group
            WHERE campaign.advertising_channel_type = 'VIDEO'
              AND campaign.name LIKE '%VL%'
              AND campaign.status != 'REMOVED'
              AND segments.date BETWEEN '{date_from}' AND '{date_to}'
            ORDER BY segments.date
        """

    def yt_init(key):
        if key not in yt_metrics:
            yt_metrics[key] = {}

    # 1) video_views + view_rate + cpv
    try:
        for row in service.search(customer_id=CUSTOMER_ID, query=yt_query(
            "metrics.video_views, metrics.video_view_rate, metrics.average_cpv"
        )):
            key = (str(row.campaign.id), row.segments.date)
            yt_init(key)
            yt_metrics[key].setdefault("video_views", 0)
            yt_metrics[key].setdefault("cost_micros", 0)
            yt_metrics[key]["video_views"] += row.metrics.video_views
            yt_metrics[key]["cost_micros"] += row.metrics.cost_micros
            if row.metrics.video_view_rate:
                yt_metrics[key].setdefault("vr_sum", 0)
                yt_metrics[key].setdefault("vr_cnt", 0)
                yt_metrics[key]["vr_sum"] += row.metrics.video_view_rate * 100
                yt_metrics[key]["vr_cnt"] += 1
        print("  YT: video_views/view_rate/cpv OK")
    except Exception as e:
        print(f"  YT: video_views/view_rate/cpv nedostupne – {str(e)[:120]}")

    # 2) quartile rates
    try:
        for row in service.search(customer_id=CUSTOMER_ID, query=yt_query(
            "metrics.video_quartile_p25_rate, metrics.video_quartile_p50_rate, "
            "metrics.video_quartile_p75_rate, metrics.video_quartile_p100_rate"
        )):
            key = (str(row.campaign.id), row.segments.date)
            yt_init(key)
            for attr, k in [("video_quartile_p25_rate","q25"), ("video_quartile_p50_rate","q50"),
                            ("video_quartile_p75_rate","q75"), ("video_quartile_p100_rate","q100")]:
                val = getattr(row.metrics, attr, 0) or 0
                yt_metrics[key].setdefault(k + "_sum", 0)
                yt_metrics[key].setdefault(k + "_cnt", 0)
                if val:
                    yt_metrics[key][k + "_sum"] += val * 100
                    yt_metrics[key][k + "_cnt"] += 1
        print("  YT: quartile rates OK")
    except Exception as e:
        print(f"  YT: quartile rates nedostupne – {str(e)[:120]}")

    # 3) active_view_impressions + avg frequency
    try:
        for row in service.search(customer_id=CUSTOMER_ID, query=yt_query(
            "metrics.active_view_impressions, metrics.average_impression_frequency_per_user"
        )):
            key = (str(row.campaign.id), row.segments.date)
            yt_init(key)
            yt_metrics[key].setdefault("active_view_impr", 0)
            yt_metrics[key]["active_view_impr"] += getattr(row.metrics, "active_view_impressions", 0) or 0
            freq = getattr(row.metrics, "average_impression_frequency_per_user", 0) or 0
            if freq:
                yt_metrics[key].setdefault("freq_sum", 0)
                yt_metrics[key].setdefault("freq_cnt", 0)
                yt_metrics[key]["freq_sum"] += freq
                yt_metrics[key]["freq_cnt"] += 1
        print("  YT: active_view/frequency OK")
    except Exception as e:
        print(f"  YT: active_view/frequency nedostupne – {str(e)[:120]}")

    # Finalizace – průměry + výsledné hodnoty
    for key, v in yt_metrics.items():
        views = v.get("video_views", 0)
        cost  = v.get("cost_micros", 0)
        v["video_views"] = views
        v["average_cpv"] = round(cost / 1_000_000 / views, 4) if views > 0 else 0
        v["view_rate"]   = round(v.get("vr_sum", 0) / max(v.get("vr_cnt", 1), 1), 2)
        for k in ["q25", "q50", "q75", "q100"]:
            cnt = max(v.get(k + "_cnt", 1), 1)
            v[k] = round(v.get(k + "_sum", 0) / cnt, 1)
        v["active_view_impr"] = v.get("active_view_impr", 0)
        fcnt = max(v.get("freq_cnt", 1), 1)
        v["avg_frequency"] = round(v.get("freq_sum", 0) / fcnt, 2)

    print(f"  YT metriky celkem: {len(yt_metrics)} záznamů")

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
