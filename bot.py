"""
Plaza (newnewnew.space) → Discord notifier — Enschede only
Requires: pip install playwright requests && python -m playwright install chromium
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
DISCORD_WEBHOOK_URL = os.environ.get(
    "DISCORD_WEBHOOK_URL",
    "https://discord.com/api/webhooks/1482295315653722215/lMdBztZUhDFhB3TxcpCCAbz4qBcfTLwmg7Qapkaais5_qWMtPF-BWn0GMzuM50JJDpqs"
)
PLAZA_URL = "https://plaza.newnewnew.space/en/availables-places/living-place"
FILTER_CITY = "enschede"           # only notify for listings containing this word
CHECK_INTERVAL_SECONDS = 30
SEEN_IDS_FILE = "/data/seen_plaza.json"
# ─────────────────────────────────────────────


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean(text: str, max_len: int = 256) -> str:
    text = (text or "").strip()
    text = "".join(c for c in text if c.isprintable())
    return text[:max_len] if text else ""


def find_price_in_text(text: str) -> str:
    match = re.search(r"€\s*[\d.,]+(?:\s*/\s*(?:mnd|maand|month|mo))?", text, re.IGNORECASE)
    return match.group(0).strip() if match else ""


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
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()
            page.goto(PLAZA_URL, wait_until="domcontentloaded", timeout=45000)
            time.sleep(4)

            # Dismiss cookie banner if present
            try:
                page.click(
                    "button:has-text('Accept'), button:has-text('Agree'), "
                    "button:has-text('OK'), [id*='cookie'] button, [class*='cookie'] button",
                    timeout=4000,
                )
                print(f"[{now()}] 🍪  Cookie banner dismissed.")
                time.sleep(1)
            except PlaywrightTimeout:
                pass

            # Wait for listing cards
            SELECTORS = [
                "a[href*='/living-place/']",
                "a[href*='/available']",
                "[class*='listing']",
                "[class*='property']",
                "[class*='card']",
                "[class*='place']",
                "article",
            ]

            found_selector = None
            for sel in SELECTORS:
                try:
                    page.wait_for_selector(sel, timeout=8000)
                    found_selector = sel
                    break
                except PlaywrightTimeout:
                    continue

            if not found_selector:
                print(f"[{now()}] ⚠️  No listing cards found — saving debug_plaza.html")
                with open("debug_plaza.html", "w", encoding="utf-8") as f:
                    f.write(page.content())
                browser.close()
                return []

            # Grab all anchor tags that link to individual listings
            all_links = page.query_selector_all("a[href*='/living-place/'], a[href*='/place/']")
            if not all_links:
                all_links = page.query_selector_all("a[href]")

            seen_hrefs: set = set()

            for card in all_links:
                href = card.get_attribute("href") or ""
                if not href or href == PLAZA_URL or href.endswith("/living-place"):
                    continue

                # Build full URL
                if href.startswith("http"):
                    full_url = href
                elif href.startswith("/"):
                    full_url = "https://plaza.newnewnew.space" + href
                else:
                    continue

                # Use the last URL segment as ID
                listing_id = full_url.rstrip("/").split("/")[-1]
                if not listing_id or listing_id in seen_hrefs:
                    continue
                seen_hrefs.add(listing_id)

                full_text = clean(card.inner_text(), 2000)

                # ── City filter — skip if not Enschede ──────────────────
                if FILTER_CITY.lower() not in full_text.lower() and FILTER_CITY.lower() not in full_url.lower():
                    continue

                # ── Title ────────────────────────────────────────────────
                title = ""
                for sel in ["h2", "h3", "h4", "[class*='title']", "[class*='name']",
                            "[class*='street']", "[class*='address']", "strong"]:
                    el = card.query_selector(sel)
                    if el:
                        t = clean(el.inner_text(), 256)
                        if t:
                            title = t
                            break
                if not title:
                    # fallback: parse from URL slug
                    slug = re.sub(r"^\d+-", "", listing_id)
                    title = slug.replace("-", " ").title()

                # ── Price ────────────────────────────────────────────────
                price = ""
                for sel in ["[class*='price']", "[class*='rent']", "[class*='huur']",
                            "[class*='cost']", "[class*='bedrag']", "[class*='prijs']"]:
                    el = card.query_selector(sel)
                    if el:
                        p = clean(el.inner_text(), 100)
                        if p and "€" in p:
                            price = p
                            break
                if not price:
                    price = find_price_in_text(full_text)

                # ── Location ─────────────────────────────────────────────
                location = ""
                for sel in ["[class*='location']", "[class*='city']", "[class*='place']",
                            "[class*='address']", "[class*='stad']"]:
                    el = card.query_selector(sel)
                    if el:
                        loc = clean(el.inner_text(), 100)
                        if loc:
                            location = loc
                            break
                if not location:
                    location = "Enschede"

                # ── Type ─────────────────────────────────────────────────
                prop_type = ""
                for sel in ["[class*='type']", "[class*='kind']", "[class*='category']",
                            "[class*='label']", "[class*='tag']", "[class*='badge']"]:
                    el = card.query_selector(sel)
                    if el:
                        t = clean(el.inner_text(), 60)
                        if t:
                            prop_type = t
                            break
                if not prop_type:
                    type_match = re.search(
                        r"\b(kamer|studio|appartement|apartment|room|flat|house|loft)\b",
                        full_text, re.IGNORECASE
                    )
                    if type_match:
                        prop_type = type_match.group(0).capitalize()

                # ── Image ─────────────────────────────────────────────────
                image = None
                for img_el in card.query_selector_all("img"):
                    src = (
                        img_el.get_attribute("src")
                        or img_el.get_attribute("data-src")
                        or img_el.get_attribute("data-lazy-src")
                    )
                    if src and src.startswith("http") and not src.endswith(".svg"):
                        image = src
                        break

                listings.append({
                    "id": listing_id,
                    "title": title,
                    "price": price,
                    "location": location,
                    "type": prop_type,
                    "url": full_url,
                    "image": image,
                })

            browser.close()
            print(f"[{now()}] 📍  Found {len(listings)} Enschede listing(s) on Plaza.")

    except Exception as e:
        print(f"[{now()}] ❌  Browser error: {e}")

    return listings


def send_discord_notification(listing: dict) -> None:
    title     = clean(listing.get("title", ""), 256) or "—"
    price     = clean(listing.get("price", ""), 100) or "—"
    location  = clean(listing.get("location", ""), 100) or "—"
    prop_type = clean(listing.get("type", ""), 60) or "—"
    url       = (listing.get("url") or "").strip()
    if not url.startswith("http"):
        url = "https://plaza.newnewnew.space"

    embed = {
        "title": title,
        "url": url,
        "color": 0xE67E22,   # orange to distinguish from Roomspot
        "fields": [
            {"name": "💶 Price",    "value": price,     "inline": True},
            {"name": "📍 Location", "value": location,  "inline": True},
            {"name": "🏷️ Type",    "value": prop_type, "inline": True},
        ],
        "footer": {"text": "Plaza Notifier • Enschede"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    image_url = listing.get("image") or ""
    if image_url.startswith("http"):
        embed["image"] = {"url": image_url}

    payload = {"content": "🏠 **New listing on Plaza (Enschede)!**", "embeds": [embed]}

    for attempt in range(5):
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
                print(f"[{now()}] ✅  Sent: {title} | {price} | {location}")
                return
        except requests.RequestException as e:
            print(f"[{now()}] ❌  Webhook error: {e}")
            return

    print(f"[{now()}] ❌  Gave up after 5 retries: {title}")


def main() -> None:
    print(f"[{now()}] 🚀  Plaza notifier started (interval: {CHECK_INTERVAL_SECONDS}s, city: {FILTER_CITY})")
    print(f"[{now()}] 🔗  URL: {PLAZA_URL}")

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
