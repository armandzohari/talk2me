"""
Pipecat voice pipeline for Talk2Me.

Flow:
  LiveKit mic audio
    → Silero VAD          (detects speech start/end)
    → Deepgram STT        (streaming transcription)
    → Claude LLM          (conversational brain)
    → Cartesia TTS        (your cloned voice)
    → LiveKit speaker audio
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask

from pipecat.transports.services.livekit import LiveKitParams, LiveKitTransport
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from pipecat.services.deepgram import DeepgramSTTService, LiveOptions
from pipecat.services.groq import GroqLLMService
from pipecat.services.cartesia import CartesiaTTSService

from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.frames.frames import LLMMessagesFrame, EndFrame

import config

# ── Log directory ──────────────────────────────────────────────────────────────
LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


# ── Conversation logger ────────────────────────────────────────────────────────

class ConversationLogger:
    """
    Accumulates transcript turns in memory, then writes a human-readable log
    file to LOGS_DIR when flush() is called (at end of session).
    """

    def __init__(self, room_name: str, visitor_meta: dict):
        self._flushed = False
        self.room_name = room_name
        self.visitor_meta = visitor_meta
        self.started_at = datetime.now(timezone.utc)
        self.turns: list[dict] = []   # {"ts": ISO, "speaker": "visitor"|"agent", "text": "..."}

    def add_turn(self, speaker: str, text: str):
        self.turns.append({
            "ts":      datetime.now(timezone.utc).isoformat(),
            "speaker": speaker,
            "text":    text,
        })

    def flush(self):
        """Write the log file. Safe to call more than once (idempotent)."""
        if self._flushed:
            return
        self._flushed = True
        try:
            ended_at  = datetime.now(timezone.utc)
            duration  = int((ended_at - self.started_at).total_seconds())
            mins, sec = divmod(duration, 60)

            # ── Filename: YYYY-MM-DD_HH-MM-SS_<room>.txt ──────────────────
            ts_str   = self.started_at.strftime("%Y-%m-%d_%H-%M-%S")
            filename = LOGS_DIR / f"{ts_str}_{self.room_name}.txt"

            meta = self.visitor_meta
            geo  = meta.get("geo", {})
            pua  = meta.get("parsed_ua", {})

            # ── Geo block ─────────────────────────────────────────────────
            if geo and "country" in geo:
                location = (
                    f"{geo.get('city', '')} {geo.get('regionName', '')} "
                    f"{geo.get('country', '')} ({geo.get('countryCode', '')}) — "
                    f"ISP: {geo.get('isp', '')} — "
                    f"Lat/Lon: {geo.get('lat', '')}/{geo.get('lon', '')}"
                ).strip()
                timezone_str = geo.get("timezone", "")
            else:
                location     = geo.get("note", "unavailable")
                timezone_str = ""

            # ── UA block ──────────────────────────────────────────────────
            if "browser" in pua:
                device_type = "Mobile" if pua.get("is_mobile") else (
                    "Tablet" if pua.get("is_tablet") else "Desktop"
                )
                ua_line = (
                    f"{pua.get('browser', '')} on "
                    f"{pua.get('os', '')}  [{device_type}]"
                    + (f"  {pua.get('device_brand','')} {pua.get('device_model','')}".rstrip()
                       if pua.get("device_brand") else "")
                )
            else:
                ua_line = pua.get("raw", "unknown")

            sep = "=" * 72

            lines = [
                sep,
                "Talk2Me — Conversation Log",
                sep,
                f"Session   : {self.room_name}",
                f"Started   : {self.started_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                f"Ended     : {ended_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
                f"Duration  : {mins}m {sec:02d}s",
                "",
                "VISITOR INFORMATION",
                "-" * 36,
                f"IP Address: {meta.get('ip', 'unknown')}",
                f"Location  : {location}",
                f"Timezone  : {timezone_str}",
                f"Browser   : {ua_line}",
                f"Referrer  : {meta.get('referrer', '') or '(direct)'}",
                f"Language  : {meta.get('accept_lang', '')}",
                f"User-Agent: {meta.get('user_agent', '')}",
                "",
                "CONVERSATION TRANSCRIPT",
                "-" * 36,
            ]

            if self.turns:
                for turn in self.turns:
                    ts      = turn["ts"][11:19]   # HH:MM:SS from ISO
                    speaker = config.AGENT_NAME if turn["speaker"] == "agent" else "Visitor"
                    lines.append(f"[{ts}] {speaker}: {turn['text']}")
            else:
                lines.append("(no conversation recorded)")

            lines += [
                "",
                sep,
                f"End of log — {len(self.turns)} turn(s)",
                sep,
            ]

            content = "\n".join(lines) + "\n"
            filename.write_text(content, encoding="utf-8")
            # Also print to stdout so Railway's log viewer shows the full conversation
            logger.info(f"Conversation log saved → {filename}\n{content}")
            # Append to GitHub file (best-effort)
            github_token = os.environ.get("GITHUB_TOKEN", "")
            if github_token:
                asyncio.get_event_loop().create_task(
                    self._append_to_github(github_token, content)
                )
        except Exception as e:
            logger.error(f"ConversationLogger.flush() failed: {e}")

    async def _append_to_github(self, token: str, new_content: str):
        """
        Appends new_content to conversations.txt in the GitHub repo.
        Uses the GitHub Contents API: GET current file → append → PUT back.
        """
        import httpx, base64

        repo    = "armandzohari/talk2me"
        path    = "conversations.txt"
        api_url = f"https://api.github.com/repos/{repo}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept":        "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                # GET current file (may not exist yet)
                r = await client.get(api_url, headers=headers)
                if r.status_code == 200:
                    data        = r.json()
                    sha         = data["sha"]
                    existing    = base64.b64decode(data["content"]).decode("utf-8")
                    updated     = existing + "\n" + new_content
                    commit_msg  = f"conversation log {self.started_at.strftime('%Y-%m-%d %H:%M UTC')}"
                elif r.status_code == 404:
                    sha         = None
                    updated     = new_content
                    commit_msg  = "init conversation log"
                else:
                    logger.error(f"GitHub GET failed: {r.status_code} {r.text[:200]}")
                    return

                body = {
                    "message": commit_msg,
                    "content": base64.b64encode(updated.encode("utf-8")).decode("utf-8"),
                }
                if sha:
                    body["sha"] = sha

                r2 = await client.put(api_url, headers=headers, json=body)
                if r2.status_code in (200, 201):
                    logger.info(f"Conversation appended to github.com/{repo}/{path} ✓")
                else:
                    logger.error(f"GitHub PUT failed: {r2.status_code} {r2.text[:200]}")
        except Exception as e:
            logger.error(f"GitHub append failed: {e}")


# ── Transcript interceptor ─────────────────────────────────────────────────────
# Replaces context.messages with a smart list that:
#   1. Publishes each new turn to the LiveKit data channel (frontend transcript panel)
#   2. Records each turn in ConversationLogger for the server-side log file

class _TranscriptList(list):
    def __init__(self, initial_messages, transport, conv_logger: ConversationLogger):
        super().__init__(initial_messages)
        self._transport   = transport
        self._conv_logger = conv_logger
        # Skip everything already in the list at creation time (system prompt +
        # greeting trigger). Only newly appended messages get published.
        self._publish_from = len(initial_messages)

    def append(self, message):
        super().append(message)
        idx = len(self) - 1
        if idx < self._publish_from:
            return
        if not isinstance(message, dict):
            return
        role = message.get("role", "")
        if role not in ("user", "assistant"):
            return
        content = message.get("content", "")
        # Anthropic can return content as a list of blocks
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        text = (content or "").strip()
        if text:
            speaker = "visitor" if role == "user" else "agent"
            # Record in server-side log
            self._conv_logger.add_turn(speaker, text)
            logger.debug(f"Transcript: queuing publish for {speaker}: {text[:60]}")
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(self._publish({"speaker": speaker, "text": text}))
            except Exception as e:
                logger.error(f"Transcript: failed to schedule publish task: {e}")

    async def _publish(self, payload):
        """Best-effort — never let a failure here affect the call."""
        try:
            room = self._resolve_room()
            if not room:
                return
            lp = getattr(room, "local_participant", None)
            if not lp:
                logger.error("Transcript: local_participant is None — cannot publish")
                return
            data = json.dumps(payload).encode("utf-8")
            try:
                await lp.publish_data(data, reliable=True, topic="transcript")
                logger.debug(f"Transcript: published {payload}")
            except TypeError:
                from livekit.rtc import DataPacketKind
                await lp.publish_data(data, DataPacketKind.RELIABLE, topic="transcript")
                logger.debug(f"Transcript: published (legacy API) {payload}")
        except Exception as e:
            logger.error(f"Transcript: publish failed: {e}")

    def _resolve_room(self):
        """Walk common Pipecat attribute paths to find the LiveKit Room object."""
        t = self._transport
        candidates = [
            lambda: getattr(t, "_room", None),
            lambda: getattr(getattr(t, "_client", None), "_room", None),
            lambda: getattr(getattr(t, "_output", None), "_room", None),
            lambda: getattr(getattr(t, "_input",  None), "_room", None),
            lambda: getattr(getattr(getattr(t, "_output", None), "_client", None), "_room", None),
            lambda: getattr(getattr(getattr(t, "_input",  None), "_client", None), "_room", None),
        ]
        for fn in candidates:
            try:
                room = fn()
                if room is not None:
                    return room
            except Exception:
                pass
        logger.error(
            "Transcript: could not find room on transport. "
            f"Top-level attrs: {[a for a in dir(t) if not a.startswith('__')]}"
        )
        return None


async def run_agent(room_name: str, visitor_meta: dict | None = None):
    # ── Conversation logger ────────────────────────────────────────────────
    conv_logger = ConversationLogger(room_name, visitor_meta or {})

    # ── Transport (LiveKit WebRTC) ─────────────────────────────────────────
    transport = LiveKitTransport(
        url=config.LIVEKIT_URL,
        token=_mint_agent_token(room_name),
        room_name=room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_audio_passthrough=True,   # REQUIRED: audio must flow to Deepgram even when VAD
                                          # says "not speaking" — without this, AudioRawFrame
                                          # is consumed by the VAD and never reaches DeepgramSTTService
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    stop_secs=0.3,
                    min_volume=0.2,
                    confidence=0.5,
                )
            ),
        ),
    )

    # ── STT: Deepgram (streaming) ──────────────────────────────────────────
    # NOTE: sample_rate MUST be a top-level param here.
    # Pipecat's DeepgramSTTService.start() does:
    #   self._settings["sample_rate"] = self.sample_rate
    # …which OVERWRITES whatever is inside live_options.
    # If sample_rate is only in live_options, self.sample_rate is None → Deepgram
    # receives audio but never transcribes it.
    stt = DeepgramSTTService(
        api_key=config.DEEPGRAM_API_KEY,
        sample_rate=16000,          # top-level — this is what pipecat actually uses
        live_options=LiveOptions(
            model="nova-2-general",
            language="en-US",
            encoding="linear16",
            channels=1,
            smart_format=True,
            interim_results=True,
            punctuate=True,
            endpointing=300,
        ),
    )

    # ── LLM: Groq + Llama (fast, free tier) ──────────────────────────────
    llm = GroqLLMService(
        api_key=config.GROQ_API_KEY,
        model="llama-3.1-8b-instant",
    )

    # Conversation context — system prompt + greeting trigger
    messages = [
        {"role": "system", "content": config.SYSTEM_PROMPT},
        {"role": "user",   "content": "Please greet the visitor. Start with 'What's up, doc?' and briefly introduce yourself as Armando, Bugs Bunny's twin."},
    ]
    context = OpenAILLMContext(messages=messages)
    # Swap in the transcript-aware list — no pipeline changes needed.
    # context.messages is a read-only property; we write to the backing attribute directly.
    context._messages = _TranscriptList(list(context.messages), transport, conv_logger)
    context_aggregator = llm.create_context_aggregator(context)

    # ── TTS: Cartesia (your cloned voice) ──────────────────────────────────
    tts = CartesiaTTSService(
        api_key=config.CARTESIA_API_KEY,
        voice_id=config.CARTESIA_VOICE_ID,
        model="sonic-2",
    )

    # ── Assemble the pipeline ─────────────────────────────────────────────
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            context_aggregator.user(),
            llm,
            tts,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),
    )

    # Greet the user as soon as they join — tests the full output chain
    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        await task.queue_frames([LLMMessagesFrame(messages)])

    # End pipeline and flush log when visitor leaves
    @transport.event_handler("on_participant_disconnected")
    async def on_disconnect(transport, participant):
        conv_logger.flush()
        await task.queue_frame(EndFrame())

    runner = PipelineRunner()
    await runner.run(task)

    # Flush again as a safety net in case on_participant_disconnected didn't fire
    # (e.g. server restart, network drop). flush() is idempotent.
    conv_logger.flush()


def _mint_agent_token(room_name: str) -> str:
    from livekit.api import AccessToken, VideoGrants
    return (
        AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity("talk2me-agent")
        .with_name(f"{config.AGENT_NAME} (AI)")
        .with_grants(VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
