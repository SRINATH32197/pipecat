# HRM Integration Plan

## Goal

Connect the AI phone interview service to Struzon HRM.

HR should be able to:

1.  Select a candidate.
2.  Start an AI interview.
3.  See call status.
4.  See completed transcript.
5.  See recording.
6.  See structured AI analysis.

## Flow

``` text
HRM Candidate
    ↓
Start AI Interview
    ↓
HRM Backend
    ↓
AI Interview API
    ↓
Create interview record
    ↓
Exotel
    ↓
Candidate phone
    ↓
Pipecat
    ↓
Deepgram
    ↓
Ollama Qwen 3.5 9B
    ↓
Cartesia
    ↓
Interview completed
    ↓
Transcript + recording + analysis
    ↓
HRM
```

## Start Endpoint

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

## Service Responsibilities

The AI Interview Service should:

-   validate the candidate;
-   validate the phone number;
-   prevent duplicate active interviews;
-   create the interview session;
-   initiate Exotel;
-   store call SID;
-   update status;
-   run Pipecat;
-   finalize transcript;
-   trigger analysis;
-   return results to HRM.

## Database

Recommended table:

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

Adapt to existing HRM database conventions.

## Recommended APIs

``` text
POST /api/ai-interviews/start
GET  /api/ai-interviews/:id
GET  /api/ai-interviews/:id/status
GET  /api/ai-interviews/:id/transcript
GET  /api/ai-interviews/:id/recording
POST /api/ai-interviews/:id/cancel
```

Provider callback:

``` text
POST /api/ai-interviews/webhooks/exotel
```

## Candidate Context

Pass only interview-relevant information to Qwen:

``` text
Candidate: Candidate Name
Position: Senior Tekla Modeler
Experience: 5 years
```

Then instruct:

``` text
Conduct a structured professional interview.
Ask one question at a time.
Ask relevant follow-ups.
Keep spoken responses short.
```

## Transcript

Maintain the conversation context during the call.

At completion:

``` text
candidate speech transcript
+
assistant responses
=
final transcript
```

Store it against `ai_interviews.id`.

## Recording

If Exotel recording is enabled, save the recording reference.

Expose it through an authorized HRM endpoint instead of exposing
provider credentials or unrestricted URLs.

## Analysis

After the interview:

``` text
Transcript
 ↓
Analysis model
 ↓
Structured result
 ↓
HRM
```

Example:

``` json
{
  "summary": "Candidate has relevant project experience...",
  "technical_score": 82,
  "communication_score": 76,
  "experience_score": 88,
  "strengths": [
    "Strong Tekla exposure"
  ],
  "areas_to_verify": [
    "Advanced connection detailing"
  ]
}
```

Analysis should support HR review rather than automatically making the
final hiring decision.

## HRM UI

Candidate page:

``` text
[ Start AI Interview ]
```

Status:

``` text
Scheduled
Calling
Answered
Interview in Progress
Completed
No Answer
Failed
```

Completed page:

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

## Duplicate Protection

Before starting:

``` text
candidate has active interview?
```

If yes, do not initiate another call.

## Retry

``` text
no_answer → HR-controlled retry
busy      → HR-controlled retry
failed    → investigate / controlled retry
completed → no automatic retry
```

## Audit

Record:

``` text
who started interview
when it started
candidate ID
job ID
call SID
status changes
who viewed transcript
who viewed recording
```

## Production Architecture

``` text
React HRM
    |
    v
Node/Express HRM API
    |
    v
AI Interview Service
    |
    +---- Exotel
    +---- Pipecat
    +---- Ollama
    +---- Deepgram
    +---- Cartesia
    +---- PostgreSQL
    +---- Object Storage
```

The React frontend should never directly call Exotel.
