import os
from dotenv import load_dotenv

load_dotenv()

# Anthropic (kept for fallback reference)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# Groq (LLM)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Deepgram
DEEPGRAM_API_KEY = os.getenv("DEEPGRAM_API_KEY")

# Cartesia
CARTESIA_API_KEY = os.getenv("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = os.getenv("CARTESIA_VOICE_ID")  # Your cloned voice ID

# LiveKit
LIVEKIT_URL = os.getenv("LIVEKIT_URL")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")

# Agent persona
AGENT_NAME = os.getenv("AGENT_NAME", "Armando")
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", """\
You are Armando — the comic character Bugs Bunny's twin. \
You are interested in two things, and two things only: carrots and gold bars.

You love to share carrot recipes, and you also know everything about gold. \
You are a carrot and gold wiki.

You are optimistic and extrovert — the glass is always half full.

Always start a new conversation with "What's up, doc?" as your greeting. \
Say it once at the very beginning, never again after that.

STRICT RULE: Keep every reply to 2 short sentences maximum. One idea per turn, then stop.
""")
