#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Struzon AI interview service (Exotel outbound + Pipecat).

Flow:

HRM backend  --X-API-Key-->  POST /api/ai-interviews/start
    ↓  (row in employee.ai_interviews, status=initiating)
Exotel Calls/connect  →  candidate phone  →  Exotel flow (Voicebot applet)
    ↓
Exotel opens bidirectional WebSocket  →  /ws  →  Pipecat bot
    ↓
Deepgram → Ollama Qwen 3.5 9B → Cartesia
    ↓
transcript + analysis stored; Exotel status webhook stores recording/duration
"""

import hmac
import json
import re
from contextlib import asynccontextmanager
from urllib.parse import urlencode, urlparse

import aiohttp
import asyncpg
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

import config
import db

E164 = re.compile(r"^\+[1-9]\d{7,14}$")

# Exotel call Status -> interview status (terminal callback).
EXOTEL_STATUS_MAP = {
    "busy": "busy",
    "no-answer": "no_answer",
    "failed": "failed",
    "canceled": "cancelled",
}


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if not config.INTERVIEW_API_KEY:
        raise HTTPException(503, "INTERVIEW_API_KEY is not configured")
    if not hmac.compare_digest(x_api_key, config.INTERVIEW_API_KEY):
        raise HTTPException(401, "Invalid API key")


def actor_of(x_actor: str = Header(default="")) -> str | None:
    """HRM passes the logged-in user (e.g. emp_id) for the audit trail."""
    return x_actor.strip()[:100] or None


# ---------------------------------------------------------------------------
# EXOTEL
# ---------------------------------------------------------------------------


def exotel_auth() -> aiohttp.BasicAuth:
    return aiohttp.BasicAuth(config.required("EXOTEL_API_KEY"), config.required("EXOTEL_API_TOKEN"))


async def make_exotel_call(session: aiohttp.ClientSession, to_number: str) -> str:
    """Dial the candidate and hand the answered call to the Exotel flow (Voicebot applet).

    The applet's URL must be wss://<PUBLIC_BASE_URL host>/ws. Returns the call SID.
    """
    sid = config.required("EXOTEL_SID")
    app_id = config.required("EXOTEL_APP_ID")
    callback = f"{config.required('PUBLIC_BASE_URL')}/api/ai-interviews/webhooks/exotel?" + urlencode(
        {"token": config.required("EXOTEL_WEBHOOK_TOKEN")}
    )
    data = {
        "From": to_number,
        "CallerId": config.required("EXOTEL_PHONE_NUMBER"),
        "Url": f"http://my.exotel.com/{sid}/exoml/start_voice/{app_id}",
        "CallType": "trans",
        "TimeLimit": config.EXOTEL_CALL_TIME_LIMIT,
        "TimeOut": config.EXOTEL_RING_TIMEOUT,
        "Record": "true",
        "StatusCallback": callback,
        "StatusCallbackEvents[0]": "terminal",
        "StatusCallbackContentType": "application/json",
    }
    url = f"{config.EXOTEL_API_BASE}/v1/Accounts/{sid}/Calls/connect.json"
    logger.info(f"Exotel dial-out to {to_number} via flow {app_id}")

    async with session.post(url, data=data, auth=exotel_auth()) as resp:
        text = await resp.text()
        if resp.status not in (200, 201):
            raise RuntimeError(f"Exotel API error ({resp.status}): {text[:500]}")
    try:
        call_sid = json.loads(text)["Call"]["Sid"]
    except (ValueError, KeyError, TypeError):
        match = re.search(r"<Sid>([^<]+)</Sid>", text)
        call_sid = match.group(1) if match else None
    if not call_sid:
        raise RuntimeError(f"Exotel response had no call SID: {text[:500]}")
    return call_sid


# ---------------------------------------------------------------------------
# APP
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    missing = config.missing_env()
    if missing:
        logger.warning(f"Missing env for live calls: {', '.join(missing)}")
    await db.connect()
    app.state.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
    yield
    await app.state.session.close()
    await db.close()


# No CORS: only the HRM backend calls this service, never a browser.
app = FastAPI(
    title="Struzon AI Interview Agent",
    description="Pipecat + Exotel + Ollama AI Interview Server",
    lifespan=lifespan,
)


@app.get("/")
async def health_check():
    return {
        "status": "online",
        "service": "Struzon AI Interview Agent",
        "llm": "Ollama",
        "model": config.OLLAMA_MODEL,
        "transport": "Exotel",
        "missing_env": config.missing_env(),
    }


# ---------------------------------------------------------------------------
# INTERVIEW API
# ---------------------------------------------------------------------------


class StartInterview(BaseModel):
    candidate_id: int
    job_id: int | None = None
    candidate_name: str | None = Field(default=None, max_length=200)
    phone_number: str
    position: str | None = Field(default=None, max_length=200)
    experience_years: float | None = Field(default=None, ge=0, le=60)


def public_view(row: dict) -> dict:
    """Interview summary without the transcript body or the raw provider recording URL."""
    hidden = {"transcript", "recording_url", "analysis"}
    out = {k: v for k, v in row.items() if k not in hidden}
    out["has_transcript"] = bool(row.get("transcript"))
    out["has_recording"] = bool(row.get("recording_url"))
    analysis = row.get("analysis") or {}
    out["strengths"] = analysis.get("strengths", [])
    out["areas_to_verify"] = analysis.get("areas_to_verify", [])
    return out


async def load(interview_id: int) -> dict:
    row = await db.get_interview(interview_id)
    if not row:
        raise HTTPException(404, "Interview not found")
    return row


@app.post("/api/ai-interviews/start", dependencies=[Depends(require_api_key)])
@app.post("/start", dependencies=[Depends(require_api_key)], include_in_schema=False)
async def start_interview(body: StartInterview, request: Request, actor: str | None = Depends(actor_of)):
    phone = body.phone_number.replace(" ", "").replace("-", "")
    if not E164.match(phone):
        raise HTTPException(400, "phone_number must be E.164, e.g. +919876543210")

    candidate = await db.get_candidate(body.candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")

    data = body.model_dump()
    data["phone_number"] = phone
    data["candidate_name"] = body.candidate_name or candidate.get("candidate_name")
    try:
        row = await db.create_interview(data, actor)
    except asyncpg.UniqueViolationError:
        raise HTTPException(409, "Candidate already has an active AI interview")

    try:
        call_sid = await make_exotel_call(request.app.state.session, phone)
    except Exception as e:
        logger.exception("Exotel dial-out failed")
        await db.update_interview(row["id"], {"status": "failed", "error": str(e)[:1000]}, actor=actor)
        raise HTTPException(502, "Could not start the call; see interview error")

    row = await db.update_interview(row["id"], {"call_sid": call_sid, "status": "ringing"}, only_if_active=True, actor=actor)
    return {"interview_id": row["id"], "call_sid": call_sid, "status": row["status"]}


@app.get("/api/ai-interviews/{interview_id}", dependencies=[Depends(require_api_key)])
async def get_interview(interview_id: int):
    return JSONResponse(json.loads(json.dumps(public_view(await load(interview_id)), default=str)))


@app.get("/api/ai-interviews/{interview_id}/status", dependencies=[Depends(require_api_key)])
async def get_status(interview_id: int):
    row = await load(interview_id)
    return {"interview_id": row["id"], "status": row["status"], "error": row.get("error")}


@app.get("/api/ai-interviews/{interview_id}/transcript", dependencies=[Depends(require_api_key)])
async def get_transcript(interview_id: int, actor: str | None = Depends(actor_of)):
    row = await load(interview_id)
    if not row.get("transcript"):
        raise HTTPException(404, "Transcript not available")
    await db.log_event(interview_id, "transcript_viewed", actor)
    return {"interview_id": row["id"], "transcript": row["transcript"]}


@app.get("/api/ai-interviews/{interview_id}/recording", dependencies=[Depends(require_api_key)])
async def get_recording(interview_id: int, request: Request, actor: str | None = Depends(actor_of)):
    """Stream the recording through this service so provider credentials/URLs stay private."""
    row = await load(interview_id)
    url = row.get("recording_url")
    if not url:
        raise HTTPException(404, "Recording not available")
    host = urlparse(url).hostname or ""
    if urlparse(url).scheme != "https" or not host.endswith(config.RECORDING_HOST_SUFFIXES):
        raise HTTPException(502, "Recording URL is not an Exotel HTTPS URL")

    await db.log_event(interview_id, "recording_viewed", actor)
    resp = await request.app.state.session.get(url, auth=exotel_auth(), timeout=aiohttp.ClientTimeout(total=300))
    if resp.status != 200:
        resp.release()
        raise HTTPException(502, f"Recording fetch failed ({resp.status})")

    async def body():
        try:
            async for chunk in resp.content.iter_chunked(64 * 1024):
                yield chunk
        finally:
            resp.release()

    return StreamingResponse(body(), media_type=resp.headers.get("Content-Type", "audio/mpeg"))


@app.post("/api/ai-interviews/{interview_id}/cancel", dependencies=[Depends(require_api_key)])
async def cancel_interview(interview_id: int, actor: str | None = Depends(actor_of)):
    await load(interview_id)
    row = await db.update_interview(interview_id, {"status": "cancelled"}, only_if_active=True, actor=actor)
    if row is None:
        raise HTTPException(409, "Interview is not active")
    from bot import active_runners

    runner = active_runners.get(interview_id)
    if runner:
        await runner.cancel()
    return {"interview_id": interview_id, "status": "cancelled"}


# ---------------------------------------------------------------------------
# EXOTEL STATUS WEBHOOK
# ---------------------------------------------------------------------------


@app.post("/api/ai-interviews/webhooks/exotel")
async def exotel_webhook(request: Request, token: str = ""):
    # Exotel does not sign callbacks; the secret token in the callback URL authenticates it.
    if not config.EXOTEL_WEBHOOK_TOKEN or not hmac.compare_digest(token, config.EXOTEL_WEBHOOK_TOKEN):
        raise HTTPException(401, "Invalid webhook token")

    if "json" in request.headers.get("content-type", ""):
        payload = await request.json()
    else:
        payload = dict(await request.form())

    call_sid = payload.get("CallSid")
    status = str(payload.get("Status", "")).lower()
    logger.info(f"Exotel callback {call_sid}: {status}")
    row = await db.find_for_call(call_sid, []) if call_sid else None
    if not row:
        return {"ok": True, "matched": False}

    fields: dict = {}
    if payload.get("RecordingUrl"):
        fields["recording_url"] = str(payload["RecordingUrl"])
    duration = payload.get("ConversationDuration") or payload.get("Duration")
    if str(duration or "").isdigit():
        fields["duration_seconds"] = int(duration)
    if not row.get("ended_at") and status in (*EXOTEL_STATUS_MAP, "completed"):
        fields["ended_at"] = db.utcnow()
    if fields:
        await db.update_interview(row["id"], fields, event="exotel_callback")

    # Unanswered outcomes end the interview; "completed" is set by the bot with the transcript.
    new_status = EXOTEL_STATUS_MAP.get(status)
    if new_status:
        await db.update_interview(row["id"], {"status": new_status}, only_if_active=True, actor="exotel")
    elif status == "completed" and row["status"] in ("initiating", "ringing", "answered"):
        # Call ended but the voicebot stream never reached /ws (check the Exotel flow applet URL).
        await db.update_interview(
            row["id"],
            {"status": "failed", "error": "Call ended without the voicebot stream connecting"},
            only_if_active=True,
            actor="exotel",
        )
    return {"ok": True, "matched": True}


# ---------------------------------------------------------------------------
# EXOTEL WEBSOCKET
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Exotel WebSocket accepted; starting Pipecat AI interview bot")

    try:
        from pipecat.runner.types import WebSocketRunnerArguments

        from bot import bot

        runner_args = WebSocketRunnerArguments(websocket=websocket)
        runner_args.handle_sigint = False
        await bot(runner_args)
    except Exception:
        logger.exception("WebSocket error")
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    uvicorn.run(app, host=config.HOST, port=config.PORT)
