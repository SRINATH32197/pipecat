#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio

from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import EndWorkerFrame, FunctionCallResultProperties, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport

# Local Ollama LLM
from pipecat.services.ollama.llm import OLLamaLLMService

# STT / TTS
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.llm_service import FunctionCallParams

from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams
from pipecat.workers.runner import WorkerRunner

import analysis
import config
import db

# interview_id -> runner, so POST /cancel can stop a live call.
active_runners: dict[int, WorkerRunner] = {}


# ---------------------------------------------------------------------------
# AI INTERVIEW SYSTEM PROMPT
# ---------------------------------------------------------------------------

INTERVIEW_SYSTEM_PROMPT = """
You are an AI interviewer for Struzon Technologies conducting a professional job interview over a phone call.

Interview behavior:

1. Start by greeting the candidate by name professionally.
2. Introduce yourself as an AI interviewer from Struzon Technologies.
3. Explain that the interview will take about fifteen minutes.
4. Ask one question at a time.
5. Wait for the candidate's answer before asking the next question.
6. Ask natural follow-up questions based on the candidate's answer.
7. Focus on the candidate's actual experience rather than theoretical memorized answers.
8. Ask job-specific technical questions for the position, covering:
   - Previous projects
   - Technical skills
   - Problem solving
   - Tools and technologies
   - Real-world project experience
   - Team collaboration
   - Debugging and troubleshooting
9. Keep each response short, one or two sentences, because it will be converted to speech.
10. Do not use markdown, bullet points, emojis or any special formatting.
11. Speak naturally and professionally.
12. Never ask multiple questions in the same response.
13. Do not repeat a question that the candidate has already answered.
14. If the candidate gives a short answer, ask a relevant follow-up question.
15. If the candidate says they do not know something, move to another relevant question.
16. Do not reveal any evaluation or score during the interview.
17. After about eight to ten main questions, or if the candidate asks to stop, close the interview:
    thank the candidate, clearly say that the HR team will review the interview and get back to them,
    say goodbye, and then call the end_interview function.

Important:

This is an interview, not a casual conversation.
Keep the conversation focused on evaluating the candidate's skills, experience,
communication and suitability for the job.
Never make a final hiring decision yourself.
"""


def build_system_prompt(interview: dict | None) -> str:
    """Add only interview-relevant candidate context (no contact details)."""
    if not interview:
        return INTERVIEW_SYSTEM_PROMPT
    lines = []
    if interview.get("candidate_name"):
        lines.append(f"Candidate: {interview['candidate_name']}")
    if interview.get("position"):
        lines.append(f"Position: {interview['position']}")
    if interview.get("experience_years") is not None:
        lines.append(f"Experience: {interview['experience_years']} years")
    if not lines:
        return INTERVIEW_SYSTEM_PROMPT
    return INTERVIEW_SYSTEM_PROMPT + "\nCandidate details:\n" + "\n".join(lines) + "\n"


def build_transcript(context: LLMContext) -> tuple[str, int]:
    """Return the spoken conversation as text, plus the number of candidate turns."""
    lines, candidate_turns = [], 0
    for msg in context.get_messages():
        role = msg.get("role") if isinstance(msg, dict) else None
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = (content or "").strip()
        if not text:
            continue
        if role == "user":
            candidate_turns += 1
        lines.append(f"{'Candidate' if role == 'user' else 'Interviewer'}: {text}")
    return "\n".join(lines), candidate_turns


async def end_interview(params: FunctionCallParams):
    """Tool the LLM calls after its closing line; flushes queued audio, then ends the call."""
    await params.result_callback(
        {"success": True}, properties=FunctionCallResultProperties(run_llm=False)
    )
    await params.llm.push_frame(EndWorkerFrame())


# ---------------------------------------------------------------------------
# RUN BOT
# ---------------------------------------------------------------------------


async def run_bot(transport: BaseTransport, handle_sigint: bool, interview: dict | None):
    interview_id = interview["id"] if interview else None

    # Ollama must be running locally. base_url is a constructor argument, not a
    # Settings field, in this Pipecat build. reasoning_effort=none disables Qwen
    # thinking: measured 43 s vs 0.5 s per reply on qwen3.5:9b.
    llm = OLLamaLLMService(
        base_url=f"{config.OLLAMA_BASE_URL}/v1",
        settings=OLLamaLLMService.Settings(
            model=config.OLLAMA_MODEL,
            system_instruction=build_system_prompt(interview),
            extra={"reasoning_effort": "none"},
        ),
    )

    stt = DeepgramSTTService(api_key=config.DEEPGRAM_API_KEY)

    tts = CartesiaTTSService(
        api_key=config.CARTESIA_API_KEY,
        settings=CartesiaTTSService.Settings(voice=config.CARTESIA_VOICE_ID),
    )

    tools = ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="end_interview",
                description="End the phone call after the closing statement has been spoken.",
                properties={},
                required=[],
                handler=end_interview,
            )
        ]
    )

    context = LLMContext(tools=tools)

    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),  # Incoming candidate audio
            stt,  # Candidate speech -> text
            user_aggregator,  # Add candidate message to context
            llm,  # Local Qwen model
            tts,  # Qwen response -> speech
            transport.output(),  # Audio back to candidate
            assistant_aggregator,  # Store assistant response in context
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    runner = WorkerRunner(handle_sigint=handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Candidate connected (interview {interview_id})")
        # The candidate answered the phone: the interviewer speaks first.
        context.add_message(
            {"role": "developer", "content": "The candidate has answered the call. Greet them and begin."}
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Candidate disconnected (interview {interview_id})")
        await runner.cancel()

    if interview_id:
        active_runners[interview_id] = runner
    try:
        await runner.run()
    finally:
        active_runners.pop(interview_id, None)
        if interview_id:
            await finalize(interview_id, context)


async def finalize(interview_id: int, context: LLMContext) -> None:
    """Persist the transcript, set the final status, then run analysis."""
    transcript, candidate_turns = build_transcript(context)
    fields = {"ended_at": db.utcnow(), "transcript": transcript or None}
    if candidate_turns:
        fields["status"] = "completed"
    else:
        fields.update(status="failed", error="Call ended before the candidate spoke")
    # only_if_active: a cancel or a busy/no-answer webhook already set the outcome.
    row = await db.update_interview(interview_id, fields, only_if_active=True)
    if row is None:
        await db.update_interview(interview_id, {"transcript": transcript or None, "ended_at": fields["ended_at"]})
    logger.info(f"Interview {interview_id} finalized: {candidate_turns} candidate turns")
    if candidate_turns:
        # Detached so the WebSocket handler returns promptly; errors are stored on the row.
        asyncio.create_task(analysis.analyze_interview(interview_id))


# ---------------------------------------------------------------------------
# PIPECAT BOT ENTRY POINT
# ---------------------------------------------------------------------------


async def bot(runner_args: RunnerArguments):
    """Main bot entry point. Links the Exotel stream to its HRM interview row."""

    transport_params = {
        "exotel": lambda: FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    }

    # create_transport detects the telephony provider from the first message
    # and fills runner_args.call_data with call SID / from / to.
    transport = await create_transport(runner_args, transport_params)

    call = runner_args.call_data
    call_sid = call.call_id if call else None
    phones = [p for p in (call.from_number, call.to_number) if p] if call else []

    interview = await db.find_for_call(call_sid, phones)
    if interview:
        fields = {"status": "in_progress", "started_at": db.utcnow()}
        if call_sid and interview.get("call_sid") != call_sid:
            fields["call_sid"] = call_sid
        interview = await db.update_interview(interview["id"], fields, only_if_active=True)
    if not interview:
        logger.warning(f"No active interview for call {call_sid} / {phones}; running without persistence")

    await run_bot(transport, runner_args.handle_sigint, interview)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
