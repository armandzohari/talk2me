"""
Pipecat voice pipeline for Talk2Me.

Flow:
  LiveKit mic audio
    → Silero VAD          (detects speech start/end)
    → Deepgram STT        (streaming transcription, Nova-3)
    → Claude LLM          (conversational brain)
    → Cartesia TTS        (your cloned voice)
    → LiveKit speaker audio
"""

import asyncio

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

from pipecat.frames.frames import EndFrame

import config


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
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                stop_secs=0.5,
                start_secs=0.2,      # Added: Reduces delay before speech is captured
                min_volume=0.2,      # CRITICAL: Lowered from 0.6 so it hears you
                confidence=0.5,      # Lowered: Makes VAD more sensitive to speech
                )
            ),
        ),
    )

    # ── STT: Deepgram Nova-3 (streaming) ───────────────────────────────────
    stt = DeepgramSTTService(
        api_key=config.DEEPGRAM_API_KEY,
        live_options=LiveOptions(
            model="nova-3",
            language="en-US",
            smart_format=True,
            interim_results=True,  # Required: pipecat needs interim results for pipeline flow
            punctuate=True,
            endpointing=300 # ADDED: tells Deepgram exactly when to stop listening and send the text to Claude
        ),
    )

    # ── LLM: Claude ────────────────────────────────────────────────────────
    llm = AnthropicLLMService(
        api_key=config.ANTHROPIC_API_KEY,
        model="claude-sonnet-4-6",
    )

    # Conversation context — system prompt lives here
    context = OpenAILLMContext(
        messages=[{"role": "user", "content": "Hello"}, {"role": "assistant", "content": config.SYSTEM_PROMPT}]
    )
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
            transport.input(),               # Raw audio in from LiveKit
            stt,                             # Audio → transcript
            context_aggregator.user(),       # Transcript → context message
            llm,                             # Context → LLM response stream
            tts,                             # LLM text → speech audio
            transport.output(),              # Audio out to LiveKit
            context_aggregator.assistant(),  # Store assistant turn in context
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),  # Enables barge-in
    )

    # When the last participant leaves, end the pipeline
    @transport.event_handler("on_participant_disconnected")
    async def on_disconnect(transport, participant):
        await task.queue_frame(EndFrame())

    runner = PipelineRunner()
    await runner.run(task)


def _mint_agent_token(room_name: str) -> str:
    """Mint a LiveKit token for the AI agent participant."""
    from livekit.api import AccessToken, VideoGrants

    return (
        AccessToken(config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET)
        .with_identity("talk2me-agent")
        .with_name(f"{config.AGENT_NAME} (AI)")
        .with_grants(VideoGrants(room_join=True, room=room_name))
        .to_jwt()
    )
