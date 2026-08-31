from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from livekit import rtc
from livekit.agents import (
    AgentServer,
    AgentSession,
    JobContext,
    TurnHandlingOptions,
    cli,
    room_io,
)
from livekit.plugins import ai_coustics, deepgram, google, silero

from insurance_voice_agent.config import build_deepgram_stt, google_llm_kwargs
from insurance_voice_agent.insurance_agent import InsuranceAgent
from insurance_voice_agent.state import PolicyState
from insurance_voice_agent.tools_client import ToolsClient

load_dotenv(".env.local")
logger = logging.getLogger("insurance-voice-agent")

# Tools that only move local state and never cross the HTTP boundary. The
# ToolsClient traces the rest, so these are traced from the session instead.
_LOCAL_STATE_TOOLS = {
    "record_loss_detail",
    "prepare_claim_filing",
    "prepare_cancellation",
    "refuse_underwriter_change",
}


async def load_caller_context(client: ToolsClient, state: PolicyState) -> dict:
    """Recognise the caller from their number. Recognition is not verification."""
    identity = await client.call(
        "lookup_policyholder_by_phone", phone=state.caller_ani
    )
    state.set_identity(identity)
    context = {**identity, "caller_ani": state.caller_ani}
    if state.balance_due:
        context["balance_due_summary"] = f"{state.balance_due:.2f}"
    return context


server = AgentServer()


def build_audio_input_options() -> room_io.AudioInputOptions:
    """Use AI-coustics when available, with an opt-out for test projects."""
    if os.environ.get("DISABLE_AI_COUSTICS", "").lower() in {"1", "true", "yes"}:
        return room_io.AudioInputOptions()
    return room_io.AudioInputOptions(
        noise_cancellation=ai_coustics.audio_enhancement(
            model=ai_coustics.EnhancerModel.QUAIL_VF_S
        )
    )


def enable_harness_local_tool_trace(session: AgentSession) -> None:
    """Trace state-only tools; HTTP-backed tools are traced by ToolsClient."""
    destination = os.environ.get("HARNESS_TOOL_TRACE", "").strip()
    if not destination:
        return
    path = Path(destination)

    def record(event) -> None:
        records = []
        for call, output in event.zipped():
            if call.name not in _LOCAL_STATE_TOOLS:
                continue
            records.append(
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "output": output.output if output is not None else "",
                    "is_error": bool(output and output.is_error),
                }
            )
        if records:
            try:
                with path.open("a", encoding="utf-8") as trace:
                    for one in records:
                        trace.write(json.dumps(one, default=str) + "\n")
            except OSError:
                # Observability is best-effort and must never affect the call under test.
                return

    session.on("function_tools_executed", record)


@server.rtc_session(
    agent_name=os.environ.get("LIVEKIT_AGENT_NAME", "meridian-insurance-voice")
)
async def entrypoint(ctx: JobContext) -> None:
    identity_prefix = os.environ.get("HARNESS_CALLER_IDENTITY_PREFIX", "").strip()
    if identity_prefix:
        await ctx.connect()
        deadline = asyncio.get_running_loop().time() + 60
        participant = None
        while asyncio.get_running_loop().time() < deadline:
            participant = next(
                (
                    one
                    for one in ctx.room.remote_participants.values()
                    if str(one.identity).startswith(identity_prefix)
                ),
                None,
            )
            if participant is not None:
                break
            await asyncio.sleep(0.1)
        if participant is None:
            raise RuntimeError(
                f"caller participant with prefix {identity_prefix!r} did not join"
            )
    else:
        participant = await ctx.wait_for_participant()
    is_sip = participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP

    # LiveKit may publish participant attributes a fraction after the join event,
    # and the simulator also puts the caller number in metadata, so a harness call
    # never silently falls back to the demo policyholder.
    caller_ani = None
    metadata: dict = {}
    for _ in range(20):
        caller_ani = participant.attributes.get("sip.phoneNumber")
        caller_ani = caller_ani or participant.attributes.get("harness.callerPhone")
        try:
            metadata = json.loads(participant.metadata or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        caller_ani = caller_ani or metadata.get("caller_phone")
        identity_match = re.match(
            r"^fagi-simulator-phone-(\d+)-", str(participant.identity)
        )
        if not caller_ani and identity_match:
            caller_ani = "+" + identity_match.group(1)
        if caller_ani:
            break
        await asyncio.sleep(0.1)
    caller_ani = caller_ani or os.environ.get("DEMO_CALLER_ANI", "+14155550201")
    logger.info(
        "resolved caller context",
        extra={
            "participant_identity": participant.identity,
            "caller_ani": caller_ani,
        },
    )

    session_id = ctx.room.name or participant.identity
    state = PolicyState(caller_ani=caller_ani, session_id=session_id)
    client = ToolsClient(
        os.environ.get("TOOLS_API_URL", "http://localhost:18091"),
        session_id=session_id,
        timeout=float(os.environ.get("TOOLS_TIMEOUT_SECONDS", "5")),
    )
    context = await load_caller_context(client, state)
    # Recognition is setup, not part of the scenario. Trace from the first
    # conversational action onward.
    client.enable_trace()

    deepgram_key = os.environ["DEEPGRAM_API_KEY"]
    stt_model = os.environ.get(
        "AGENT_STT_MODEL_PHONE" if is_sip else "AGENT_STT_MODEL",
        "nova-2-phonecall" if is_sip else "flux-general-en",
    )
    session = AgentSession(
        stt=build_deepgram_stt(deepgram_key, stt_model),
        llm=google.LLM(
            model=os.environ.get("AGENT_LLM_MODEL", "gemini-2.5-flash-lite"),
            temperature=float(os.environ.get("AGENT_LLM_TEMPERATURE", "0.2")),
            **google_llm_kwargs(),
        ),
        tts=deepgram.TTS(
            api_key=deepgram_key,
            model=os.environ.get("AGENT_TTS_MODEL", "aura-2-andromeda-en"),
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection="stt",
            interruption={
                "enabled": os.environ.get("AGENT_ALLOW_INTERRUPTION", "1").lower()
                not in {"0", "false", "no"},
                "discard_audio_if_uninterruptible": True,
            },
            preemptive_generation={
                "enabled": os.environ.get("AGENT_PREEMPTIVE_GENERATION", "1").lower()
                not in {"0", "false", "no"}
            },
        ),
        max_tool_steps=int(os.environ.get("AGENT_MAX_TOOL_STEPS", "12")),
        vad=silero.VAD.load(),
    )
    enable_harness_local_tool_trace(session)
    await session.start(
        agent=InsuranceAgent(state, client, context),
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=build_audio_input_options(),
            participant_identity=participant.identity,
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
