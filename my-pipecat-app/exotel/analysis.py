"""Post-interview analysis with the local Ollama model.

Output supports HR review only; it never makes a hiring decision.
"""

import aiohttp
import json

from loguru import logger

import config
import db

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "technical_score": {"type": ["number", "null"]},
        "communication_score": {"type": ["number", "null"]},
        "experience_score": {"type": ["number", "null"]},
        "overall_score": {"type": ["number", "null"]},
        "strengths": {"type": "array", "items": {"type": "string"}},
        "areas_to_verify": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "summary",
        "technical_score",
        "communication_score",
        "experience_score",
        "overall_score",
        "strengths",
        "areas_to_verify",
    ],
}

ANALYSIS_PROMPT = """You review phone-interview transcripts for an HR team.
Assess only what the candidate actually said. Do not invent experience.
Score each dimension 0-100:
- technical_score: depth and correctness of technical answers for the position
- communication_score: clarity and structure of answers
- experience_score: relevance of real project experience to the position
- overall_score: balanced overall assessment
Summary: 3-5 sentences for HR. List strengths and areas HR should verify.
Do not recommend hire or reject; the HR team makes that decision.
Score what the candidate did say, even from a short interview; a short interview is not a low score.
Use null only when there is no evidence at all for a dimension, and say so in areas_to_verify."""


def _clamp(value) -> float | None:
    if value is None:
        return None
    try:
        return round(max(0.0, min(100.0, float(value))), 2)
    except (TypeError, ValueError):
        return None


async def analyze_transcript(transcript: str, position: str | None) -> dict:
    payload = {
        "model": config.OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "format": ANALYSIS_SCHEMA,
        "options": {"temperature": 0.2},
        "messages": [
            {"role": "system", "content": ANALYSIS_PROMPT},
            {
                "role": "user",
                "content": f"Position: {position or 'Not specified'}\n\nTranscript:\n{transcript}",
            },
        ],
    }
    timeout = aiohttp.ClientTimeout(total=600)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{config.OLLAMA_BASE_URL}/api/chat", json=payload) as resp:
            body = await resp.text()
            if resp.status != 200:
                raise RuntimeError(f"Ollama analysis error ({resp.status}): {body[:500]}")
    result = json.loads(json.loads(body)["message"]["content"])
    for key in ("technical_score", "communication_score", "experience_score", "overall_score"):
        result[key] = _clamp(result.get(key))
    result["strengths"] = [str(s) for s in result.get("strengths") or []]
    result["areas_to_verify"] = [str(s) for s in result.get("areas_to_verify") or []]
    return result


async def analyze_interview(interview_id: int) -> None:
    """Analyse a stored transcript and write the scores back. Errors are recorded, not raised."""
    row = await db.get_interview(interview_id)
    if not row or not row.get("transcript"):
        return
    try:
        result = await analyze_transcript(row["transcript"], row.get("position"))
    except Exception as e:
        logger.exception(f"Analysis failed for interview {interview_id}")
        await db.update_interview(interview_id, {"error": f"analysis: {e}"}, event="analysis_failed")
        return
    await db.update_interview(
        interview_id,
        {
            "ai_summary": result["summary"],
            "technical_score": result["technical_score"],
            "communication_score": result["communication_score"],
            "experience_score": result["experience_score"],
            "overall_score": result["overall_score"],
            "analysis": result,
        },
        event="analysis_completed",
    )
    logger.info(f"Interview {interview_id} analysed: overall {result['overall_score']}")
