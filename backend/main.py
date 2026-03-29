import asyncio
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from livekit.api import LiveKitAPI, CreateRoomRequest, AccessToken, VideoGrants

import config

app = FastAPI(title="Talk2Me API")

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


@app.post("/join", response_model=JoinResponse)
async def join(req: JoinRequest):
    """
    Creates a LiveKit room and returns a token for both the user
    and kicks off the Pipecat agent in that room.
    """
    if not all([config.LIVEKIT_URL, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET]):
        raise HTTPException(status_code=500, detail="LiveKit credentials not configured.")

    room_name = f"talk2me-{uuid.uuid4().hex[:8]}"

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

    # Spin up the Pipecat agent in the background
    asyncio.create_task(start_agent(room_name))

    return JoinResponse(token=token, url=config.LIVEKIT_URL, room_name=room_name)


async def start_agent(room_name: str):
    """Launches the Pipecat voice pipeline in the given LiveKit room."""
    from agent import run_agent
    await run_agent(room_name)


@app.get("/health")
async def health():
    return {"status": "ok"}
