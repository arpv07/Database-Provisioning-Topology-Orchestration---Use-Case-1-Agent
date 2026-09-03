"""
FastAPI Application – Oracle DB Provisioning Agent
====================================================
Routes:
  POST /api/provision                 – launch a seed or clone job (SSE stream)
  GET  /api/jobs                      – list all jobs (queue state)
  GET  /api/jobs/{job_id}             – single job detail
  GET  /api/jobs/{job_id}/stream      – stream job logs over SSE
  GET  /api/topology/frames           – list Exadata frames
  GET  /api/topology/frames/{id}/cluster – get cluster for frame
  GET  /api/health                    – Docker container health check
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .docker_controller import DockerController, DockerExecutionError
from .job_store import JobRecord, job_store
from .topology import topology_manager
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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────── Security & Auth ─────────────────────────────────

PROVISIONING_API_KEY = os.getenv("PROVISIONING_API_KEY")
if not PROVISIONING_API_KEY:
    raise RuntimeError("PROVISIONING_API_KEY environment variable is required and must be set at startup.")

security_bearer = HTTPBearer(auto_error=False)


async def verify_bearer_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> str:
    token = credentials.credentials if credentials else None
    if not token or token != PROVISIONING_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing Bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


# ─────────────────────────── FastAPI app ─────────────────────────────────────

app = FastAPI(
    title="Oracle DB Provisioning Agent",
    description="Autonomous provisioning of Oracle databases inside a Docker-hosted Exadata environment.",
    version="2.0.0",
)

cors_origins_raw = os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
allowed_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Controller instance resolved per-request based on topology
_controller = DockerController(container_name="oracle-exadata-dev")


# ─────────────────────────── Request schema ──────────────────────────────────

class ProvisionPayload(BaseModel):
    db_name: str = Field(..., json_schema_extra={"example": "mydb1a"})
    db_unique_name: str = Field(..., json_schema_extra={"example": "mydb1a_site1"})
    target_cluster_id: str = Field(..., json_schema_extra={"example": "cluster-exa-dev01"})
    source_cluster_id: Optional[str] = Field(default=None, json_schema_extra={"example": "cluster-exa-prod01"})
    provisioning_type: Literal["seed", "clone"]
    character_set: str = Field(default="AL32UTF8")
    national_character_set: str = Field(default="AL16UTF16")
    is_standby: bool = Field(default=False)
    create_standby: bool = Field(default=False)
    dataguard_enabled: bool = Field(default=False)


# ─────────────────────────── Helpers ─────────────────────────────────────────

async def _run_provisioning(job: JobRecord, container_name: str) -> None:
    """
    Background coroutine driving the full provisioning pipeline on a target container.
    Persists log updates into JobStore.
    """
    job.status = "running"
    job.started_at = _now_iso()
    job_store.update_job(job)

    controller = DockerController(container_name=container_name)

    req = ProvisionRequest(
        db_name=job.db_name,
        db_unique_name=job.db_unique_name,
        target_cluster_id=job.target_cluster_id,
        provisioning_type=job.provisioning_type,  # type: ignore[arg-type]
    )

    try:
        # ── Phase 1: provision (seed or clone) ───────────────────────────────
        if job.provisioning_type == "seed":
            workflow = seed_database(req, controller)
        else:
            workflow = clone_database(req, controller)

        async for line in workflow:
            job_store.append_log(job.job_id, line)

        # ── Phase 2: post-provisioning parameters ────────────────────────────
        async for line in apply_post_provision_parameters(job.db_name, controller):
            job_store.append_log(job.job_id, line)

        # ── Phase 3: verify parameters ───────────────────────────────────────
        async for line in verify_parameters(job.db_name, controller):
            job_store.append_log(job.job_id, line)

        # ── Phase 4: RMAN catalog check ──────────────────────────────────────
        async for line in verify_rman_catalog_registration(job.db_name, controller):
            job_store.append_log(job.job_id, line)

        current_job = job_store.get_job(job.job_id)
        if current_job:
            current_job.status = "completed"
            job_store.append_log(job.job_id, f"[AGENT] ✔  Job {job.job_id} completed successfully.")
            current_job.completed_at = _now_iso()
            job_store.update_job(current_job)

    except DockerExecutionError as exc:
        current_job = job_store.get_job(job.job_id)
        if current_job:
            current_job.status = "failed"
            current_job.error = str(exc)
            job_store.append_log(job.job_id, f"[AGENT] ✘  Docker error: {exc}")
            current_job.completed_at = _now_iso()
            job_store.update_job(current_job)
    except Exception as exc:  # noqa: BLE001
        current_job = job_store.get_job(job.job_id)
        if current_job:
            current_job.status = "failed"
            current_job.error = str(exc)
            job_store.append_log(job.job_id, f"[AGENT] ✘  Unexpected error: {exc}")
            current_job.completed_at = _now_iso()
            job_store.update_job(current_job)
        logger.exception("Unhandled error in job %s", job.job_id)


# ─────────────────────────── SSE Generator ───────────────────────────────────

async def _sse_stream(job_id: str) -> AsyncGenerator[str, None]:
    sent_index = 0

    while True:
        job = job_store.get_job(job_id)
        if job is None:
            yield f"data: {json.dumps({'error': 'Job not found'})}\n\n"
            return

        logs = job.logs or []
        new_lines = logs[sent_index:]
        for line in new_lines:
            payload = json.dumps({"type": "log", "message": line})
            yield f"data: {payload}\n\n"
            sent_index += 1

        if job.status in ("completed", "failed"):
            final = json.dumps({"type": "status", "status": job.status, "error": job.error})
            yield f"data: {final}\n\n"
            return

        await asyncio.sleep(0.3)


# ─────────────────────────── Routes ──────────────────────────────────────────

@app.get("/api/health", tags=["Infra"])
async def health_check():
    """Returns Docker container health."""
    reachable = _controller.health_check()
    return {
        "container": "oracle-exadata-dev",
        "reachable": reachable,
        "status": "healthy" if reachable else "unreachable",
    }


@app.get("/api/topology/frames", tags=["Topology"], dependencies=[Depends(verify_bearer_token)])
async def list_topology_frames():
    """List all Exadata frames from topology inventory."""
    return [f.model_dump() for f in topology_manager.get_all_frames()]


@app.get("/api/topology/frames/{frame_id}/cluster", tags=["Topology"], dependencies=[Depends(verify_bearer_token)])
async def get_frame_cluster(frame_id: str):
    """Get the cluster associated with a specific frame ID."""
    cluster = topology_manager.get_cluster_for_frame(frame_id)
    if not cluster:
        raise HTTPException(status_code=404, detail=f"Cluster for frame '{frame_id}' not found.")
    return cluster.model_dump()


@app.get("/api/topology/clone-sources", tags=["Topology"], dependencies=[Depends(verify_bearer_token)])
async def list_clone_sources():
    """List all registered clone source databases across clusters."""
    return [cs.model_dump() for cs in topology_manager.get_all_clone_sources()]


@app.post("/api/provision", status_code=202, tags=["Provisioning"], dependencies=[Depends(verify_bearer_token)])
async def provision(payload: ProvisionPayload):
    """
    Enqueue a new provisioning job against a target Exadata cluster.
    """
    # ── Resolve target_cluster_id FIRST ──
    try:
        container_name = topology_manager.resolve_cluster_container(payload.target_cluster_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"validation_errors": [str(exc)]})

    # ── Validate clone source if provisioning_type is clone ──
    if payload.provisioning_type == "clone":
        if not payload.source_cluster_id:
            raise HTTPException(status_code=400, detail={"validation_errors": ["source_cluster_id is required when provisioning_type is 'clone'"]})
        cs = topology_manager.get_clone_source(payload.source_cluster_id)
        if not cs:
            raise HTTPException(status_code=400, detail={"validation_errors": [f"Unknown source_cluster_id '{payload.source_cluster_id}'"]})

    req = ProvisionRequest(
        db_name=payload.db_name,
        db_unique_name=payload.db_unique_name,
        target_cluster_id=payload.target_cluster_id,
        source_cluster_id=payload.source_cluster_id,
        provisioning_type=payload.provisioning_type,
        character_set=payload.character_set,
        national_character_set=payload.national_character_set,
        is_standby=payload.is_standby,
        create_standby=payload.create_standby,
        dataguard_enabled=payload.dataguard_enabled,
    )

    # ── Validate Request Rules ──
    result = validate_provision_request(req)
    if not result.valid:
        raise HTTPException(status_code=400, detail={"validation_errors": result.errors})

    job_id = str(uuid.uuid4())
    job = JobRecord(
        job_id=job_id,
        db_name=payload.db_name.upper(),
        db_unique_name=payload.db_unique_name.upper(),
        target_cluster_id=payload.target_cluster_id,
        provisioning_type=payload.provisioning_type,
        status="pending",
        created_at=_now_iso(),
        logs=[],
    )
    job_store.create_job(job)

    asyncio.create_task(_run_provisioning(job, container_name))

    logger.info("Enqueued job %s (%s → %s on %s)", job_id, payload.db_name, payload.provisioning_type, container_name)
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/jobs/{job_id}/stream", tags=["Provisioning"])
async def stream_job(job_id: str, token: Optional[str] = Query(None)):
    """Server-Sent Events stream for a specific job."""
    if not token or token != PROVISIONING_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing Bearer token query parameter",
        )
    if job_store.get_job(job_id) is None:
        raise HTTPException(status_code=404, detail="Job not found")

    return StreamingResponse(
        _sse_stream(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/jobs", tags=["Queue"], dependencies=[Depends(verify_bearer_token)])
async def list_jobs():
    """Return all jobs grouped by status."""
    jobs = [j.to_dict() for j in job_store.list_jobs()]
    return {
        "pending": [j for j in jobs if j["status"] == "pending"],
        "running": [j for j in jobs if j["status"] == "running"],
        "completed": [j for j in jobs if j["status"] == "completed"],
        "failed": [j for j in jobs if j["status"] == "failed"],
    }


@app.get("/api/jobs/{job_id}", tags=["Queue"], dependencies=[Depends(verify_bearer_token)])
async def get_job(job_id: str):
    """Return a single job's full detail including logs."""
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.delete("/api/jobs/{job_id}", tags=["Queue"], dependencies=[Depends(verify_bearer_token)])
async def delete_job(job_id: str):
    """Remove a completed or failed job from the queue."""
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in ("pending", "running"):
        raise HTTPException(status_code=409, detail="Cannot delete an active job.")
    job_store.delete_job(job_id)
    return {"deleted": job_id}
