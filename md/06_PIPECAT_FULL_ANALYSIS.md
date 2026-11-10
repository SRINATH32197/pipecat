# Pipecat AI Interview Service — Full Analysis

Date: 2026-11-04 · Scope: `Dev-Pro/pipecat` (framework) + `Dev-Pro/pipecat/my-pipecat-app` (Struzon service)

---

## 1. Summary

| Item | State |
| --- | --- |
| What it is | Phone interviewer: Exotel dials the candidate, a Pipecat voice bot (Deepgram → local Qwen → Cartesia) runs the interview, the transcript is scored and stored in HRM PostgreSQL |
| Code | `my-pipecat-app/exotel/` — `server.py` 353, `bot.py` 289, `db.py` 211, `analysis.py` 110, `config.py` 65 lines |
| Framework | Pipecat `0.0.0.dev13452`, local checkout used as a uv workspace source |
| Built | P0 + P1 (2026-09-29): API, DB tables, Exotel dial-out, webhook, WebSocket bot, transcript, AI analysis, audit events |
| Tested | Against a mock Exotel and a simulated Exotel WebSocket handshake; real Ollama analysis |
| **Not done** | No real phone call yet; no HRM backend/UI; **no automatic calling, scheduling, retries or reminders** |
| Local DB | `employee.ai_interviews` and `employee.ai_interview_events` exist, 0 rows; `employee.candidates` is empty locally |

---

## 2. How Pipecat (the framework) works

Pipecat is an open-source Python framework for real-time voice agents. Everything is a **frame** moving through a **pipeline** of **processors**.

| Concept | Meaning | Where |
| --- | --- | --- |
| Frame | A unit of data (audio chunk, text, transcript) or a control signal (start, end, interruption) | `src/pipecat/frames/` |
| FrameProcessor | One step that receives frames and pushes new ones | `src/pipecat/processors/` |
| Pipeline | Ordered list of processors | `src/pipecat/pipeline/` |
| PipelineWorker | Runs one pipeline (sends `StartFrame`, handles errors/end) | `src/pipecat/pipeline/worker.py` |
| WorkerRunner | Executes workers; `cancel()` stops them | `src/pipecat/workers/runner.py` |
| Transport | Audio I/O (WebRTC, WebSocket, telephony) | `src/pipecat/transports/` |
| Serializer | Converts a provider's wire protocol to frames — `ExotelFrameSerializer` decodes base64 8 kHz audio, handles `media` / `dtmf` / `clear` events | `src/pipecat/serializers/exotel.py` |
| Services | 70+ STT / LLM / TTS providers | `src/pipecat/services/` |
| LLMContext + aggregators | Keep the conversation history; add user and bot turns | `src/pipecat/processors/aggregators/` |
| VAD | Silero voice-activity detection decides when the candidate stopped talking | `src/pipecat/audio/vad/` |
| Runner utils | `create_transport()` auto-detects the provider from the first WebSocket message (Exotel = `event: start` with `stream_sid`, `call_sid`, `account_sid`) and fills `call_data` (call SID, from, to) | `src/pipecat/runner/utils.py` |

Other parts of the repo (not used by our service): `examples/` (24 topic folders), `evals/` behavioural test suite, `flows/`, multi-worker bus (`bus/`, `registry/`), `docs/architecture/`.

---

## 3. Struzon service architecture

```text
 HRM backend (planned)                     Exotel cloud                       Struzon AI server (:7860)
 ─────────────────────                     ────────────                       ─────────────────────────
 POST /api/ai-interviews/start ──X-API-Key──────────────────────────────────▶ server.py  start_interview()
                                                                                 │ 1. validate E.164 phone
                                                                                 │ 2. candidate exists?
                                                                                 │ 3. INSERT ai_interviews (initiating)
                                                                                 │    unique index → 409 if active
                                          Calls/connect.json ◀───────────────── │ 4. make_exotel_call()
                                               │                                 │ 5. status = ringing, store call_sid
                                               ▼
                                   rings candidate's phone
                                               │ answered
                                               ▼
                                   Exotel Flow (EXOTEL_APP_ID)
                                   Voicebot applet → wss://<host>/ws ──────────▶ server.py  /ws
                                                                                 │ bot.py  bot()
                                                                                 │  create_transport → Exotel serializer
                                                                                 │  find_for_call(call_sid / phone)
                                                                                 │  status = in_progress
                                                                                 ▼
                                        8 kHz audio ⇄ ─────────────── Pipecat pipeline:
                                                                   transport.input → Deepgram STT
                                                                   → user aggregator (Silero VAD)
                                                                   → Ollama qwen3.5:9b (thinking off)
                                                                   → Cartesia TTS → transport.output
                                                                   → assistant aggregator
                                                                                 │ LLM calls end_interview tool
                                                                                 │ or candidate hangs up
                                                                                 ▼
                                                                         finalize(): transcript + status
                                                                         → background analysis (Ollama JSON)
                                   call ends
                                   StatusCallback (terminal) ──?token=────────▶ /api/ai-interviews/webhooks/exotel
                                                                                 recording_url, duration,
                                                                                 busy / no_answer / failed
                                                                                 ▼
                                                                    HRM PostgreSQL employee.ai_interviews
                                                                                 + ai_interview_events (audit)
```

### Components

| Layer | Technology | Notes |
| --- | --- | --- |
| API | FastAPI + uvicorn, port 7860 | No CORS — only the HRM backend should call it |
| Telephony | Exotel `Calls/connect` + Voicebot applet | Bidirectional WebSocket, 8 kHz |
| STT | Deepgram | API key required |
| LLM | Ollama `qwen3.5:9b` (local, OpenAI-compatible `/v1`) | `reasoning_effort: none` — thinking on = ~43 s/reply, off = ~0.5 s |
| TTS | Cartesia | Voice `71a7ad14-…` default |
| Turn detection | Silero VAD | |
| Analysis | Ollama `/api/chat` with JSON schema, `think:false`, temp 0.2 | Runs after the call |
| DB | HRM PostgreSQL (asyncpg pool 1–5) | Tables auto-created on boot |
| Tunnel (dev) | ngrok (installed) or cloudflared (`my-pipecat-app/README.md`) | Must give a public HTTPS/WSS URL |

---

## 4. Call lifecycle — step by step

1. **Trigger.** A caller (HRM backend, curl, script) sends `POST /api/ai-interviews/start` with `X-API-Key` and optional `X-Actor` (who started it, for audit).
   ```json
   { "candidate_id": 12, "job_id": 25, "phone_number": "+919876543210",
     "position": "Senior Tekla Modeler", "experience_years": 5 }
   ```
2. **Validation.** Phone must be E.164; `candidate_id` must exist in `employee.candidates`.
3. **Duplicate guard.** Insert with `status='initiating'`. A partial unique index allows only one active interview per candidate → `409` if one exists (race-free, enforced by the DB).
4. **Dial-out.** `Calls/connect.json` with `From=<candidate>`, `CallerId=<ExoPhone>`, `Url=http://my.exotel.com/<SID>/exoml/start_voice/<APP_ID>`, `Record=true`, `TimeLimit=1800`, `TimeOut=30`, `StatusCallback=<public>/…/webhooks/exotel?token=…` (terminal events only). Failure → row `failed` + `502`.
5. **Ringing.** Call SID saved, `status='ringing'`.
6. **Answer.** Exotel runs the flow; the Voicebot applet opens `wss://<host>/ws`.
7. **Linking.** `create_transport()` reads the Exotel `start` message. `find_for_call()` matches by call SID, else by the last 10 phone digits of an active interview created in the last 15 minutes. Row → `in_progress`, `started_at` set. No match → the bot still talks but nothing is saved.
8. **Interview.** A developer message "candidate has answered, greet them" starts the LLM, so the AI speaks first. The system prompt holds name/position/experience only (no contact data). Rules: one question at a time, 8–10 main questions, short spoken replies, no scores revealed, no hiring decision.
9. **Ending.** Either the LLM speaks its closing line and calls the `end_interview` tool (pushes `EndWorkerFrame`, audio flushes first), or the candidate hangs up (`on_client_disconnected` → runner cancel), or HR calls `/cancel`.
10. **Finalize.** Transcript built from the LLM context (`Interviewer:` / `Candidate:` lines). ≥1 candidate turn → `completed`; else `failed` ("ended before the candidate spoke"). Never overwrites a terminal status already set by cancel/webhook.
11. **Analysis.** Background task asks Qwen for JSON: summary, technical / communication / experience / overall scores (0–100, clamped), strengths, areas_to_verify. Stored in score columns + `analysis` JSONB. Errors stored in `error`, not raised.
12. **Webhook.** Exotel terminal callback (token-checked) stores `recording_url`, duration, `ended_at`; maps `busy` / `no-answer` / `failed` / `canceled` to statuses. A `completed` call whose row never reached `in_progress` → `failed` ("voicebot stream never connected" — usually a wrong applet URL).

### Status machine

```text
initiating → ringing → in_progress → completed
     │          │            └──────→ failed (no candidate speech)
     │          ├──→ busy / no_answer / failed / cancelled   (webhook or /cancel)
     └──→ failed (Exotel API error)
```
Active = `scheduled, initiating, ringing, answered, in_progress`. `scheduled` and `answered` are defined but **nothing sets them yet**.

---

## 5. Automatic calling and candidate reminders — current truth

> **Update 2026-11-04:** built as the HRM *Telephonic Auto Calls* module — see `07_TELEPHONIC_AUTO_CALLS.md`. The table below describes the AI-interview path only.

| Question | Answer |
| --- | --- |
| Does it call candidates automatically? | **No.** One `POST /start` = one immediate call. Nothing picks candidates from HRM, and there is no scheduler, queue or cron. |
| Is there an HRM "Start AI Interview" button? | **No.** HRM has no AI-interview code yet (by plan: wait for one real call). |
| Retries on busy / no answer? | **No.** Plan says HR-controlled retry; not built. After a terminal status a new `/start` is allowed. |
| Reminders to candidates (SMS/email/WhatsApp before the call)? | **No.** Not in code or plan docs. |
| Re-calling remaining candidates? | **No.** No batch/list mode. |

### Proposed design (not built — needs approval)

```text
HRM candidate page / bulk select
   └─▶ schedule (time slot, job) ──▶ ai_interviews status = scheduled, scheduled_at
                                       │
              reminder job (every 5 min) ── T-24h, T-1h: SMS (Exotel SMS) / email via HRM mailer
                                       │                 → ai_interview_events 'reminder_sent'
              dialer job (every 1 min) ── due & within calling hours (e.g. 10:00–19:00 IST)
                                       │   & under concurrency cap (Ollama/GPU limit, start with 1–2)
                                       └─▶ same path as /start (Exotel dial-out)
              retry rule ── busy/no_answer → re-schedule +30 min, max 2 attempts, then HR
```

Needed for it: `scheduled_at`, `attempt_no`, `next_attempt_at` columns; a single dialer loop (FastAPI lifespan task or HRM cron) using `SELECT … FOR UPDATE SKIP LOCKED`; candidate consent text in the reminder; DND/TRAI rules for transactional calls checked with Exotel.

---

## 6. API reference

All need `X-API-Key: <INTERVIEW_API_KEY>`; optional `X-Actor: <emp_id>`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | Health, model, list of missing env vars (no key) |
| POST | `/api/ai-interviews/start` (alias `/start`) | Create row + dial |
| GET | `/api/ai-interviews/{id}` | Summary + scores (no transcript, no raw recording URL) |
| GET | `/api/ai-interviews/{id}/status` | Status + error |
| GET | `/api/ai-interviews/{id}/transcript` | Transcript; logs `transcript_viewed` |
| GET | `/api/ai-interviews/{id}/recording` | Streams the Exotel recording through the service (HTTPS + `*.exotel.com/.in` only); logs `recording_viewed` |
| POST | `/api/ai-interviews/{id}/cancel` | Cancel active interview, stops the live bot |
| POST | `/api/ai-interviews/webhooks/exotel?token=` | Exotel status callback (token auth) |
| WS | `/ws` | Exotel Voicebot audio stream |

---

## 7. Database

`employee.ai_interviews` — id, candidate_id (FK `employee.candidates`), job_id, call_sid, status, phone_number, candidate_name, position, experience_years, started_by, started_at, ended_at, duration_seconds, recording_url, transcript, ai_summary, 4 score columns, analysis JSONB, error, created_at, updated_at.

`employee.ai_interview_events` — audit: created, status_changed, exotel_callback, transcript_viewed, recording_viewed, analysis_completed / failed, with actor and JSON detail.

Indexes: one-active-per-candidate (partial unique), call_sid, events by interview. Timestamps are naive UTC; local DB timezone is `Etc/UTC`, so Python `utcnow()` and `CURRENT_TIMESTAMP` agree.

---

## 8. Configuration (`my-pipecat-app/.env`)

| Variable | Local state (2026-11-04) |
| --- | --- |
| `HRM_DATABASE_URL`, `INTERVIEW_API_KEY`, `EXOTEL_WEBHOOK_TOKEN` | Set |
| `EXOTEL_API_KEY`, `EXOTEL_API_TOKEN`, `EXOTEL_PHONE_NUMBER` | Present (verify real values) |
| `EXOTEL_SID`, `EXOTEL_APP_ID`, `PUBLIC_BASE_URL` | **Placeholders** |
| `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY` | **Placeholders** |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | Defaults `127.0.0.1:11434` / `qwen3.5:9b` |
| `EXOTEL_CALL_TIME_LIMIT` / `EXOTEL_RING_TIMEOUT` | Defaults 1800 s / 30 s |

`.env` loads with `override=False` (process env wins — used to point at a mock Exotel in tests). `.env` and `.env.bak` hold secrets: keep them out of git.

### Run

```bash
cd ~/Documents/Struzon/Dev-Pro/pipecat/my-pipecat-app
uv run --active python exotel/server.py          # T1
ngrok http 7860   # or: cloudflared tunnel --url http://127.0.0.1:7860   # T2
curl http://127.0.0.1:7860/                       # missing_env must be []
```
Then set `PUBLIC_BASE_URL` and the Exotel flow's Voicebot URL to `wss://<tunnel host>/ws`, restart, and call `/start` on an authorised test number.

---

## 9. Security review

| Control | Status |
| --- | --- |
| Service auth | `X-API-Key`, constant-time compare; 503 if unset |
| Webhook auth | Secret token in callback URL (Exotel does not sign) |
| Recording | Proxied; host allow-list prevents SSRF; provider creds never leave the server |
| LLM privacy | Local Ollama; only name/position/experience in prompt; transcript sent to Deepgram, text to Cartesia (third-party cloud) |
| Audit | Every status change and every transcript/recording view logged with actor |
| Duplicates | DB-level unique index |
| Gaps | No rate limit on `/start`; `/ws` accepts any WebSocket (an unmatched stream still runs the bot and uses Deepgram/Cartesia credit); tunnel URL is public; no retention policy for recordings/transcripts; candidate consent/recording notice not in the greeting |

---

## 10. Findings

| # | Finding | Impact | Action |
| --- | --- | --- | --- |
| 1 | Analysis task created with bare `asyncio.create_task` and no reference | Task could be garbage-collected mid-run, so scores silently never saved | **Fixed** (`bot.py`: tasks held in `_analysis_tasks` until done) |
| 2 | `/ws` runs the bot even when no interview matches | Cost + abuse via a leaked tunnel URL | Recommend: close the socket when `find_for_call` returns nothing |
| 3 | `active_runners` is in-process | `/cancel` only works with one uvicorn worker | Keep single worker, or move to DB flag |
| 4 | `scheduled` / `answered` never set | Status list misleads HRM UI | Use them when scheduling is built, or hide |
| 5 | Concurrency unbounded | Several simultaneous calls on one local Qwen will raise reply latency | Cap concurrent interviews (start 1–2) |
| 6 | Recording consent not announced | Legal/HR policy | Add a line to the greeting — HR to approve wording |
| 7 | Transcript = STT text, not verbatim | Mishears go into scores | HR reviews recording for borderline cases |
| 8 | No real end-to-end call yet | Unknown real-world latency/quality | First milestone below |

---

## 11. Next steps (in order)

1. Fill Deepgram, Cartesia, Exotel SID/App ID keys; start tunnel; set `PUBLIC_BASE_URL` + applet URL.
2. Seed one test candidate locally (candidates table is empty) and run one real call to an authorised number.
3. Check: greeting latency, barge-in, `end_interview` hang-up, webhook recording, analysis row.
4. HRM: backend proxy (`/api/ai-interviews/*` → service, API key server-side) + role-scoped candidate-page panel (HR only — see cross-app RBAC rules).
5. Then the scheduler / reminders / retries in §5, after approval of calling hours, retry policy and reminder channel.
6. Production: run on a GPU host, fixed domain + TLS, recording storage and retention, monitoring.

AI scores support HR review only; hiring decisions stay with HR.
