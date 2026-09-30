# Environment and Configuration

## Project

``` bash
cd ~/Documents/Struzon/Dev-Pro/pipecat/my-pipecat-app
```

## Python

``` bash
source .venv/bin/activate
python --version
```

## Ollama

Check:

``` bash
curl http://127.0.0.1:11434/api/tags
curl http://127.0.0.1:11434/v1/models
```

Selected model:

``` text
qwen3.5:9b
```

Run:

``` bash
ollama run qwen3.5:9b
```

## `.env`

Template: `my-pipecat-app/.env.example` (all variables, no secrets).

Required for live calls (the health check `GET /` lists any missing):

``` text
PUBLIC_BASE_URL        https://<ngrok host>
INTERVIEW_API_KEY      shared secret; HRM backend sends it as X-API-Key
EXOTEL_WEBHOOK_TOKEN   secret in the Exotel StatusCallback URL
HRM_DATABASE_URL       HRM PostgreSQL (local: 127.0.0.1:5438/ems_db)
EXOTEL_API_KEY / EXOTEL_API_TOKEN / EXOTEL_SID / EXOTEL_PHONE_NUMBER
EXOTEL_APP_ID          flow containing the Voicebot applet
DEEPGRAM_API_KEY / CARTESIA_API_KEY
```

Process environment variables take precedence over `.env`.

Do not commit `.env`.

`.gitignore`:

``` gitignore
.env
.venv/
__pycache__/
```

## Pipecat

Run:

``` bash
uv run --active python exotel/server.py
```

Expected:

``` text
Uvicorn running on http://0.0.0.0:7860
```

Health check:

``` bash
curl http://127.0.0.1:7860/
```

## ngrok

Development:

``` bash
ngrok http 7860
```

Copy the HTTPS URL:

``` text
https://example.ngrok-free.app
```

Set:

``` env
PUBLIC_BASE_URL=https://example.ngrok-free.app
```

The server uses:

``` text
https://example.ngrok-free.app/ws
```

as the public WebSocket path.

## Exotel

Server-side credentials:

``` text
EXOTEL_API_KEY
EXOTEL_API_TOKEN
EXOTEL_SID
EXOTEL_PHONE_NUMBER
```

Outbound flow: `Calls/connect.json` with
`Url=http://my.exotel.com/<SID>/exoml/start_voice/<EXOTEL_APP_ID>`.
When the candidate answers, Exotel runs that flow. Its Voicebot applet
must be set to:

``` text
wss://<ngrok host>/ws
```

Update the applet whenever the ngrok URL changes. Exotel posts the
terminal call status (recording URL, duration) to
`/api/ai-interviews/webhooks/exotel?token=<EXOTEL_WEBHOOK_TOKEN>`.

## Database

On start the service creates, if missing, in HRM PostgreSQL:

-   `employee.ai_interviews`: one row per call (FK to
    `employee.candidates`; a unique partial index allows one active
    interview per candidate).
-   `employee.ai_interview_events`: audit trail (created, status
    changes, transcript/recording views, with `X-Actor`).

Timestamps are UTC.

## Security

Never expose provider keys to:

-   React
-   browser JavaScript
-   mobile clients
-   Git
-   candidate prompts

## Production

Replace ngrok with:

``` text
real HTTPS/WSS domain
+
reverse proxy/load balancer
+
TLS
+
authentication
+
monitoring
```

Keep Ollama private behind the AI Interview Service.
