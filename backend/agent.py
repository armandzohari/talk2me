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
from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask

from pipecat.transports.services.livekit import LiveKitParams, LiveKitTransport
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams

from pipecat.services.deepgram import DeepgramSTTService, LiveOptions
from pipecat.services.anthropic import AnthropicLLMService
from pipecat.services.cartesia import CartesiaTTSService

from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.frames.frames import LLMMessagesFrame, EndFrame

import config


# ── Transcript interceptor ─────────────────────────────────────────────────────
# Replaces context.messages with a smart list that publishes each new turn to the
# LiveKit data channel (topic: "transcript") so the frontend can display it live.
# This approach avoids touching the Pipecat pipeline entirely.

class _TranscriptList(list):
    def __init__(self, initial_messages, transport):
        super().__init__(initial_messages)
        self._transport = transport
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
            logger.debug(f"Transcript: queuing publish for {speaker}: {text[:60]}")
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(self._publish({"speaker": speaker, "text": text}))
            except Exception as e:
                logger.error(f"Transcript: failed to schedule publish task: {e}")

    async def _publish(self, payload):
        """Best-effort — never let a failure here affect the call."""
        try:
            room = getattr(self._transport, "_room", None)
            if not room:
                logger.error("Transcript: transport._room is None — cannot publish")
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


async def run_agent(room_name: str):
    # ── Transport (LiveKit WebRTC) ──────────────────────────────────────────
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
                    stop_secs=0.5,
                    min_volume=0.2,
                    confidence=0.5,
                )
            ),
        ),
    )

    # ── STT: Deepgram (streaming) ───────────────────────────────────────────
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

    # ── LLM: Claude ────────────────────────────────────────────────────────
    llm = AnthropicLLMService(
        api_key=config.ANTHROPIC_API_KEY,
        model="claude-sonnet-4-6",
    )

    # Conversation context — system prompt + greeting trigger
    messages = [
        {"role": "system", "content": config.SYSTEM_PROMPT},
        {"role": "user",   "content": "Please greet the visitor warmly and briefly. Introduce yourself as Armand."},
    ]
    context = OpenAILLMContext(messages=messages)
    # Swap in the transcript-aware list — no pipeline changes needed.
    # context.messages is a read-only property; we write to the backing attribute directly.
    context._messages = _TranscriptList(list(context.messages), transport)
    context_aggregator = llm.create_context_aggregator(context)

    # ── TTS: Cartesia (your cloned voice) ───────────────────────────────────
    tts = CartesiaTTSService(
        api_key=config.CARTESIA_API_KEY,
        voice_id=config.CARTESIA_VOICE_ID,
        model="sonic-2",
    )

    # ── Assemble the pipeline ──────────────────────────────────────────────
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

    # End pipeline when visitor leaves
    @transport.event_handler("on_participant_disconnected")
    async def on_disconnect(transport, participant):
        await task.queue_frame(EndFrame())

    runner = PipelineRunner()
    await runner.run(task)


def _mint_agent_token(room_name: str) -> str:
    from livekit.api import AccessToken, VideoGrants
    return (
        AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity("talk2me-agent")
        .with_name(f"{config.AGENT_NAME} (AI)")
        .with_grants(VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
