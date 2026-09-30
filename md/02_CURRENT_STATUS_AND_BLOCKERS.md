# Current Status and Blockers

Date: 2026-09-29

## Working

  Component                         Status
  --------------------------------- ------------------------------------------
  Python / Pipecat dev13452 / .venv Working
  Ollama + Qwen 3.5 9B              Working (thinking disabled, ~0.5 s/reply)
  Interview API (FastAPI :7860)     Implemented, tested locally
  HRM PostgreSQL persistence        `employee.ai_interviews` + events, tested
  Duplicate-interview protection    Tested (409)
  Exotel dial-out request           Tested against a mock Exotel
  Exotel status webhook             Tested (no_answer / completed / recording)
  Exotel WebSocket → bot linking    Tested with a simulated Exotel handshake
  Qwen greeting on answer           Tested
  Transcript + AI analysis          Tested with real Ollama
  Real Exotel call                  Not tested yet
  ngrok                             Not installed (dpkg lock)
  Deepgram / Cartesia keys          Missing from `.env`
  HRM backend/UI                    Not started (by plan)

## Fixed in the voice loop

-   `OLLamaLLMService.Settings(base_url=...)` crashed on this Pipecat
    build; `base_url` is a constructor argument.
-   Qwen thinking made each reply take ~43 s; now disabled
    (`reasoning_effort: none`).
-   The bot waited silently for the candidate; it now greets on answer.
-   The call never ended from the AI side; the LLM now calls an
    `end_interview` tool after its closing line.
-   Exotel `Calls/connect` needs `Url=.../exoml/start_voice/<flow id>`
    (Voicebot applet); `StreamUrl`/`StreamType` are not its parameters.
-   `/start` had no authentication and CORS `*`; now `X-API-Key`, no CORS.

## Blockers before the first real call

1.  `.env`: add `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`, `EXOTEL_APP_ID`,
    `INTERVIEW_API_KEY`, `EXOTEL_WEBHOOK_TOKEN`, `HRM_DATABASE_URL`
    (see `my-pipecat-app/.env.example`).
2.  `.env`: `EXOTEL_SID` and `EXOTEL_PHONE_NUMBER` still hold placeholder
    values (`your_account_sid`, `your_exotel_number`).
3.  Install ngrok (wait for the dpkg lock; do not delete it).
4.  Exotel flow: set the Voicebot applet URL to `wss://<ngrok host>/ws`.

## Immediate Sequence

``` text
1. Fill .env            5. POST /api/ai-interviews/start (authorised test number)
2. ngrok http 7860      6. Candidate answers → /ws → Qwen greets
3. Set PUBLIC_BASE_URL  7. Hang up → status completed, transcript, analysis
   + applet wss URL     8. Exotel webhook → recording + duration
4. Start server         9. Then connect HRM
```

## Do Not Integrate HRM UI Yet

First complete one successful real phone interview. The service already
writes to the HRM database, so HRM integration is only the backend proxy
and the candidate-page UI.
