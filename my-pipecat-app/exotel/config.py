"""Environment-driven settings for the AI interview service."""

import os

from dotenv import load_dotenv

load_dotenv(override=False)


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def required(name: str) -> str:
    value = env(name)
    if not value:
        raise ValueError(f"Missing required environment variable: {name}")
    return value


# Server
HOST = env("HOST", "0.0.0.0")
PORT = int(env("PORT", "7860"))
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL").rstrip("/")

# Service-to-service auth: the HRM backend sends X-API-Key. Webhooks carry ?token=.
INTERVIEW_API_KEY = env("INTERVIEW_API_KEY")
EXOTEL_WEBHOOK_TOKEN = env("EXOTEL_WEBHOOK_TOKEN")

# HRM PostgreSQL (tables live in the `employee` schema, like the rest of HRM)
HRM_DATABASE_URL = env("HRM_DATABASE_URL")

# Exotel
EXOTEL_API_BASE = env("EXOTEL_API_BASE", "https://api.in.exotel.com").rstrip("/")
EXOTEL_CALL_TIME_LIMIT = env("EXOTEL_CALL_TIME_LIMIT", "1800")
EXOTEL_RING_TIMEOUT = env("EXOTEL_RING_TIMEOUT", "30")
# Recording URLs arrive via webhook; only these hosts are fetched (SSRF guard).
RECORDING_HOST_SUFFIXES = (".exotel.com", ".exotel.in")

# LLM (local Ollama, OpenAI-compatible endpoint)
OLLAMA_BASE_URL = env("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = env("OLLAMA_MODEL", "qwen3.5:9b")

# Speech
DEEPGRAM_API_KEY = env("DEEPGRAM_API_KEY")
CARTESIA_API_KEY = env("CARTESIA_API_KEY")
CARTESIA_VOICE_ID = env("CARTESIA_VOICE_ID", "71a7ad14-091c-4e8e-a314-022ece01c121")

REQUIRED_FOR_CALLS = (
    "HRM_DATABASE_URL",
    "INTERVIEW_API_KEY",
    "EXOTEL_WEBHOOK_TOKEN",
    "PUBLIC_BASE_URL",
    "EXOTEL_API_KEY",
    "EXOTEL_API_TOKEN",
    "EXOTEL_SID",
    "EXOTEL_PHONE_NUMBER",
    "EXOTEL_APP_ID",
    "DEEPGRAM_API_KEY",
    "CARTESIA_API_KEY",
)


def missing_env() -> list[str]:
    return [name for name in REQUIRED_FOR_CALLS if not env(name)]
