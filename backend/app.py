"""
FastAPI Application – Oracle DB Provisioning Agent
====================================================
Routes:
  POST /api/provision        – launch a seed or clone job (SSE stream)
  GET  /api/jobs             – list all jobs (queue state)
  GET  /api/jobs/{job_id}    – single job detail
  GET  /api/health           – Docker container health check
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .docker_controller import DockerController, DockerExecutionError
from .validation_engine import ProvisionRequest, validate_provision_request
from .workflows import (
    apply_post_provision_parameters,
    clone_database,
    seed_database,
    verify_parameters,
    verify_rman_catalog_registration,
)

# ─────────────────────────── logging ─────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("oracle_provisioner")

# ─────────────────────────── in-memory job store ─────────────────────────────

JobStatus = Literal["pending", "running", "completed", "failed"]


class Job(BaseModel):
    job_id: str
    db_name: str
    db_unique_name: str
    provisioning_type: str
    status: JobStatus
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    logs: list[str] = Field(default_factory=list)
    error: Optional[str] = None


_jobs: dict[str, Job] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────── FastAPI app ─────────────────────────────────────

app = FastAPI(
    title="Oracle DB Provisioning Agent",
    description="Autonomous provisioning of Oracle databases inside a Docker-hosted Exadata environment.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared controller (singleton per process)
_controller = DockerController()


# ─────────────────────────── request schema ──────────────────────────────────

class ProvisionPayload(BaseModel):
    db_name: str = Field(..., example="mydb1a")
    db_unique_name: str = Field(..., example="mydb1a_site1")
    provisioning_type: Literal["seed", "clone"]
    character_set: str = Field(default="AL32UTF8")
    national_character_set: str = Field(default="AL16UTF16")


# ─────────────────────────── helpers ─────────────────────────────────────────

async def _run_provisioning(job: Job) -> None:
    """
    Background coroutine that drives the full provisioning pipeline and
    updates the shared job record in-place.
    """
    job.status = "running"
    job.started_at = _now_iso()

    req = ProvisionRequest(
        db_name=job.db_name,
        db_unique_name=job.db_unique_name,
        provisioning_type=job.provisioning_type,  # type: ignore[arg-type]
    )

    try:
        # ── Phase 1: provision (seed or clone) ───────────────────────────────
        if job.provisioning_type == "seed":
            workflow = seed_database(req, _controller)
        else:
            workflow = clone_database(req, _controller)

        async for line in workflow:
            job.logs.append(line)

        # ── Phase 2: post-provisioning parameters ────────────────────────────
        async for line in apply_post_provision_parameters(job.db_name, _controller):
            job.logs.append(line)

        # ── Phase 3: verify parameters ───────────────────────────────────────
        async for line in verify_parameters(job.db_name, _controller):
            job.logs.append(line)

        # ── Phase 4: RMAN catalog check ──────────────────────────────────────
        async for line in verify_rman_catalog_registration(job.db_name, _controller):
            job.logs.append(line)

        job.status = "completed"
        job.logs.append(f"[AGENT] ✔  Job {job.job_id} completed successfully.")

    except DockerExecutionError as exc:
        job.status = "failed"
        job.error = str(exc)
        job.logs.append(f"[AGENT] ✘  Docker error: {exc}")
    except Exception as exc:  # noqa: BLE001
        job.status = "failed"
        job.error = str(exc)
        job.logs.append(f"[AGENT] ✘  Unexpected error: {exc}")
        logger.exception("Unhandled error in job %s", job.job_id)
    finally:
        job.completed_at = _now_iso()


# ─────────────────────────── SSE generator ───────────────────────────────────

async def _sse_stream(job_id: str) -> AsyncGenerator[str, None]:
    """
    Stream job logs as Server-Sent Events until the job finishes.
    """
    sent_index = 0

    while True:
        job = _jobs.get(job_id)
        if job is None:
            yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
            return

        # Send any new log lines
        new_lines = job.logs[sent_index:]
        for line in new_lines:
            payload = json.dumps({"type": "log", "message": line})
            yield f"data: {payload}\n\n"
            sent_index += 1

        if job.status in ("completed", "failed"):
            final = json.dumps({"type": "status", "status": job.status, "error": job.error})
            yield f"data: {final}\n\n"
            return

        await asyncio.sleep(0.3)


# Fix missing import annotation
from collections.abc import AsyncGenerator  # noqa: E402


# ─────────────────────────── routes ──────────────────────────────────────────

@app.get("/api/health", tags=["Infra"])
async def health_check():
    """Returns Docker container health."""
    reachable = _controller.health_check()
    return {
        "container": "oracle-exadata-dev",
        "reachable": reachable,
        "status": "healthy" if reachable else "unreachable",
    }


@app.post("/api/provision", status_code=202, tags=["Provisioning"])
async def provision(payload: ProvisionPayload, background_tasks=None):
    """
    Enqueue a new provisioning job.

    Returns job_id immediately; use GET /api/jobs/{job_id}/stream for SSE.
    """
    req = ProvisionRequest(
        db_name=payload.db_name,
        db_unique_name=payload.db_unique_name,
        provisioning_type=payload.provisioning_type,
        character_set=payload.character_set,
        national_character_set=payload.national_character_set,
    )

    # ── Validate FIRST ──
    result = validate_provision_request(req)
    if not result.valid:
        raise HTTPException(status_code=400, detail={"validation_errors": result.errors})

    job_id = str(uuid.uuid4())
    job = Job(
        job_id=job_id,
        db_name=payload.db_name.upper(),
        db_unique_name=payload.db_unique_name.upper(),
        provisioning_type=payload.provisioning_type,
        status="pending",
        created_at=_now_iso(),
    )
    _jobs[job_id] = job

    # Fire-and-forget in background
    asyncio.create_task(_run_provisioning(job))

    logger.info("Enqueued job %s (%s → %s)", job_id, payload.db_name, payload.provisioning_type)
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/jobs/{job_id}/stream", tags=["Provisioning"])
async def stream_job(job_id: str):
    """Server-Sent Events stream for a specific job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")

    return StreamingResponse(
        _sse_stream(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs", tags=["Queue"])
async def list_jobs():
    """Return all jobs grouped by status."""
    jobs = list(_jobs.values())
    return {
        "pending": [j for j in jobs if j.status == "pending"],
        "running": [j for j in jobs if j.status == "running"],
        "completed": [j for j in jobs if j.status == "completed"],
        "failed": [j for j in jobs if j.status == "failed"],
    }


@app.get("/api/jobs/{job_id}", tags=["Queue"])
async def get_job(job_id: str):
    """Return a single job's full detail including logs."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.delete("/api/jobs/{job_id}", tags=["Queue"])
async def delete_job(job_id: str):
    """Remove a completed or failed job from the queue."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("pending", "running"):
        raise HTTPException(status_code=409, detail="Cannot delete an active job.")
    del _jobs[job_id]
    return {"deleted": job_id}
