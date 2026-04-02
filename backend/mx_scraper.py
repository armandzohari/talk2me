"""
mx_scraper.py — scrapes upcoming events from mx-tickets.com

Strategy:
  1. Fetch the events page (en-GB = Europe, en-US = Americas)
  2. Parse plain text to extract (date, event_name, @trackslug) tuples
  3. Fetch each unique track page at /t/{slug} and extract lat/lon from JSON-LD
     — track pages are cached in memory so repeated queries are fast
  4. Geocode the requested city via OpenStreetMap Nominatim (free, no key)
  5. Filter events by haversine distance ≤ radius_km
  6. Return a short plain-text summary for reading aloud
"""

import asyncio
import json
import math
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger

# ── In-memory track location cache  ────────────────────────────────────────
# {slug: (lat, lon) | None}  — None means "fetched but no coords found"
_TRACK_CACHE: dict[str, Optional[tuple[float, float]]] = {}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Talk2Me/1.0)",
    "Accept-Language": "en-US,en;q=0.9",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
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
    """Extract lat/lon from the JSON-LD GeoCoordinates block embedded in track pages."""
    match = re.search(r'"geo"\s*:\s*\{[^}]*"latitude"\s*:\s*([\d.\-]+)[^}]*"longitude"\s*:\s*([\d.\-]+)', html)
    if match:
        return float(match.group(1)), float(match.group(2))
    # Try reversed order
    match = re.search(r'"geo"\s*:\s*\{[^}]*"longitude"\s*:\s*([\d.\-]+)[^}]*"latitude"\s*:\s*([\d.\-]+)', html)
    if match:
        return float(match.group(2)), float(match.group(1))
    return None


async def _fetch_track_coords(slug: str, client: httpx.AsyncClient) -> Optional[tuple[float, float]]:
    """Fetch a track page and return (lat, lon) or None. Results are cached."""
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


# ── Parse events page text ──────────────────────────────────────────────────

def _parse_events_page(text: str) -> list[dict]:
    """
    Parse the plain-text content of mx-tickets.com/events.
    Text format per event: `53°Event Name@trackslug`
    Dates appear as: `Thursday, 2 Apr 2026` or `Thursday, Apr 2, 2026`
    Returns list of {date, name, slug}
    """
    events = []
    current_date = ""

    # Date patterns: "Thursday, 2 Apr 2026" or "Thursday, Apr 2, 2026"
    date_re = re.compile(
        r'(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+'
        r'(?:\d{1,2}\s+\w+\s+\d{4}|\w+\s+\d{1,2},?\s+\d{4})'
    )
    # Event pattern: optional temp + event name + @slug
    event_re = re.compile(r'(?:\d+°)([^@\n]+)@([a-z0-9\-]+)')

    for line in re.split(r'(?=(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),)', text):
        date_match = date_re.match(line.strip())
        if date_match:
            current_date = date_match.group(0).strip()

        for ev_match in event_re.finditer(line):
            name = ev_match.group(1).strip()
            slug = ev_match.group(2).strip()
            if name and slug:
                events.append({
                    "date": current_date,
                    "name": name,
                    "slug": slug,
                })

    return events


# ── Main public function ────────────────────────────────────────────────────

async def find_events_near_city(
    city: str,
    region: str = "europe",
    radius_km: int = 200,
    max_results: int = 6,
) -> str:
    locale = "en-GB" if region.lower() in ("europe", "eu") else "en-US"
    url = f"https://mx-tickets.com/{locale}/events"

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:

        # 1. Geocode the city
        city_coords = await _geocode_city(city, client)
        logger.info(f"MX search: city={city} coords={city_coords} region={region}")
        if not city_coords:
            return f"Couldn't geocode '{city}', doc — try a bigger city nearby!"

        # 2. Fetch events page
        try:
            r = await client.get(url, headers=HEADERS)
            page_text = r.text
        except Exception as e:
            logger.error(f"MX events page fetch failed: {e}")
            return "Couldn't reach mx-tickets.com right now, doc — try again in a bit!"

        # 3. Parse events from page text
        # Strip HTML tags first
        clean = re.sub(r'<[^>]+>', ' ', page_text)
        clean = re.sub(r'\s+', ' ', clean)
        events = _parse_events_page(clean)
        logger.info(f"MX scrape: parsed {len(events)} events from page text")

        if not events:
            return f"No events found on mx-tickets.com right now — the site may have changed its layout, doc!"

        # 4. Fetch coords for all unique slugs concurrently
        unique_slugs = list({e["slug"] for e in events if e["slug"] not in _TRACK_CACHE})
        if unique_slugs:
            logger.info(f"MX scrape: fetching coords for {len(unique_slugs)} tracks")
            await asyncio.gather(*[_fetch_track_coords(s, client) for s in unique_slugs])

        # 5. Filter by distance
        city_lat, city_lon = city_coords
        nearby = []
        seen = set()  # deduplicate same track/date combo

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

        nearby.sort(key=lambda e: (e["date"] or "9999", e["dist_km"]))
        nearby = nearby[:max_results]

        if not nearby:
            return (
                f"No upcoming MX events within {radius_km} km of {city}, doc! "
                f"Check mx-tickets.com for the full list."
            )

        lines = [f"Upcoming MX events within {radius_km} km of {city}:"]
        for ev in nearby:
            lines.append(f"  • {ev['date']} — {ev['name']} at @{ev['slug']} (~{ev['dist_km']} km)")
        return "\n".join(lines)
