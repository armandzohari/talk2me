"""
mx_scraper.py — scrapes upcoming events from mx-tickets.com

Strategy:
  1. Fetch the Next.js page for the relevant region (en-GB = Europe, en-US = Americas)
  2. Extract __NEXT_DATA__ JSON which contains structured event + track data with coordinates
  3. Geocode the requested city via OpenStreetMap Nominatim (free, no key)
  4. Filter events by distance from the city (within radius_km, default 200 km)
  5. Return a short plain-text summary suitable for reading aloud

Falls back to a keyword match if coordinates are unavailable.
"""

import json
import math
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from loguru import logger

# ── Haversine distance ──────────────────────────────────────────────────────

def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = math.sin(d_lat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


# ── Geocode a city via OSM Nominatim ───────────────────────────────────────

async def _geocode_city(city: str, client: httpx.AsyncClient) -> Optional[tuple[float, float]]:
    """Returns (lat, lon) or None."""
    try:
        r = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": city, "format": "json", "limit": 1},
            headers={"User-Agent": "Talk2Me/1.0 (mx-tickets event finder)"},
            timeout=5.0,
        )
        results = r.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:
        logger.warning(f"Geocode failed for '{city}': {e}")
    return None


# ── Extract events from Next.js __NEXT_DATA__ ─────────────────────────────

def _parse_next_data(html: str) -> Optional[dict]:
    """Pull the __NEXT_DATA__ JSON blob from a Next.js page."""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except Exception:
        return None


def _extract_events_from_next_data(data: dict) -> list[dict]:
    """
    Walk the Next.js page props to find event + track records.
    Returns list of dicts: {date, name, track_name, track_slug, lat, lon}
    """
    events = []
    raw = json.dumps(data)  # flatten for broad search

    # Try common paths for events list
    props = data.get("props", {})
    page_props = props.get("pageProps", {})

    # Look for events array anywhere in pageProps
    for key, val in page_props.items():
        if isinstance(val, list) and len(val) > 0 and isinstance(val[0], dict):
            candidate = val
            for item in candidate:
                ev = _normalise_event(item)
                if ev:
                    events.append(ev)

    # If nothing found via pageProps, try a broader scan
    if not events:
        events = _scan_for_events(data)

    return events


def _normalise_event(item: dict) -> Optional[dict]:
    """Try to extract a standardised event dict from a raw item."""
    # Common field name patterns seen in MX sites
    name = (item.get("title") or item.get("name") or item.get("eventName") or "")
    date_raw = (item.get("date") or item.get("startDate") or item.get("start") or "")
    track = item.get("track") or item.get("club") or {}
    if isinstance(track, str):
        track_name = track
        lat = lon = None
    else:
        track_name = track.get("name") or track.get("title") or ""
        lat = track.get("lat") or track.get("latitude")
        lon = track.get("lon") or track.get("longitude") or track.get("lng")
    slug = item.get("slug") or track.get("slug") if isinstance(track, dict) else None

    if not name and not track_name:
        return None
    return {
        "name":       name or track_name,
        "track_name": track_name,
        "slug":       slug or "",
        "date":       str(date_raw)[:10],
        "lat":        float(lat) if lat is not None else None,
        "lon":        float(lon) if lon is not None else None,
    }


def _scan_for_events(data: dict, depth=0) -> list[dict]:
    """Recursively scan for any list that looks like events."""
    if depth > 6:
        return []
    events = []
    if isinstance(data, dict):
        for v in data.values():
            events.extend(_scan_for_events(v, depth+1))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                ev = _normalise_event(item)
                if ev and (ev["lat"] is not None or ev["date"]):
                    events.append(ev)
                else:
                    events.extend(_scan_for_events(item, depth+1))
    return events


# ── Fallback: parse plain text from the events page ───────────────────────

def _parse_text_events(text: str, city_keyword: str) -> list[str]:
    """
    If __NEXT_DATA__ parsing fails, do a keyword search on the raw page text.
    Looks for lines containing the city name (case-insensitive).
    """
    city_lower = city_keyword.lower()
    matches = []
    lines = text.split("\n")
    for line in lines:
        if city_lower in line.lower() and len(line.strip()) > 10:
            matches.append(line.strip())
    return matches[:10]


# ── Main public function ───────────────────────────────────────────────────

async def find_events_near_city(
    city: str,
    region: str = "europe",
    radius_km: int = 200,
    max_results: int = 5,
) -> str:
    """
    Returns a short plain-text summary of upcoming MX events near `city`.
    `region` should be "europe" or "americas".
    """
    locale = "en-GB" if region.lower() in ("europe", "eu") else "en-US"
    url = f"https://mx-tickets.com/{locale}/events"

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Talk2Me/1.0)",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:

        # 1. Geocode the city
        city_coords = await _geocode_city(city, client)
        logger.info(f"MX search: city={city} coords={city_coords} region={region}")

        # 2. Fetch events page
        try:
            r = await client.get(url, headers=headers)
            html = r.text
        except Exception as e:
            logger.error(f"MX scrape fetch failed: {e}")
            return f"Sorry doc, I couldn't reach mx-tickets.com right now. Try again in a bit!"

        # 3. Try to parse structured Next.js data
        next_data = _parse_next_data(html)
        structured_events = []
        if next_data:
            structured_events = _extract_events_from_next_data(next_data)
            logger.info(f"MX scrape: found {len(structured_events)} structured events")

        # 4. If we have coords + structured events, filter by distance
        if city_coords and structured_events:
            city_lat, city_lon = city_coords
            nearby = []
            for ev in structured_events:
                if ev["lat"] is not None and ev["lon"] is not None:
                    dist = _haversine_km(city_lat, city_lon, ev["lat"], ev["lon"])
                    if dist <= radius_km:
                        ev["dist_km"] = round(dist)
                        nearby.append(ev)
            nearby.sort(key=lambda e: (e.get("date", ""), e.get("dist_km", 9999)))
            nearby = nearby[:max_results]

            if nearby:
                lines = [f"Upcoming MX events within {radius_km} km of {city}:"]
                for ev in nearby:
                    date = ev.get("date", "TBD")
                    name = ev.get("name") or ev.get("track_name")
                    dist = ev.get("dist_km", "?")
                    lines.append(f"  • {date} — {name} (~{dist} km away)")
                return "\n".join(lines)

        # 5. Fallback: keyword search on the raw page text
        # Strip HTML tags for cleaner text
        text_only = re.sub(r"<[^>]+>", " ", html)
        text_only = re.sub(r"\s+", " ", text_only)
        keyword_matches = _parse_text_events(text_only, city)

        if keyword_matches:
            lines = [f"Events mentioning '{city}' on mx-tickets.com:"]
            for m in keyword_matches:
                lines.append(f"  • {m}")
            return "\n".join(lines)

        # 6. Nothing found
        return (
            f"No upcoming MX events found near {city} within {radius_km} km. "
            f"Check mx-tickets.com directly for the full list!"
        )
