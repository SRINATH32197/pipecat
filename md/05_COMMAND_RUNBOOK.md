# AI Interview Agent Command Runbook

## Project

``` bash
cd ~/Documents/Struzon/Dev-Pro/pipecat/my-pipecat-app
```

## Activate

``` bash
source .venv/bin/activate
```

## Verify Python

``` bash
python --version
```

## Verify Pipecat

``` bash
python -c "import pipecat; print('Pipecat OK')"
```

## Verify Ollama

``` bash
curl http://127.0.0.1:11434/api/tags
curl http://127.0.0.1:11434/v1/models
```

Confirm:

``` text
qwen3.5:9b
```

## Test Qwen

``` bash
ollama run qwen3.5:9b
```

## Validate Bot

``` bash
python -m py_compile exotel/bot.py
```

## Validate Server

``` bash
python -m py_compile exotel/server.py
```

## Start Server

``` bash
uv run --active python exotel/server.py
```

## Health Check

``` bash
curl http://127.0.0.1:7860/
```

## Check Port

``` bash
ss -ltnp | grep 7860
```

## Start ngrok

``` bash
ngrok http 7860
```

Set:

``` env
PUBLIC_BASE_URL=https://xxxxx.ngrok-free.app
```

Restart the server after editing `.env`.

## Test Start Endpoint

Only use an authorized test number. `candidate_id` must exist in
`employee.candidates`. `X-Actor` is recorded in the audit log.

``` bash
curl -X POST http://127.0.0.1:7860/api/ai-interviews/start \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $INTERVIEW_API_KEY" \
  -H "X-Actor: STZ000" \
  -d '{
    "candidate_id": 123,
    "job_id": 25,
    "phone_number": "+919876543210",
    "position": "Senior Tekla Modeler",
    "experience_years": 5
  }'
```

Expected successful response:

``` json
{
  "interview_id": 1,
  "call_sid": "xxxxx",
  "status": "ringing"
}
```

`409` means the candidate already has an active interview.

## Follow the Interview

``` bash
H="X-API-Key: $INTERVIEW_API_KEY"
curl -H "$H" http://127.0.0.1:7860/api/ai-interviews/1          # scores + summary
curl -H "$H" http://127.0.0.1:7860/api/ai-interviews/1/status
curl -H "$H" http://127.0.0.1:7860/api/ai-interviews/1/transcript
curl -H "$H" -o rec.mp3 http://127.0.0.1:7860/api/ai-interviews/1/recording
curl -H "$H" -X POST http://127.0.0.1:7860/api/ai-interviews/1/cancel
```

## Watch WebSocket

Server should show:

``` text
Exotel WebSocket accepted; starting Pipecat AI interview bot
Candidate connected (interview 1)
Interview 1 finalized: N candidate turns
Interview 1 analysed: overall NN
```

## Common Checks

### Ollama

``` bash
curl http://127.0.0.1:11434/api/tags
```

### Port 7860

``` bash
ss -ltnp | grep 7860
```

### Apt lock

If Kali says:

``` text
Could not get lock /var/lib/dpkg/lock-frontend
```

do not delete the lock.

Inspect:

``` bash
ps -fp <PID>
ps aux | grep -E 'apt|dpkg' | grep -v grep
```

### Credentials

Never paste these into chat or Git:

``` text
DEEPGRAM_API_KEY
CARTESIA_API_KEY
EXOTEL_API_KEY
EXOTEL_API_TOKEN
```
