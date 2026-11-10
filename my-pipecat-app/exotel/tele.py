"""Telephonic auto calls — HRM's follow-up / scheduling / reminder calls to candidates.

HRM owns the tables (employee.tele_calls, created by backend/src/routes/telephonicCalls.ts),
the question catalog, the scheduler and the department notifications. This module only
places the call HRM asks for, holds the conversation, and writes the answers back:

    HRM scheduler -> POST /api/telephonic-calls/{id}/dial -> Exotel -> /ws -> run_tele_call
    -> answers (record_answer tool + post-call extraction) -> employee.tele_calls
    -> HRM scheduler sees status=completed and notifies HR / IT / Facility ...
"""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import aiohttp
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import EndWorkerFrame, FunctionCallResultProperties
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import BaseTransport
from pipecat.workers.runner import WorkerRunner

import config
import db
from bot import build_transcript, run_call

IST = ZoneInfo("Asia/Kolkata")
ACTIVE = ("dialing", "ringing", "in_progress")
# Asked for by the agent itself when the candidate cannot talk now; HRM books a call-back.
CALLBACK_KEY = "callback_at"

# call id -> runner, so a cancel from HRM can stop a live call.
active_tele_runners: dict[int, WorkerRunner] = {}

# ---------------------------------------------------------------------------
# DB (tables are HRM's; timestamps are timestamptz, so now() is used, never Python time)
# ---------------------------------------------------------------------------


def _row(record) -> dict | None:
    if record is None:
        return None
    row = dict(record)
    for k in ("questions", "answers", "context"):
        if isinstance(row.get(k), str):
            row[k] = json.loads(row[k])
    return row


async def log(call_id: int, action: str, detail: str | None = None, actor: str = "ai-agent"):
    try:
        await db.pool().execute(
            "INSERT INTO employee.tele_call_log (call_id, action, detail, actor) VALUES ($1, $2, $3, $4)",
            call_id,
            action,
            detail,
            actor,
        )
    except Exception as e:  # the log must never break a call
        logger.warning(f"tele_call_log insert failed: {e}")


async def get_call(call_id: int) -> dict | None:
    return _row(await db.pool().fetchrow("SELECT * FROM employee.tele_calls WHERE id = $1", call_id))


async def find_for_call(call_sid: str | None, phone_numbers: list[str]) -> dict | None:
    """Match an Exotel stream to its call: by SID, else by a recent dialling phone number."""
    if call_sid:
        row = await db.pool().fetchrow(
            "SELECT * FROM employee.tele_calls WHERE call_sid = $1 ORDER BY id DESC LIMIT 1", call_sid
        )
        if row:
            return _row(row)
    tails = [p[-10:] for p in phone_numbers if p and len(p) >= 10]
    if not tails:
        return None
    return _row(
        await db.pool().fetchrow(
            """
            SELECT * FROM employee.tele_calls
            WHERE status = ANY($1::text[])
              AND right(regexp_replace(phone, '\\D', '', 'g'), 10) = ANY($2::text[])
              AND updated_at > now() - INTERVAL '15 minutes'
            ORDER BY id DESC LIMIT 1
            """,
            list(ACTIVE),
            tails,
        )
    )


async def set_status(
    call_id: int, status: str, *, error: str | None = None, only_if_active: bool = True, **extra
) -> dict | None:
    """Move a call to `status`; with only_if_active a terminal status is never overwritten."""
    sets = ["status = $2", "updated_at = now()"]
    args: list = [call_id, status]
    if error is not None:
        args.append(error[:1000])
        sets.append(f"error = ${len(args)}")
    for col, val in extra.items():
        args.append(val)
        sets.append(f"{col} = ${len(args)}")
    if status == "in_progress":
        sets.append("started_at = COALESCE(started_at, now())")
    elif status not in ACTIVE:
        sets.append("ended_at = COALESCE(ended_at, now())")
    sql = f"UPDATE employee.tele_calls SET {', '.join(sets)} WHERE id = $1"
    if only_if_active:
        args.append(list(ACTIVE))
        sql += f" AND status = ANY(${len(args)}::text[])"
    row = _row(await db.pool().fetchrow(sql + " RETURNING *", *args))
    if row:
        await log(call_id, status, error)
    return row


async def merge_answers(call_id: int, answers: dict) -> None:
    await db.pool().execute(
        "UPDATE employee.tele_calls SET answers = COALESCE(answers, '{}'::jsonb) || $2::jsonb, "
        "updated_at = now() WHERE id = $1",
        call_id,
        json.dumps(answers),
    )


async def save_provider_result(call_id: int, fields: dict) -> None:
    """Recording URL / duration from the Exotel status webhook."""
    if not fields:
        return
    cols = list(fields)
    sets = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    await db.pool().execute(
        f"UPDATE employee.tele_calls SET {sets}, updated_at = now() WHERE id = $1",
        call_id,
        *fields.values(),
    )


# ---------------------------------------------------------------------------
# PROMPT
# ---------------------------------------------------------------------------

ANSWER_FORMATS = {
    "yes_no": "yes or no",
    "date": "a date as YYYY-MM-DD",
    "time": "a time as HH:MM in 24-hour format",
    "datetime": "a date and time as YYYY-MM-DD HH:MM",
    "choice": "exactly one of the listed options",
    "text": "a short phrase in the candidate's own words",
}

PURPOSE_INTRO = {
    "schedule": "to schedule their interview and collect a few details for their visit",
    "reminder": "to remind them about their upcoming interview and confirm their visit",
    "followup": "to follow up on their application and confirm a few details",
}


def build_prompt(call: dict) -> str:
    now = datetime.now(IST)
    name = call.get("candidate_name") or "the candidate"
    position = call.get("position") or "the open position"
    ctx = call.get("context") or {}

    lines = [
        "You are a polite, friendly phone assistant calling on behalf of the HR team of Struzon Technologies.",
        f"You are calling {name} about the {position} position, "
        f"{PURPOSE_INTRO.get(call.get('purpose'), PURPOSE_INTRO['followup'])}.",
        f"Today is {now:%A, %d %B %Y} and the time is {now:%H:%M} (India time).",
        "",
        "How to talk:",
        "- Greet the candidate by name, say you are an AI assistant from Struzon Technologies HR, and ask if this is a good time to talk.",
        "- If they cannot talk now, ask when you should call back, record it with record_answer using "
        f"question_key '{CALLBACK_KEY}' as YYYY-MM-DD HH:MM, thank them and call end_call.",
        "- Ask ONE question at a time, in the order below, and wait for the answer.",
        "- After each answer, call record_answer with the question key and the answer in the requested format, then ask the next question.",
        "- If an answer is unclear, ask once more. If the candidate does not know, record their words as they said them.",
        "- Keep every reply to one or two short sentences. No lists, markdown or emojis: your words are spoken aloud.",
        "- Use only the facts given here. Never invent addresses, salaries, timings or policies; if asked, say the HR team will confirm.",
        "- When all questions are answered, briefly repeat the key details back, thank the candidate, say goodbye, then call end_call.",
    ]
    if call.get("message"):
        lines += ["", "Tell the candidate this message from HR early in the call:", str(call["message"])]
    if ctx.get("interview_at"):
        lines += ["", f"Their interview is currently set for {ctx['interview_at']}."]
    if ctx.get("offered_slots"):
        lines += ["", "HR has offered these interview slots; offer them first:", *[f"- {s}" for s in ctx["offered_slots"]]]

    lines += ["", "Questions:"]
    for i, q in enumerate(call.get("questions") or [], 1):
        fmt = ANSWER_FORMATS.get(q.get("answer_type"), ANSWER_FORMATS["text"])
        opts = q.get("options") or []
        extra = f" Options: {', '.join(opts)}." if opts else ""
        lines.append(f"{i}. [{q['key']}] {q['prompt']} (record as {fmt}.{extra})")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CALL
# ---------------------------------------------------------------------------


async def run_tele_call(transport: BaseTransport, handle_sigint: bool, call: dict, call_sid: str | None):
    call_id = call["id"]
    fields = {"call_sid": call_sid} if call_sid and call.get("call_sid") != call_sid else {}
    call = await set_status(call_id, "in_progress", **fields) or call
    keys = [q["key"] for q in call.get("questions") or []] + [CALLBACK_KEY]
    recorded: dict[str, str] = {}

    async def record_answer(params: FunctionCallParams):
        key = str(params.arguments.get("question_key", ""))
        answer = str(params.arguments.get("answer", "")).strip()[:500]
        if key not in keys or not answer:
            await params.result_callback({"saved": False, "reason": f"question_key must be one of {keys}"})
            return
        recorded[key] = answer
        await merge_answers(call_id, {key: answer})
        await params.result_callback({"saved": True})

    async def end_call(params: FunctionCallParams):
        await params.result_callback({"success": True}, properties=FunctionCallResultProperties(run_llm=False))
        await params.llm.push_frame(EndWorkerFrame())

    tools = ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="record_answer",
                description="Save the candidate's answer to one question.",
                properties={
                    "question_key": {"type": "string", "enum": keys},
                    "answer": {"type": "string", "description": "The answer in the requested format."},
                },
                required=["question_key", "answer"],
                handler=record_answer,
            ),
            FunctionSchema(
                name="end_call",
                description="End the phone call after the goodbye has been spoken.",
                properties={},
                required=[],
                handler=end_call,
            ),
        ]
    )
    context = LLMContext(tools=tools)
    try:
        await run_call(
            transport,
            handle_sigint,
            context,
            label=f"telephonic call {call_id}",
            system_prompt=build_prompt(call),
            registry=active_tele_runners,
            key=call_id,
        )
    finally:
        await finalize(call, context, recorded)


async def finalize(call: dict, context: LLMContext, recorded: dict) -> None:
    """Store the transcript, fill unanswered questions from it, and close the call."""
    call_id = call["id"]
    transcript, candidate_turns = build_transcript(context)
    await db.pool().execute(
        "UPDATE employee.tele_calls SET transcript = $2, updated_at = now() WHERE id = $1",
        call_id,
        transcript or None,
    )
    if candidate_turns:
        missing = [q for q in call.get("questions") or [] if q["key"] not in recorded]
        if missing:
            try:
                found = await extract_answers(transcript, missing)
                if found:
                    await merge_answers(call_id, found)
                    await log(call_id, "answers_extracted", ", ".join(found))
            except Exception as e:
                logger.exception(f"Answer extraction failed for telephonic call {call_id}")
                await log(call_id, "extraction_failed", str(e)[:500])
        await set_status(call_id, "completed")
    else:
        await set_status(call_id, "failed", error="Call ended before the candidate spoke")
    logger.info(f"Telephonic call {call_id} finalized: {candidate_turns} candidate turns")


async def extract_answers(transcript: str, questions: list[dict]) -> dict:
    """Read answers the agent heard but did not record. Unanswered ones are left out."""
    schema = {
        "type": "object",
        "properties": {q["key"]: {"type": ["string", "null"]} for q in questions},
        "required": [q["key"] for q in questions],
    }
    asked = "\n".join(
        f"- {q['key']}: {q['prompt']} (format: {ANSWER_FORMATS.get(q.get('answer_type'), ANSWER_FORMATS['text'])}"
        + (f"; options: {', '.join(q.get('options') or [])}" if q.get("options") else "")
        + ")"
        for q in questions
    )
    today = datetime.now(IST).strftime("%A, %d %B %Y")
    payload = {
        "model": config.OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0},
        "messages": [
            {
                "role": "system",
                "content": "Extract the candidate's answers from a phone-call transcript. "
                "Use only what the candidate actually said. Use null when a question was not answered. "
                f"Today is {today} (India); resolve words like 'tomorrow' to real dates.",
            },
            {"role": "user", "content": f"Questions:\n{asked}\n\nTranscript:\n{transcript}"},
        ],
    }
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
        async with session.post(f"{config.OLLAMA_BASE_URL}/api/chat", json=payload) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Ollama error ({resp.status}): {body[:300]}")
    result = json.loads(json.loads(body)["message"]["content"])
    return {k: str(v).strip()[:500] for k, v in result.items() if v not in (None, "") and k in schema["properties"]}
