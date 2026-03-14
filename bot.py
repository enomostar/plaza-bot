"""
Plaza (newnewnew.space) → Discord notifier — Enschede only (API-based)
Requires: pip install requests
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import requests

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1482270962945622196/5he_R80obGgeguYS54iJylePo0XLN-EtSGtuDN1U2d537jnVW4Z8i2suL0W4mddedlLV"
)
API_URL = "https://mosaic-plaza-aanbodapi.zig365.nl/api/v1/actueel-aanbod?limit=60&locale=en_GB&page=0&sort=%2BreactionData.aangepasteTotaleHuurprijs"
CHECK_INTERVAL_SECONDS = 30
SEEN_IDS_FILE = "/data/seen_plaza.json"
# ─────────────────────────────────────────────

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "Referer": "https://plaza.newnewnew.space/en/availables-places/living-place",
    "Origin": "https://plaza.newnewnew.space",
}

# Filter to Enschede only (municipality ID 15897, region Overijssel 9, Netherlands 524)
API_PAYLOAD = {
    "filters": {
        "$and": [
            {
                "$and": [
                    {"municipality.id": {"$eq": "15897"}},
                    {"regio.id": {"$eq": "9"}},
                    {"land.id": {"$eq": "524"}},
                ]
            }
        ]
    },
    "hidden-filters": {
        "$and": [
            {"dwellingType.categorie": {"$eq": "woning"}},
            {"rentBuy": {"$eq": "Huur"}},
            {"isExtraAanbod": {"$eq": ""}},
            {"isWoningruil": {"$eq": ""}},
        ]
    },
}


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_seen_ids() -> set:
    os.makedirs(os.path.dirname(SEEN_IDS_FILE), exist_ok=True)
    if os.path.exists(SEEN_IDS_FILE):
        with open(SEEN_IDS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_seen_ids(seen_ids: set) -> None:
    os.makedirs(os.path.dirname(SEEN_IDS_FILE), exist_ok=True)
    with open(SEEN_IDS_FILE, "w") as f:
        json.dump(list(seen_ids), f)


def fetch_listings() -> list[dict]:
    listings = []
    try:
        resp = requests.post(API_URL, headers=HEADERS, json=API_PAYLOAD, timeout=15)
        if resp.status_code != 200:
            print(f"[{now()}] ❌  API returned {resp.status_code}")
            return []

        data = resp.json()
        items = data.get("data", [])
        print(f"[{now()}] 📦  API returned {len(items)} Enschede listings")

        for item in items:
            try:
                listing_id = str(item.get("id", ""))
                if not listing_id:
                    continue

                street   = item.get("street", "")
                number   = item.get("houseNumber", "")
                addition = item.get("houseNumberAddition", "")

                # City
                city = ""
                if isinstance(item.get("city"), dict):
                    city = item["city"].get("name", "")
                else:
                    city = item.get("city", "")

                postal = item.get("postalcode", "")

                title_parts = [p for p in [street, number, addition] if p]
                title = " ".join(title_parts)
                if postal or city:
                    title += f", {postal} {city}".strip()

                # Price
                if item.get("totalRent"):
                    price = f"€{float(item['totalRent']):.2f} /mnd"
                elif item.get("netRent"):
                    price = f"€{float(item['netRent']):.2f} /mnd"
                else:
                    price = "—"

                # Area
                area = f"{item['areaDwelling']} m²" if item.get("areaDwelling") else "—"

                # Type
                prop_type = ""
                if isinstance(item.get("dwellingType"), dict):
                    prop_type = item["dwellingType"].get("name", "") or item["dwellingType"].get("localizedName", "")
                if not prop_type:
                    prop_type = item.get("objectType", "—")

                # Floor
                floor = ""
                if isinstance(item.get("floor"), dict):
                    floor = item["floor"].get("name", "")

                # Image
                img_url = ""
                pics = item.get("pictures", [])
                if pics and isinstance(pics[0], dict):
                    img_url = pics[0].get("url") or pics[0].get("uri") or ""
                if img_url and not img_url.startswith("http"):
                    img_url = "https://plaza.newnewnew.space" + img_url

                # Build listing URL
                cleaned = re.sub(r"[^a-z0-9]", "-", title.lower())
                cleaned = re.sub(r"-+", "-", cleaned).strip("-")
                link = f"https://plaza.newnewnew.space/en/availables-places/living-place/details/{listing_id}"
                if cleaned:
                    link += f"-{cleaned}"

                if not title.strip():
                    continue

                listings.append({
                    "id": listing_id,
                    "title": title,
                    "price": price,
                    "area": area,
                    "type": prop_type,
                    "floor": floor,
                    "location": f"{postal} {city}".strip() or "Enschede",
                    "url": link,
                    "image": img_url,
                })

            except Exception as e:
                print(f"[{now()}] ⚠️  Skipped listing: {e}")
                continue

    except Exception as e:
        print(f"[{now()}] ❌  API error: {e}")

    return listings


def send_discord_notification(listing: dict) -> None:
    fields = [
        {"name": "💶 Price",    "value": listing["price"] or "—",    "inline": True},
        {"name": "📐 Area",     "value": listing["area"] or "—",     "inline": True},
        {"name": "🏷️ Type",    "value": listing["type"] or "—",     "inline": True},
        {"name": "📍 Location", "value": listing["location"] or "—", "inline": True},
    ]
    if listing.get("floor"):
        fields.append({"name": "🏢 Floor", "value": listing["floor"], "inline": True})

    embed = {
        "title": listing["title"][:256],
        "url": listing["url"],
        "color": 0xE67E22,  # orange to distinguish from Roomspot
        "fields": fields,
        "footer": {"text": "Plaza Notifier • Enschede"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    if listing.get("image", "").startswith("http"):
        embed["image"] = {"url": listing["image"]}

    payload = {"content": "🏢 **New listing on Plaza (Enschede)!**", "embeds": [embed]}

    for _ in range(5):
        try:
            resp = requests.post(
                DISCORD_WEBHOOK_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            if resp.status_code == 429:
                retry_after = float(resp.json().get("retry_after", 2))
                print(f"[{now()}] ⏳  Rate limited — waiting {retry_after:.1f}s...")
                time.sleep(retry_after + 0.5)
                continue
            elif resp.status_code == 400:
                print(f"[{now()}] ❌  Discord rejected: {resp.text[:200]}")
                return
            else:
                resp.raise_for_status()
                print(f"[{now()}] ✅  Sent: {listing['title']} | {listing['price']}")
                return
        except requests.RequestException as e:
            print(f"[{now()}] ❌  Webhook error: {e}")
            return


def main() -> None:
    print(f"[{now()}] 🚀  Plaza API notifier started (interval: {CHECK_INTERVAL_SECONDS}s, Enschede only)")
    print(f"[{now()}] 🔗  API: {API_URL}")

    seen_ids = load_seen_ids()

    while True:
        print(f"[{now()}] 🔍  Checking for new listings...")
        listings = fetch_listings()
        new_listings = [l for l in listings if l["id"] not in seen_ids]

        if new_listings:
            print(f"[{now()}] 🆕  {len(new_listings)} new listing(s) found!")
            for listing in new_listings:
                send_discord_notification(listing)
                seen_ids.add(listing["id"])
                time.sleep(1.5)
            save_seen_ids(seen_ids)
        else:
            print(f"[{now()}] ✔️   No new listings ({len(listings)} total).")

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
