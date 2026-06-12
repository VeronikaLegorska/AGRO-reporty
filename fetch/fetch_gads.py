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

    yt_metrics = {}

    def yt_upsert(key, **kwargs):
        if key not in yt_metrics:
            yt_metrics[key] = {}
        for k, v in kwargs.items():
            yt_metrics[key][k] = yt_metrics[key].get(k, 0) + v

    # A) quartile rates z ad_group (ověřeně funguje)
    try:
        q_query = f"""
            SELECT campaign.id, segments.date,
                metrics.video_quartile_p25_rate, metrics.video_quartile_p50_rate,
                metrics.video_quartile_p75_rate, metrics.video_quartile_p100_rate
            FROM ad_group
            WHERE campaign.advertising_channel_type = 'VIDEO'
              AND campaign.name LIKE '%VL%'
              AND campaign.status != 'REMOVED'
              AND segments.date BETWEEN '{date_from}' AND '{date_to}'
            ORDER BY segments.date
        """
        for row in service.search(customer_id=CUSTOMER_ID, query=q_query):
            key = (str(row.campaign.id), row.segments.date)
            yt_upsert(key,
                q25_sum  = (row.metrics.video_quartile_p25_rate  or 0) * 100,
                q50_sum  = (row.metrics.video_quartile_p50_rate  or 0) * 100,
                q75_sum  = (row.metrics.video_quartile_p75_rate  or 0) * 100,
                q100_sum = (row.metrics.video_quartile_p100_rate or 0) * 100,
                q_cnt    = 1,
            )
        print(f"  YT quartile rates: OK ({len(yt_metrics)} rows)")
    except Exception as e:
        print(f"  YT quartile rates: FAIL – {str(e)[:100]}")

    # B) video_views + view_rate + cpv z campaign resource (VIDEO filter)
    try:
        v_query = f"""
            SELECT campaign.id, segments.date,
                metrics.video_views, metrics.video_view_rate, metrics.average_cpv
            FROM campaign
            WHERE campaign.advertising_channel_type = 'VIDEO'
              AND campaign.name LIKE '%VL%'
              AND campaign.status != 'REMOVED'
              AND segments.date BETWEEN '{date_from}' AND '{date_to}'
            ORDER BY segments.date
        """
        for row in service.search(customer_id=CUSTOMER_ID, query=v_query):
            key = (str(row.campaign.id), row.segments.date)
            views = row.metrics.video_views or 0
            yt_upsert(key,
                video_views = views,
                vr_sum      = (row.metrics.video_view_rate or 0) * 100,
                vr_cnt      = 1 if row.metrics.video_view_rate else 0,
                cpv_cost    = row.metrics.cost_micros or 0,
            )
        print(f"  YT video_views/view_rate/cpv: OK")
    except Exception as e:
        print(f"  YT video_views/view_rate/cpv: FAIL – {str(e)[:100]}")

    # C) active_view_impressions + avg frequency z campaign resource
    try:
        f_query = f"""
            SELECT campaign.id, segments.date,
                metrics.active_view_impressions,
                metrics.average_impression_frequency_per_user
            FROM campaign
            WHERE campaign.advertising_channel_type = 'VIDEO'
              AND campaign.name LIKE '%VL%'
              AND campaign.status != 'REMOVED'
              AND segments.date BETWEEN '{date_from}' AND '{date_to}'
            ORDER BY segments.date
        """
        for row in service.search(customer_id=CUSTOMER_ID, query=f_query):
            key = (str(row.campaign.id), row.segments.date)
            freq = getattr(row.metrics, "average_impression_frequency_per_user", 0) or 0
            yt_upsert(key,
                active_view_impr = getattr(row.metrics, "active_view_impressions", 0) or 0,
                freq_sum         = freq,
                freq_cnt         = 1 if freq else 0,
            )
        print(f"  YT active_view/frequency: OK")
    except Exception as e:
        print(f"  YT active_view/frequency: FAIL – {str(e)[:100]}")

    # Finalizace
    for v in yt_metrics.values():
        cnt  = max(v.get("q_cnt", 1), 1)
        v["q25"]  = round(v.get("q25_sum",  0) / cnt, 1)
        v["q50"]  = round(v.get("q50_sum",  0) / cnt, 1)
        v["q75"]  = round(v.get("q75_sum",  0) / cnt, 1)
        v["q100"] = round(v.get("q100_sum", 0) / cnt, 1)
        views = v.get("video_views", 0)
        v["video_views"]        = views
        v["view_rate"]          = round(v.get("vr_sum", 0) / max(v.get("vr_cnt",1),1), 2)
        v["average_cpv"]        = round(v.get("cpv_cost",0)/1_000_000/views, 4) if views>0 else 0
        v["active_view_impr"]   = v.get("active_view_impr", 0)
        v["avg_frequency"]      = round(v.get("freq_sum",0) / max(v.get("freq_cnt",1),1), 2)
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
