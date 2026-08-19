"""
Stáhne AGRO Google Ads A/B kampaně (obsahová síť) na úrovni ad-group i reklam,
aby šlo vyhodnotit variantu A vs B. Výstup: data/gads_ab.json.

Spouští se přes GitHub Actions workflow 'Fetch GAds A/B' (workflow_dispatch),
kde jsou GADS_* uložené jako secrets.
"""
import json
import os
from datetime import date, timedelta
from google.ads.googleads.client import GoogleAdsClient

CUSTOMER_ID       = os.environ["GADS_CUSTOMER_ID"]
LOGIN_CUSTOMER_ID = os.environ["GADS_LOGIN_CUSTOMER_ID"]

DATE_FROM = "2026-01-01"
DATE_TO   = (date.today() - timedelta(days=1)).isoformat()

# A/B kampaně: v názvu 'A/B' nebo 'A+B'
CAMP_FILTER = "(campaign.name LIKE '%A/B%' OR campaign.name LIKE '%A+B%')"


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
    client  = get_client()
    service = client.get_service("GoogleAdsService")

    # 1) ad_group úroveň – denně (pro agregát i timeline "kdy")
    q_group = f"""
        SELECT campaign.id, campaign.name,
               ad_group.id, ad_group.name, ad_group.status,
               segments.date,
               metrics.impressions, metrics.clicks, metrics.cost_micros
        FROM ad_group
        WHERE {CAMP_FILTER}
          AND campaign.status != 'REMOVED'
          AND segments.date BETWEEN '{DATE_FROM}' AND '{DATE_TO}'
        ORDER BY campaign.name, ad_group.name, segments.date
    """

    # 2) ad_group_ad úroveň – souhrn za období (odhalí, jak jsou A/B varianty rozlišené)
    q_ad = f"""
        SELECT campaign.name, ad_group.name,
               ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.ad.type,
               metrics.impressions, metrics.clicks, metrics.cost_micros
        FROM ad_group_ad
        WHERE {CAMP_FILTER}
          AND campaign.status != 'REMOVED'
          AND segments.date BETWEEN '{DATE_FROM}' AND '{DATE_TO}'
    """

    ad_groups = []
    try:
        for row in service.search(customer_id=CUSTOMER_ID, query=q_group):
            ad_groups.append({
                "campaign":   row.campaign.name,
                "campaign_id": str(row.campaign.id),
                "ad_group":   row.ad_group.name,
                "ad_group_id": str(row.ad_group.id),
                "status":     row.ad_group.status.name,
                "date":       row.segments.date,
                "impressions": row.metrics.impressions,
                "clicks":     row.metrics.clicks,
                "spend_czk":  round(row.metrics.cost_micros / 1_000_000, 2),
            })
        print(f"  ad_group řádků: {len(ad_groups)}")
    except Exception as e:
        print(f"  ad_group dotaz FAIL: {str(e)[:200]}")

    ads = []
    try:
        for row in service.search(customer_id=CUSTOMER_ID, query=q_ad):
            ad = row.ad_group_ad.ad
            ads.append({
                "campaign":  row.campaign.name,
                "ad_group":  row.ad_group.name,
                "ad_id":     str(ad.id),
                "ad_name":   ad.name,
                "ad_type":   ad.type_.name,
                "impressions": row.metrics.impressions,
                "clicks":    row.metrics.clicks,
                "spend_czk": round(row.metrics.cost_micros / 1_000_000, 2),
            })
        print(f"  ad_group_ad řádků: {len(ads)}")
    except Exception as e:
        print(f"  ad_group_ad dotaz FAIL: {str(e)[:200]}")

    return {
        "updated": date.today().isoformat(),
        "period":  {"from": DATE_FROM, "to": DATE_TO},
        "customer_id": CUSTOMER_ID,
        "ad_groups": ad_groups,
        "ads": ads,
    }


if __name__ == "__main__":
    data = fetch()
    out = os.path.join(os.path.dirname(__file__), "..", "data", "gads_ab.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Google Ads A/B: {len(data['ad_groups'])} ad_group řádků, {len(data['ads'])} reklam")
