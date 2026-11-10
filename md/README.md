# Struzon AI Interview Agent Documentation

Documentation status: 2026-09-29.

## Documents

-   `01_AI_INTERVIEW_MASTER_PLAN.md` --- complete architecture,
    milestones, current state, and production plan.
-   `02_CURRENT_STATUS_AND_BLOCKERS.md` --- what is working, what is
    pending, and immediate blockers.
-   `03_ENVIRONMENT_AND_CONFIGURATION.md` --- Ollama, Pipecat, Exotel,
    ngrok, `.env`, and security setup.
-   `04_HRM_INTEGRATION_PLAN.md` --- HRM API, database, UI, transcript,
    recording, analysis, and call lifecycle.
-   `05_COMMAND_RUNBOOK.md` --- commands for setup, validation, running,
    testing, and troubleshooting.
-   `06_PIPECAT_FULL_ANALYSIS.md` --- full analysis: framework, service
    architecture, call lifecycle, automation/reminder status, findings.
-   `07_TELEPHONIC_AUTO_CALLS.md` --- HRM Telephonic Auto Calls module:
    auto-dial, questions, department notifications, reminders, integration.

## Current milestone

Local Ollama + Qwen 3.5 9B is working. Pipecat is running on port 7860.

Next:

``` text
ngrok
  ↓
Exotel
  ↓
bidirectional WebSocket
  ↓
Pipecat
  ↓
Deepgram STT
  ↓
Ollama Qwen 3.5 9B
  ↓
Cartesia TTS
  ↓
candidate phone
```

After one complete phone interview works, connect it to the Struzon HRM.
