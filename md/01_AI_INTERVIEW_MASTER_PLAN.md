# Struzon AI Interview Agent --- Master Plan

## 1. Goal

Build an automated AI phone-interview system inside the Struzon HRM.

Target flow:

``` text
HRM candidate
  ↓
Start AI Interview
  ↓
Interview API
  ↓
Exotel outbound call
  ↓
Candidate phone
  ↓
Exotel bidirectional WebSocket
  ↓
Pipecat
  ↓
Deepgram STT
  ↓
Ollama / Qwen 3.5 9B
  ↓
Cartesia TTS
  ↓
Candidate hears AI
  ↓
Interview completes
  ↓
Transcript + recording + analysis
  ↓
HRM
```

## 2. Technology

  Layer                 Technology
  --------------------- --------------------------------------------
  HR system             Existing Struzon HRM
  Interview API         Python FastAPI
  Voice orchestration   Pipecat
  Telephony             Exotel
  STT                   Deepgram
  Local LLM             Ollama
  Model                 Qwen 3.5 9B
  TTS                   Cartesia
  Development tunnel    ngrok
  Database              Existing HRM PostgreSQL / project database

## 3. Current Environment

Project:

``` text
/home/srinath/Documents/Struzon/Dev-Pro/pipecat/my-pipecat-app
```

Pipecat repository:

``` text
/home/srinath/Documents/Struzon/Dev-Pro/pipecat
```

Python:

``` text
3.13.15
```

Pipecat:

``` text
0.0.0.dev13452
```

Ollama:

``` text
http://127.0.0.1:11434
```

Pipecat:

``` text
http://localhost:7860
```

Selected model:

``` text
qwen3.5:9b
```

## 4. Completed

### Pipecat

-   Local repository available.
-   Virtual environment works.
-   Pipecat imports successfully.
-   Pipecat HTTP server starts on port 7860.
-   `exotel/bot.py` passes Python syntax compilation.

### Ollama

Ollama is running locally.

Verified models include:

``` text
qwen3:8b
qwen3.5:9b
gemma4:latest
gemma3:4b
mistral:latest
```

The selected interview model is `qwen3.5:9b`.

The OpenAI-compatible API was verified:

``` bash
curl http://127.0.0.1:11434/v1/models
```

### Qwen interview test

Qwen 3.5 9B has already been tested with an interview prompt and
generated a relevant technical follow-up question.

### Exotel code

The project already contains:

``` text
exotel/bot.py
exotel/server.py
```

The server contains outbound-call and WebSocket handling.

## 5. Current Architecture

``` text
                         STRUZON HRM
                              |
                              | Start AI Interview
                              v
                    FastAPI Interview API
                              |
                              v
                       Exotel Connect
                              |
                              v
                    Candidate Mobile Phone
                              |
                              | bidirectional audio
                              v
                       Exotel WebSocket
                              |
                              v
                       Pipecat Pipeline
                              |
                    +---------+---------+
                    |                   |
                    v                   v
               Deepgram STT       conversation
                    |
                    v
             candidate transcript
                    |
                    v
             Ollama Qwen 3.5 9B
                    |
                    v
              AI response
                    |
                    v
              Cartesia TTS
                    |
                    v
               Exotel audio
                    |
                    v
                 Candidate
```

## 6. Interview Behavior

The AI should:

1.  Greet the candidate.
2.  Explain that it is conducting the interview.
3.  Ask one question at a time.
4.  Wait for each answer.
5.  Ask natural follow-ups.
6.  Focus on real experience.
7.  Ask job-specific technical questions.
8.  Keep spoken responses short.
9.  Avoid markdown and special formatting.
10. Avoid multiple questions in one response.
11. Avoid repeating answered questions.
12. Close professionally.
13. Tell the candidate HR will review the interview.

The AI should not make the final hiring decision.

## 7. Interview Data

Minimum:

``` text
interview_id
candidate_id
job_id
call_sid
phone_number
status
started_at
ended_at
duration
recording_url
transcript
ai_summary
technical_score
communication_score
experience_score
overall_score
created_at
updated_at
```

## 8. Interview Status

``` text
scheduled
initiating
ringing
answered
in_progress
completed
no_answer
busy
failed
cancelled
```

## 9. Pending P0 Work

These are required before the first real candidate call:

-   Confirm the exact Ollama constructor/settings for the installed
    Pipecat dev build.
-   Verify `.env`.
-   Install/configure ngrok.
-   Obtain a public HTTPS/WSS endpoint.
-   Set `PUBLIC_BASE_URL`.
-   Verify Exotel credentials.
-   Verify ExoPhone / Caller ID.
-   Confirm Exotel streaming configuration.
-   Start `exotel/server.py`.
-   Trigger one controlled call.
-   Confirm Exotel connects to `/ws`.
-   Confirm Deepgram receives speech.
-   Confirm Qwen receives transcript.
-   Confirm Cartesia returns speech.

## 10. P1 Work

After the voice loop works:

-   Candidate/job metadata.
-   Structured interview plans.
-   Session persistence.
-   Transcript persistence.
-   Recording persistence.
-   Call status callbacks.
-   Result analysis.
-   HRM API.
-   HRM interview-result page.

## 11. P2 Production Hardening

-   HRM authentication.
-   Provider webhook validation.
-   Rate limiting.
-   Retry rules.
-   Call timeout handling.
-   Duplicate interview prevention.
-   Secure recording storage.
-   Audit logs.
-   Monitoring.
-   Production deployment.

## 12. HRM Integration

Recommended HRM endpoint:

``` http
POST /api/ai-interviews/start
```

Request:

``` json
{
  "candidate_id": 12345,
  "job_id": 25,
  "candidate_name": "Candidate Name",
  "phone_number": "+919876543210",
  "position": "Senior Tekla Modeler",
  "experience_years": 5
}
```

Response:

``` json
{
  "interview_id": 9001,
  "call_sid": "xxxxx",
  "status": "initiating"
}
```

The HRM backend should call the Interview Service. The React frontend
should not call Exotel directly.

## 13. Recommended Database

``` sql
CREATE TABLE ai_interviews (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT NOT NULL,
    job_id BIGINT,
    call_sid VARCHAR(255),
    status VARCHAR(50) NOT NULL,
    phone_number VARCHAR(30),
    started_at TIMESTAMP,
    ended_at TIMESTAMP,
    duration_seconds INTEGER,
    recording_url TEXT,
    transcript TEXT,
    ai_summary TEXT,
    technical_score NUMERIC(5,2),
    communication_score NUMERIC(5,2),
    experience_score NUMERIC(5,2),
    overall_score NUMERIC(5,2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Adapt this to existing HRM migrations/naming.

## 14. HRM UI

Candidate page:

``` text
Candidate
-------------------------------
Name
Phone
Position
Experience

[ Start AI Interview ]
```

After completion:

``` text
AI Interview
-------------------------------
Status: Completed
Duration: 18:32

Technical Score
Communication Score
Experience Score

Summary

[ View Transcript ]
[ View Recording ]
```

## 15. Final Definition of Done

``` text
HRM
 ↓
Start AI Interview
 ↓
Exotel calls candidate
 ↓
Candidate answers
 ↓
Pipecat receives audio
 ↓
Deepgram transcribes
 ↓
Qwen 3.5 9B responds
 ↓
Cartesia speaks
 ↓
Candidate hears response
 ↓
Interview completes
 ↓
Transcript stored
 ↓
Recording stored
 ↓
Analysis stored
 ↓
HRM displays result
```
