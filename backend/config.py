import os
from dotenv import load_dotenv

load_dotenv()

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Deepgram
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

# ElevenLabs
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")  # Your cloned voice ID

# LiveKit
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# Agent persona (will be fleshed out later)
AGENT_NAME = os.getenv("AGENT_NAME", "Armand")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", f"You are {AGENT_NAME}. Be conversational, warm, and concise.")
