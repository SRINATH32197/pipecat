# Telephonic Auto Calls — How It Works and How It Is Integrated

Date: 2026-11-04 · Status: built and tested end-to-end against a mock Exotel; **no real phone call yet**.

---

## 1. What it does

HR picks candidates in HRM, ticks the questions the AI agent must ask, and books the calls. The agent phones each candidate, delivers HR's message, asks the questions one at a time, and records the answers. Each answer is then sent as a bell to the department that acts on it.

| Example question | Answer recorded | Who is informed |
| --- | --- | --- |
| Which date suits you for the interview? | `2026-11-07` | HR |
| What time will you reach our office? | `10:30` | HR + IT |
| How will you travel to our office? | `Own two-wheeler` | Facility |
| Will you need lunch? / Veg or non-veg? | `yes` / `Vegetarian` | Facility (only when *yes*) |
| Own laptop or should we arrange a system? | `Arrange a system` | IT |
| Will you bring resume + photo ID? | `yes` | HR |
| Can you confirm you will attend? | `yes` / `no` | HR |
| Notice period? | `30 days` | HR |

The questions, their wording, answer type, options and departments are all **editable in HRM** (Questions & departments tab). New questions can be added without code.

### The HR page

| Part | What it shows / does |
| --- | --- |
| Summary strip | Scheduled (and due now), live, completed today, not reached, 30-day answer rate, visits this week; each card opens the matching list |
| Candidates | Filter by call state (never called / call booked / not reached / answered) and stage; per-row **Call** or multi-select **Call selected** |
| Booking window | Purpose cards, questions with the departments they inform, message, **preview of what the agent will say**, time, attempts, reminder / apply-date |
| Calls | Status chips, answer progress bar, **Now** (dial ASAP), **Move** (reschedule), Cancel, Retry, CSV export; refreshes every 10 s |
| Call detail | Timeline (booked → started → ended → departments informed), answers grouped by department, conversation as chat bubbles, recording link, history |
| Visitor board | Interviews per day with arrival, attendance, travel, lunch, system and documents; per-day totals (lunches veg/non-veg, systems, travel modes, not confirmed); **Remind** books a reminder call; CSV export |
| Questions & departments | Edit the question catalog and routing |

### Three call purposes

| Purpose | Use | Pre-ticked questions |
| --- | --- | --- |
| Schedule interview | Agree the date, collect visit details | date, arrival, travel, lunch, food, system, documents |
| Interview reminder | Day-before reminder + confirm visit | attend, arrival, travel, lunch, food |
| Follow-up | General follow-up | attend, notice period |

### Automatic behaviour

| Feature | Rule (defaults — pending approval) |
| --- | --- |
| Auto dial | Scheduler checks every **60 s** and dials calls that are due |
| Calling hours | **10:00–19:00 IST, Mon–Sat** (`TELE_CALL_HOURS`); calls booked outside wait |
| Concurrency | **1 call at a time** (`TELE_CALL_MAX_CONCURRENT`) — one local Qwen model |
| Retry | No answer / busy / failed → again after **30 min**, up to **3 attempts** (1–5 per booking) |
| Gave up | After the last attempt HR gets a bell: "not reached after N attempts" |
| Call-back | Candidate says "call me later" → agent records the time → a follow-up call is booked for then with only the unanswered questions |
| Auto reminder | After a schedule call with a date, a reminder call is booked for the **day before at 10:30** (or in 10 min if that has passed and the interview is 3 h+ away). Skipped if the candidate said they will not attend |
| Apply date | Optional: write the agreed date/time into the candidate's `interview_datetime`; also a manual button |
| Offered slots | If HR already offered slots (`interview_datetime` JSON array), the agent offers those first |
| Duplicates | One open call per candidate (DB unique index) |
| Safety | **Nothing is dialled unless `AI_CALL_SERVICE_URL` and `AI_CALL_API_KEY` are set** on the HRM server |

---

## 2. Architecture

```text
┌──────────────────────── Struzon HRM (Node/Express + React) ────────────────────────┐
│  /telephonic-calls page (HR / admin only)                                          │
│    Candidates tab ─ pick ─ "Call selected" ─ purpose, questions, message, time     │
│    Calls tab      ─ status, attempts, answers, transcript, history, retry/cancel   │
│    Questions tab  ─ wording, answer type, options, departments, pre-tick rules     │
│                                                                                    │
│  backend/src/routes/telephonicCalls.ts                                             │
│    API  /api/telephonic-calls/*            (requireAuth + recruiter roles)          │
│    Scheduler (every 60 s) ── 1 dispatch completed → bells / call-back / reminder   │
│                            ── 2 retry or give up                                   │
│                            ── 3 fail stale calls                                   │
│                            ── 4 dial due calls ─────────────┐                      │
└─────────────────────────────────────────────────────────────┼──────────────────────┘
                     PostgreSQL (HRM ems_db, schema employee) │ POST /api/telephonic-calls/:id/dial
                     tele_calls · tele_call_questions ·       │ X-API-Key
                     tele_call_log · notifications            ▼
┌──────────────── AI call service (Dev-Pro/pipecat/my-pipecat-app/exotel, :7860) ────┐
│  server.py  dial → Exotel Calls/connect (same flow as AI interviews)               │
│             webhook → busy / no_answer / recording / duration                      │
│             /ws → bot.py decides: telephonic call or AI interview                  │
│  tele.py    prompt from the call row → Pipecat pipeline (shared run_call):         │
│             Deepgram STT → Ollama qwen3.5:9b → Cartesia TTS                         │
│             tools: record_answer(key, answer) · end_call                            │
│             after the call: transcript + extract missed answers (Ollama JSON)      │
│             → writes answers/status into employee.tele_calls                       │
└────────────────────────────────────────────────────────────────────────────────────┘
                     ▲ audio (8 kHz WebSocket)
                Exotel ⇄ candidate's phone
```

**Ownership split** — HRM owns business rules (who, what, when, who is informed); the Pipecat service only dials and talks. The service writes results straight into the HRM tables; HRM's scheduler picks them up within a minute (an outbox pattern), so no callback from Python to Node is needed and a restart of either side loses nothing.

---

## 3. Life of one call

```text
HR books ──▶ scheduled ──(due, in hours, slot free)──▶ dialing ──Exotel accepted──▶ ringing
                                                          │                          │
                                         service down ────┘ failed          answered │ /ws
                                                                                     ▼
                                     no answer / busy (webhook) ◀──── ringing   in_progress
                                                                                     │ end_call / hang-up
                                                                                     ▼
                                                                                 completed
                                                                                     │ scheduler
                                                                    bells per department, apply date,
                                                                    call-back or reminder call booked
no_answer / busy / failed ──attempts left──▶ scheduled (+30 min)
                          └─no attempts────▶ HR bell "not reached"
cancelled ◀── HR Cancel (live call is hung up)
```

Step by step:

1. **Book** — `POST /api/telephonic-calls` with candidate ids, purpose, question keys, message, time (IST) and options. Phone numbers are normalised (`98765 00001` → `+919876500001`); candidates without a valid number are skipped and listed. The chosen questions are **snapshotted** into the call, so later catalog edits do not change booked calls.
2. **Dial** — the scheduler claims due calls (`FOR UPDATE SKIP LOCKED`), sets `dialing`, attempts + 1, and calls the service. The service dials through Exotel (`Calls/connect`, the same Voicebot flow as the AI interviews) and sets `ringing` + call SID.
3. **Talk** — Exotel streams audio to `/ws`. `bot.py` matches the stream to the call by SID (or phone within 15 min) and runs `tele.run_tele_call`. The prompt contains: candidate name, position, purpose, HR's message, today's date in IST, offered slots / current interview time, and the numbered question list with answer formats. The agent greets, asks if it is a good time, asks one question at a time and saves each answer with `record_answer` as it goes (so a dropped call keeps partial answers).
4. **Finish** — on `end_call` or hang-up: transcript saved; any question not recorded by the tool is extracted from the transcript by Qwen (JSON schema, temperature 0, "only what the candidate said"); status `completed` (or `failed` if the candidate never spoke).
5. **Act** — within 60 s HRM claims the completed call once (`notified_at`) and sends one bell per department with every answer that department acts on, e.g.
   `Telephonic call #12 · Ravi K (Tekla Modeler) · interview 7 Nov 10:30 — Travel mode: Own two-wheeler; Lunch needed: yes; Food preference: Vegetarian` → Facility.
   Then: apply date (if ticked), book a call-back or a reminder call.
6. **Unanswered** — the Exotel status webhook sets `no_answer` / `busy`; the scheduler re-queues it 30 min later until attempts run out, then bells HR.

### How departments are resolved

| Department | Recipients |
| --- | --- |
| HR | every HR login (`notifyHr`) |
| IT | every IT login and IT team member (`notifyIt`) |
| Any other (Facility, Accounts, …) | active employees whose `emp_team` is that name, or who hold a seat on that admin team (`admin_team_manager_map`) |
| Department with nobody in it | HR gets the bell with "[no Facility member found — please pass on]" |

The department list on the Questions tab is HR + IT + Facility + every active team in `admin_team_manager_map`.

---

## 4. Files

### HRM (`Dev-Pro/Struzon_hrm-main`)

| File | Change |
| --- | --- |
| `backend/src/routes/telephonicCalls.ts` | **New** — tables, default questions, API, scheduler, department bells, call-back/reminder booking |
| `backend/src/index.ts` | Mounts `/api/telephonic-calls` (behind `requireAuth`), starts the scheduler |
| `backend/.env.example` | `AI_CALL_SERVICE_URL`, `AI_CALL_API_KEY`, `TELE_CALL_HOURS`, `TELE_CALL_MAX_CONCURRENT` |
| `frontend/src/pages/TelephonicCalls.tsx` | **New** — the three-tab page |
| `frontend/src/App.tsx` | Route `/telephonic-calls` |
| `frontend/src/data/nav.ts` | Recruitment → "Telephonic Auto Calls" (all three Recruitment menus); roles hrm, admin, super_admin, sub_admin (read-only), developer |
| `frontend/src/components/layout/NotificationPanel.tsx` | Bells starting "Telephonic call #" open the page |

### AI call service (`Dev-Pro/pipecat/my-pipecat-app/exotel`)

| File | Change |
| --- | --- |
| `tele.py` | **New** — call row access, prompt builder, `record_answer` / `end_call` tools, finalize, answer extraction |
| `bot.py` | Pipeline extracted into shared `run_call()` (AI interviews behave as before); `/ws` streams routed to telephonic calls first by SID |
| `server.py` | `POST /api/telephonic-calls/{id}/dial`, `POST /api/telephonic-calls/{id}/stop`; Exotel webhook also matches telephonic calls |

---

## 5. Data model (HRM `employee` schema)

**`tele_call_questions`** — catalog: `key` (PK), `label`, `prompt`, `answer_type` (`yes_no | date | time | datetime | choice | text`), `options` jsonb, `notify` jsonb (departments), `notify_when` (`any | yes`), `default_for` jsonb (purposes), `active`, `sort`.

**`tele_calls`** — one row per call attempt series: `candidate_id` (FK candidates), `purpose`, `phone` (E.164), `candidate_name`, `position`, `message`, `questions` (snapshot), `context` (offered slots / interview time), `scheduled_at`, `status`, `attempts`, `max_attempts`, `auto_reminder`, `apply_date`, `parent_id` (reminder/call-back origin), `call_sid`, `answers` jsonb (`{key: answer}`, plus `callback_at`), `transcript`, `error`, `recording_url`, `duration_seconds`, `started_at`, `ended_at`, `notified_at`, `created_by`, timestamps. All times are `timestamptz`; the API returns IST strings.

**`tele_call_log`** — audit trail: scheduled, dialing, ringing, in_progress, completed, answers_extracted, notified, candidate_updated, reminder_booked, callback_booked, retry_scheduled, cancelled, failed … with actor and detail.

Created automatically on first use (HRM convention). Unique partial index = one open call per candidate.

---

## 6. API (HRM, logged-in HR roles)

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/telephonic-calls/options` | Questions, departments, candidate stages, service health, rules |
| GET | `/api/telephonic-calls/summary` | Counts for the summary strip (scheduled, due, live, completed today, not reached, 30-day answer rate) |
| GET | `/api/telephonic-calls/visits?days=` | Visitor board: interviews from today to +days (1–90) with the merged answers of completed calls |
| GET | `/api/telephonic-calls/candidates?q=&status=&call=` | Candidates with callable phone + last call; `call` = `never`, `open`, `attention`, `completed` |
| GET | `/api/telephonic-calls?status=&q=` | Calls (`all`, `open`, `attention`, or a status) |
| GET | `/api/telephonic-calls/:id` | Call + transcript + recording URL + history |
| POST | `/api/telephonic-calls/:id/reschedule` | Move a scheduled call (`scheduled_at` IST, empty = now) |
| POST | `/api/telephonic-calls` | Book calls (≤ 200 candidates) |
| POST | `/api/telephonic-calls/:id/cancel` | Cancel (hangs up a live call) |
| POST | `/api/telephonic-calls/:id/retry` | Re-queue an unanswered / failed / cancelled call now |
| POST | `/api/telephonic-calls/:id/apply-date` | Write the agreed slot into the candidate |
| POST / PUT | `/api/telephonic-calls/questions[/:key]` | Add / edit a question |

AI call service (called by HRM only, `X-API-Key`): `POST /api/telephonic-calls/{id}/dial`, `POST /api/telephonic-calls/{id}/stop`.

---

## 7. Setup

1. **AI call service** — as in `05_COMMAND_RUNBOOK.md`; fill Deepgram, Cartesia and real Exotel values in `my-pipecat-app/.env`; run `server.py`; expose it (ngrok / cloudflared); the Exotel flow's Voicebot URL = `wss://<public host>/ws`.
2. **HRM backend `.env`** — then restart the backend (`tsx watch` does not reload `.env`):
   ```env
   AI_CALL_SERVICE_URL=http://127.0.0.1:7860
   AI_CALL_API_KEY=<same value as INTERVIEW_API_KEY in my-pipecat-app/.env>
   # TELE_CALL_HOURS=10-19
   # TELE_CALL_MAX_CONCURRENT=1
   ```
3. **Review the questions** in HRM → Recruitment → Telephonic Auto Calls → Questions & departments (wording, departments).
4. **First real call** — book a call for a test candidate whose phone is your own authorised number; watch the Calls tab.

The page shows a warning banner when the service is not configured, offline, or missing keys.

---

## 8. Verification (2026-11-04)

End-to-end on the local DB, real Pipecat server on a test port, mock Exotel, real Ollama — 17/17 passed:

- Options, phone normalisation, booking, duplicate blocked
- Scheduler dialled through the service → mock Exotel → `ringing` + SID; offered slots carried
- Service side (`tele.py`): matched by SID, prompt built, answers merged, **Qwen extracted 4 missing answers from a transcript correctly**, status `completed`
- Scheduler: 3 department bells (HR; HR + IT; Facility), candidate slot applied, reminder booked for the day before 10:30
- Reminder dialled; Exotel webhook `no-answer` → `no_answer` → retry in 30 min
- History log, cancel, retry, question edit, validation

Test rows (candidate, calls, bells) were removed afterwards. Backend + frontend type-check; ESLint clean on the new page.

**Not verified:** a real voice call (needs Deepgram/Cartesia keys and Exotel flow), Qwen's tool-calling reliability over a live conversation (answer extraction after the call is the fallback), and voice quality/latency.

---

## 9. Open decisions (need approval)

| # | Decision | Current default |
| --- | --- | --- |
| 1 | Calling hours and days | 10:00–19:00 IST, Mon–Sat |
| 2 | Retry policy | 3 attempts, 30 min apart |
| 3 | Reminder time | Day before, 10:30 |
| 4 | Department per question | Table in §1 (arrival → HR + IT; food & travel → Facility) |
| 5 | Auto-writing the agreed date into the candidate | Off by default (checkbox / button) |
| 6 | Agent introduction + call-recording notice wording | "AI assistant from Struzon Technologies HR"; no recording notice yet |
| 7 | Email/SMS in addition to bells | Bells only |
| 8 | Concurrent calls | 1 (raise only with GPU headroom) |

## 10. Known limits

- Answers are STT text interpreted by a 9B model — HR should glance at the transcript for important ones.
- The `Yuvaraj.K.M` entry in the department list comes from a person's name stored as a team in `admin_team_manager_map` (data issue, not code).
- A wrong-but-valid-looking phone number retries until attempts run out.
- Recordings are stored as Exotel URLs on the row but not yet playable from the page.
