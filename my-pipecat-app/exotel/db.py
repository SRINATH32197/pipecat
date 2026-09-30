"""HRM PostgreSQL persistence for AI interviews.

Tables are created idempotently at service start, following the HRM convention
of each module owning its `CREATE TABLE IF NOT EXISTS` in the `employee` schema.
"""

import json
from datetime import datetime, timezone
from typing import Any

import asyncpg

import config

ACTIVE_STATUSES = ("scheduled", "initiating", "ringing", "answered", "in_progress")
TERMINAL_STATUSES = ("completed", "no_answer", "busy", "failed", "cancelled")

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS employee.ai_interviews (
    id BIGSERIAL PRIMARY KEY,
    candidate_id BIGINT NOT NULL REFERENCES employee.candidates(id),
    job_id BIGINT,
    call_sid VARCHAR(255),
    status VARCHAR(50) NOT NULL,
    phone_number VARCHAR(30),
    candidate_name TEXT,
    position TEXT,
    experience_years NUMERIC(4,1),
    started_by TEXT,
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
    analysis JSONB,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Duplicate protection: at most one active interview per candidate, race-free.
CREATE UNIQUE INDEX IF NOT EXISTS ai_interviews_one_active_per_candidate
    ON employee.ai_interviews (candidate_id)
    WHERE status IN ({", ".join(f"'{s}'" for s in ACTIVE_STATUSES)});

CREATE INDEX IF NOT EXISTS ai_interviews_call_sid ON employee.ai_interviews (call_sid);

CREATE TABLE IF NOT EXISTS employee.ai_interview_events (
    id BIGSERIAL PRIMARY KEY,
    interview_id BIGINT NOT NULL REFERENCES employee.ai_interviews(id) ON DELETE CASCADE,
    event VARCHAR(50) NOT NULL,
    actor TEXT,
    detail JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ai_interview_events_interview
    ON employee.ai_interview_events (interview_id);
"""

_pool: asyncpg.Pool | None = None


def utcnow() -> datetime:
    """Naive UTC, matching the DB's CURRENT_TIMESTAMP defaults (server runs in UTC)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def connect() -> None:
    global _pool
    _pool = await asyncpg.create_pool(config.required("HRM_DATABASE_URL"), min_size=1, max_size=5)
    async with _pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)


async def close() -> None:
    if _pool:
        await _pool.close()


def pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Database not connected")
    return _pool


def _row(record: asyncpg.Record | None) -> dict[str, Any] | None:
    if record is None:
        return None
    row = dict(record)
    if isinstance(row.get("analysis"), str):
        row["analysis"] = json.loads(row["analysis"])
    return row


async def log_event(interview_id: int, event: str, actor: str | None = None, detail: Any = None):
    await pool().execute(
        "INSERT INTO employee.ai_interview_events (interview_id, event, actor, detail) "
        "VALUES ($1, $2, $3, $4::jsonb)",
        interview_id,
        event,
        actor,
        json.dumps(detail) if detail is not None else None,
    )


async def get_candidate(candidate_id: int) -> dict | None:
    return _row(
        await pool().fetchrow(
            "SELECT id, candidate_name, phone_no FROM employee.candidates WHERE id = $1",
            candidate_id,
        )
    )


async def create_interview(data: dict, actor: str | None) -> dict:
    """Insert a new interview in `initiating`. Raises UniqueViolationError if one is active."""
    row = _row(
        await pool().fetchrow(
            """
            INSERT INTO employee.ai_interviews
                (candidate_id, job_id, status, phone_number, candidate_name, position,
                 experience_years, started_by)
            VALUES ($1, $2, 'initiating', $3, $4, $5, $6, $7)
            RETURNING *
            """,
            data["candidate_id"],
            data.get("job_id"),
            data["phone_number"],
            data.get("candidate_name"),
            data.get("position"),
            data.get("experience_years"),
            actor,
        )
    )
    await log_event(row["id"], "created", actor, {"status": "initiating"})
    return row


async def get_interview(interview_id: int) -> dict | None:
    return _row(
        await pool().fetchrow("SELECT * FROM employee.ai_interviews WHERE id = $1", interview_id)
    )


async def find_for_call(call_sid: str | None, phone_numbers: list[str]) -> dict | None:
    """Match an Exotel stream to its interview: by call SID, else by recent active phone."""
    if call_sid:
        row = await pool().fetchrow(
            "SELECT * FROM employee.ai_interviews WHERE call_sid = $1 ORDER BY id DESC LIMIT 1",
            call_sid,
        )
        if row:
            return _row(row)
    tails = [p[-10:] for p in phone_numbers if p and len(p) >= 10]
    if not tails:
        return None
    return _row(
        await pool().fetchrow(
            """
            SELECT * FROM employee.ai_interviews
            WHERE status = ANY($1::text[])
              AND right(regexp_replace(phone_number, '\\D', '', 'g'), 10) = ANY($2::text[])
              AND created_at > CURRENT_TIMESTAMP - INTERVAL '15 minutes'
            ORDER BY id DESC LIMIT 1
            """,
            list(ACTIVE_STATUSES),
            tails,
        )
    )


async def update_interview(
    interview_id: int,
    fields: dict[str, Any],
    *,
    only_if_active: bool = False,
    actor: str | None = None,
    event: str | None = None,
) -> dict | None:
    """Update columns; with only_if_active, never overwrite a terminal status.

    Returns the updated row, or None when nothing matched (RETURNING, not rowCount).
    """
    if not fields:
        return await get_interview(interview_id)
    fields = dict(fields)
    if "analysis" in fields and not isinstance(fields["analysis"], str):
        fields["analysis"] = json.dumps(fields["analysis"])
    cols = list(fields)
    sets = ", ".join(
        f"{c} = ${i + 2}{'::jsonb' if c == 'analysis' else ''}" for i, c in enumerate(cols)
    )
    sql = (
        f"UPDATE employee.ai_interviews SET {sets}, updated_at = CURRENT_TIMESTAMP "
        f"WHERE id = $1"
    )
    args: list[Any] = [interview_id, *fields.values()]
    if only_if_active:
        sql += f" AND status = ANY(${len(args) + 1}::text[])"
        args.append(list(ACTIVE_STATUSES))
    row = _row(await pool().fetchrow(sql + " RETURNING *", *args))
    if row and (event or "status" in fields):
        detail = {k: v for k, v in fields.items() if k in ("status", "call_sid", "error")}
        await log_event(interview_id, event or "status_changed", actor, detail or None)
    return row
