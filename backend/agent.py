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
                    min_volume=0.2,
                    confidence=0.5,
                )
            ),
        ),
    )

    # ── STT: Deepgram (streaming) ───────────────────────────────────────────
    stt = DeepgramSTTService(
        api_key=config.DEEPGRAM_API_KEY,
        live_options=LiveOptions(
            model="nova-2-general",
            language="en-US",
            encoding="linear16",
            sample_rate=16000,
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
    messages = [{"role": "user", "content": "Please greet the visitor warmly and briefly."}]
    context = OpenAILLMContext(messages=messages)
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
