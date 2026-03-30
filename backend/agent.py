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
from pipecat.frames.frames import LLMMessagesFrame, EndFrame, TranscriptionFrame, TextFrame

# FrameDirection lives in different places across pipecat versions
try:
    from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
except ImportError:
    from pipecat.processors.frame_processor import FrameProcessor
    FrameDirection = None  # type: ignore

# LLMFullResponseEndFrame was renamed in some pipecat versions
try:
    from pipecat.frames.frames import LLMFullResponseEndFrame as _LlmEndFrame
except ImportError:
    try:
        from pipecat.frames.frames import LLMResponseEndFrame as _LlmEndFrame
    except ImportError:
        _LlmEndFrame = None  # type: ignore

import config


# ── Transcript forwarders ──────────────────────────────────────────────────────

class _TranscriptBase(FrameProcessor):
    """Shared helper: publishes a JSON dict to the LiveKit data channel."""

    def __init__(self, transport: LiveKitTransport):
        super().__init__()
        self._transport = transport

    async def _publish(self, payload: dict):
        """Best-effort — never let a publish failure break the call."""
        try:
            room = getattr(self._transport, '_room', None)
            if room is None:
                return
            lp = getattr(room, 'local_participant', None)
            if lp is None:
                return
            data = json.dumps(payload).encode('utf-8')
            # livekit-python ≥1.0 uses keyword args; 0.x uses positional + kind enum
            try:
                await lp.publish_data(data, reliable=True, topic="transcript")
            except TypeError:
                from livekit.rtc import DataPacketKind
                await lp.publish_data(data, DataPacketKind.RELIABLE, topic="transcript")
        except Exception:
            pass


class UserTranscriptForwarder(_TranscriptBase):
    """Between STT and context aggregator — taps final TranscriptionFrames."""

    async def process_frame(self, frame, direction):
        try:
            if isinstance(frame, TranscriptionFrame):
                text = (getattr(frame, 'text', '') or '').strip()
                is_final = getattr(frame, 'is_final', True)
                if text and is_final:
                    await self._publish({"speaker": "visitor", "text": text})
        except Exception:
            pass
        # MUST call super() — this is what wires the frame into Pipecat's internal queue
        await super().process_frame(frame, direction)


class AgentTranscriptForwarder(_TranscriptBase):
    """Between LLM and TTS — buffers streaming TextFrames and flushes on end."""

    def __init__(self, transport: LiveKitTransport):
        super().__init__(transport)
        self._buffer = ""

    async def process_frame(self, frame, direction):
        try:
            if isinstance(frame, TextFrame):
                self._buffer += (getattr(frame, 'text', '') or '')
            elif _LlmEndFrame and isinstance(frame, _LlmEndFrame):
                text = self._buffer.strip()
                if text:
                    await self._publish({"speaker": "agent", "text": text})
                self._buffer = ""
        except Exception:
            pass
        # MUST call super() — this is what wires the frame into Pipecat's internal queue
        await super().process_frame(frame, direction)


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
    context_aggregator = llm.create_context_aggregator(context)

    # ── TTS: Cartesia (your cloned voice) ───────────────────────────────────
    tts = CartesiaTTSService(
        api_key=config.CARTESIA_API_KEY,
        voice_id=config.CARTESIA_VOICE_ID,
        model="sonic-2",
    )

    # ── Transcript forwarders (send speaker text to frontend data channel) ─
    user_transcript = UserTranscriptForwarder(transport)
    agent_transcript = AgentTranscriptForwarder(transport)

    # ── Assemble the pipeline ──────────────────────────────────────────────
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_transcript,            # tap visitor speech after STT
            context_aggregator.user(),
            llm,
            agent_transcript,           # tap agent speech after LLM
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
