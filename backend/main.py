import asyncio
import subprocess
import sys
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from livekit.api import LiveKitAPI, CreateRoomRequest, AccessToken, VideoGrants
from loguru import logger

import config

app = FastAPI(title="Talk2Me API")


@app.on_event("startup")
async def install_playwright_browsers():
    """
    Ensure Playwright's Chromium binary is present.
    Railway installs the Python package but doesn't always run
    'playwright install' — so we do it here on first boot.
    It's a no-op if the browser is already installed.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode == 0:
            logger.info("Playwright Chromium ready ✓")
        else:
            logger.warning(f"playwright install returned {result.returncode}: {result.stderr[:300]}")
    except Exception as e:
        logger.error(f"playwright install failed at startup: {e}")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class JoinRequest(BaseModel):
    user_name: str = "visitor"


class JoinResponse(BaseModel):
    token: str
    url: str
    room_name: str


def _get_client_ip(request: Request) -> str:
    """Return the real visitor IP, respecting common proxy headers."""
    for header in ("x-forwarded-for", "x-real-ip", "cf-connecting-ip"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _fetch_geolocation(ip: str) -> dict:
    """
    Best-effort geo lookup via ip-api.com (free, no key needed, 45 req/min).
    Returns an empty dict on any failure so it never breaks the call flow.
    """
    if ip in ("unknown", "127.0.0.1", "::1"):
        return {"note": "local/unknown IP — no geo data"}
    try:
        fields = "status,country,countryCode,regionName,city,zip,lat,lon,timezone,isp,org,query"
        async with httpx.AsyncClient(timeout=3.0) as client:
            r = await client.get(f"http://ip-api.com/json/{ip}?fields={fields}")
            data = r.json()
            if data.get("status") == "success":
                data.pop("status", None)
                return data
    except Exception:
        pass
    return {}


def _parse_user_agent(ua_string: str) -> dict:
    """
    Parse browser / OS / device type from the User-Agent string.
    Uses the 'user-agents' library if installed, falls back to raw string.
    """
    try:
        from user_agents import parse as ua_parse
        ua = ua_parse(ua_string)
        return {
            "browser":        f"{ua.browser.family} {ua.browser.version_string}".strip(),
            "os":             f"{ua.os.family} {ua.os.version_string}".strip(),
            "device_family":  ua.device.family,
            "device_brand":   ua.device.brand or "",
            "device_model":   ua.device.model or "",
            "is_mobile":      ua.is_mobile,
            "is_tablet":      ua.is_tablet,
            "is_pc":          ua.is_pc,
            "is_bot":         ua.is_bot,
        }
    except ImportError:
        return {"raw": ua_string}


@app.post("/join", response_model=JoinResponse)
async def join(req: JoinRequest, request: Request):
    """
    Creates a LiveKit room and returns a token for both the user
    and kicks off the Pipecat agent in that room.
    """
    if not all([config.LIVEKIT_URL, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET]):
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured.")

    room_name = f"talk2me-{uuid.uuid4().hex[:8]}"

    # ── Visitor metadata ───────────────────────────────────────────────────
    ip = _get_client_ip(request)
    ua_string = request.headers.get("user-agent", "")
    geo = await _fetch_geolocation(ip)
    ua_info = _parse_user_agent(ua_string)

    visitor_meta = {
        "room_name":   room_name,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "ip":          ip,
        "geo":         geo,
        "user_agent":  ua_string,
        "parsed_ua":   ua_info,
        "referrer":    request.headers.get("referer", ""),
        "accept_lang": request.headers.get("accept-language", ""),
    }

    # Create the room via LiveKit server API
    lk_api = LiveKitAPI(
        url=config.LIVEKIT_URL,
        api_key=config.LIVEKIT_API_KEY,
        api_secret=config.LIVEKIT_API_SECRET,
    )
    await lk_api.room.create_room(CreateRoomRequest(name=room_name, empty_timeout=300))
    await lk_api.aclose()

    # Mint a token for the human user
    token = (
        AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity(req.user_name)
        .with_name(req.user_name)
        .with_grants(VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )

    # Spin up the Pipecat agent in the background, passing visitor metadata
    asyncio.create_task(start_agent(room_name, visitor_meta))

    return JoinResponse(token=token, url=config.LIVEKIT_URL, room_name=room_name)


async def start_agent(room_name: str, visitor_meta: dict | None = None):
    """Launches the Pipecat voice pipeline in the given LiveKit room."""
    from agent import run_agent
    await run_agent(room_name, visitor_meta=visitor_meta or {})


@app.get("/health")
async def health():
    return {"status": "ok"}
