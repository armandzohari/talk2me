"""
mx_scraper.py — scrapes upcoming events from mx-tickets.com

Strategy:
  1. Launch headless Chromium via Playwright to render the CSR page
  2. Navigate to /en-GB/events (Europe) or /en-US/events (Americas) based on region
  3. Wait for Firebase to populate the DOM, then extract date headers + event links
  4. Fetch each unique track page at /t/{slug} with httpx to get lat/lon from JSON-LD
     — track pages are cached in memory so repeated queries are fast
  5. Geocode the requested city via OpenStreetMap Nominatim (free, no key)
  6. Filter events by haversine distance ≤ radius_km
  7. Return a short plain-text summary + event URLs for "open the page" feature
"""

import asyncio
import math
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger

# ── In-memory track location cache ──────────────────────────────────────────
_TRACK_CACHE: dict[str, Optional[tuple[float, float]]] = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


async def _geocode_city(city: str, client: httpx.AsyncClient) -> Optional[tuple[float, float]]:
    try:
        r = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": city, "format": "json", "limit": 1},
            headers={"User-Agent": "Talk2Me/1.0 (mx-tickets event finder)"},
            timeout=5.0,
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        logger.warning(f"Geocode failed for '{city}': {e}")
    return None


def _extract_coords_from_page(html: str) -> Optional[tuple[float, float]]:
    """Extract lat/lon from the JSON-LD GeoCoordinates block on track pages."""
    m = re.search(r'"geo"\s*:\s*\{[^}]*"latitude"\s*:\s*([\d.\-]+)[^}]*"longitude"\s*:\s*([\d.\-]+)', html)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r'"geo"\s*:\s*\{[^}]*"longitude"\s*:\s*([\d.\-]+)[^}]*"latitude"\s*:\s*([\d.\-]+)', html)
    if m:
        return float(m.group(2)), float(m.group(1))
    return None


async def _fetch_track_coords(slug: str, client: httpx.AsyncClient) -> Optional[tuple[float, float]]:
    if slug in _TRACK_CACHE:
        return _TRACK_CACHE[slug]
    try:
        r = await client.get(
            f"https://mx-tickets.com/en-GB/t/{slug}",
            headers=HEADERS,
            timeout=6.0,
        )
        coords = _extract_coords_from_page(r.text)
        _TRACK_CACHE[slug] = coords
        return coords
    except Exception as e:
        logger.warning(f"Track fetch failed for '{slug}': {e}")
        _TRACK_CACHE[slug] = None
        return None


# ── Playwright page scrape ────────────────────────────────────────────────────

async def _scrape_events_with_playwright(locale: str) -> list[dict]:
    """
    Launch headless Chromium, load the events page, wait for Firebase to render
    the event list, then extract date/name/slug/url from the DOM.

    Returns list of {date, name, slug, url}.
    """
    from playwright.async_api import async_playwright

    url = f"https://mx-tickets.com/{locale}/events"
    events = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        try:
            page = await browser.new_page(
                user_agent=HEADERS["User-Agent"],
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=20_000)

            # Wait until at least one event link appears (Firebase has populated the DOM)
            await page.wait_for_selector('a[href*="/events/"]', timeout=15_000)

            # Allow a little extra time for more events to stream in
            await asyncio.sleep(2)

            # Extract date headers and event links in DOM order
            events = await page.evaluate("""
                () => {
                    const items = Array.from(document.querySelectorAll(
                        'li.MuiListSubheader-root, a[href*="/events/"]'
                    ));
                    const result = [];
                    let currentDate = '';
                    for (const el of items) {
                        if (el.tagName === 'LI') {
                            currentDate = el.textContent.trim();
                        } else {
                            const text = el.innerText.trim();
                            const href = el.getAttribute('href') || '';
                            const m = href.match(/\\/t\\/([^\\/]+)\\/events\\//);
                            if (m && text) {
                                // Clean name: strip leading "58°" temperature prefix
                                const name = text.replace(/^\\d+°/, '').replace(/@[a-z0-9\\-]+$/i, '').trim();
                                result.push({
                                    date: currentDate,
                                    name: name || text,
                                    slug: m[1],
                                    url: 'https://mx-tickets.com' + href
                                });
                            }
                        }
                    }
                    return result;
                }
            """)

            logger.info(f"Playwright scraped {len(events)} events from {url}")
        except Exception as e:
            logger.error(f"Playwright scrape failed: {e}")
        finally:
            await browser.close()

    return events or []


# ── Main public function ──────────────────────────────────────────────────────

async def find_events_near_city(
    city: str,
    region: str = "europe",
    radius_km: int = 200,
    max_results: int = 6,
) -> str:
    locale = "en-GB" if region.lower() in ("europe", "eu") else "en-US"

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:

        # 1. Geocode the city and scrape events page concurrently
        city_coords_task = asyncio.create_task(_geocode_city(city, client))
        events_task = asyncio.create_task(_scrape_events_with_playwright(locale))

        city_coords = await city_coords_task
        events = await events_task

        logger.info(f"MX search: city={city} coords={city_coords} region={region} events={len(events)}")

        if not city_coords:
            return f"Couldn't geocode '{city}', doc — try a bigger city nearby!"

        if not events:
            return "Couldn't load mx-tickets.com events right now, doc — try again in a bit!"

        # 2. Fetch coords for all unique slugs concurrently
        unique_slugs = [s for s in {e["slug"] for e in events} if s not in _TRACK_CACHE]
        if unique_slugs:
            logger.info(f"MX scrape: fetching coords for {len(unique_slugs)} tracks")
            await asyncio.gather(*[_fetch_track_coords(s, client) for s in unique_slugs])

        # 3. Filter by distance
        city_lat, city_lon = city_coords
        nearby = []
        seen: set[tuple[str, str]] = set()

        for ev in events:
            coords = _TRACK_CACHE.get(ev["slug"])
            if not coords:
                continue
            dist = _haversine_km(city_lat, city_lon, coords[0], coords[1])
            if dist <= radius_km:
                key = (ev["slug"], ev["date"])
                if key not in seen:
                    seen.add(key)
                    nearby.append({**ev, "dist_km": round(dist)})

        nearby.sort(key=lambda e: (e.get("date") or "9999", e["dist_km"]))
        nearby = nearby[:max_results]

        if not nearby:
            return (
                f"No upcoming MX events within {radius_km} km of {city}, doc! "
                f"Check mx-tickets.com for the full list."
            )

        lines = [f"Upcoming MX events within {radius_km} km of {city}:"]
        for ev in nearby:
            lines.append(
                f"  • {ev['date']} — {ev['name']} (~{ev['dist_km']} km) | URL: {ev['url']}"
            )
        return "\n".join(lines)
