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

import dotenv
dotenv.load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .ai_agent import diagnose_provisioning_error, parse_natural_language_intent
from .docker_controller import DockerController
from .job_store import JobRecord, job_store
from .langgraph_workflow import langgraph_app
from .topology import topology_manager
from .validation_engine import ProvisionRequest, validate_provision_request

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

        if job.status in ("completed", "failed", "validation_failed"):
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


class AIPromptPayload(BaseModel):
    prompt: str = Field(..., json_schema_extra={"example": "Clone production database ORD1P to cluster-exa-dev01 for testing"})


class AIDiagnosePayload(BaseModel):
    logs: list[str] = Field(default_factory=list)


@app.get("/api/topology/clone-sources", tags=["Topology"], dependencies=[Depends(verify_bearer_token)])
async def list_clone_sources():
    """List all registered clone source databases across clusters."""
    return [cs.model_dump() for cs in topology_manager.get_all_clone_sources()]


@app.post("/api/ai/parse-intent", tags=["AI Agent"], dependencies=[Depends(verify_bearer_token)])
async def ai_parse_intent(payload: AIPromptPayload):
    """Parse natural language request into structured ProvisionPayload using Llama-3.3-70b (Groq API)."""
    return parse_natural_language_intent(payload.prompt)


@app.post("/api/ai/diagnose", tags=["AI Agent"], dependencies=[Depends(verify_bearer_token)])
async def ai_diagnose_log(payload: AIDiagnosePayload):
    """Generate Root Cause Analysis (RCA) and resolution recommendations for execution log errors."""
    return diagnose_provisioning_error(payload.logs)


@app.post("/api/ai/langgraph-provision", status_code=202, tags=["AI Agent"], dependencies=[Depends(verify_bearer_token)])
async def ai_langgraph_provision(payload: ProvisionPayload):
    """Execute provisioning request through the compiled LangGraph StateGraph workflow engine."""
    container_name = topology_manager.resolve_cluster_container(payload.target_cluster_id)
    controller = DockerController(container_name=container_name)
    if not controller.health_check():
        raise HTTPException(
            status_code=503,
            detail={
                "validation_errors": [
                    f"Target Docker container '{container_name}' is offline or unreachable. "
                    "Please start Docker Desktop and run 'docker compose up -d' before submitting provision requests."
                ]
            },
        )

    job_id = str(uuid.uuid4())
    job = JobRecord(
        job_id=job_id,
        db_name=payload.db_name.upper(),
        db_unique_name=payload.db_unique_name.upper(),
        target_cluster_id=payload.target_cluster_id,
        source_cluster_id=payload.source_cluster_id,
        provisioning_type=payload.provisioning_type,
        status="pending",
        created_at=_now_iso(),
        logs=[f"[LANGGRAPH] ▶ Initiating LangGraph StateGraph Workflow (Job ID: {job_id[:8]})"],
    )
    job_store.create_job(job)

    initial_state = {
        "job_id": job_id,
        "raw_prompt": None,
        "request": payload.model_dump(),
        "status": "pending",
        "logs": job.logs,
        "rca_report": None,
        "error": None,
    }
    asyncio.create_task(asyncio.to_thread(langgraph_app.invoke, initial_state))
    return {
        "job_id": job_id,
        "status": "pending",
        "logs": job.logs,
    }


@app.post("/api/provision", status_code=202, tags=["Provisioning"], dependencies=[Depends(verify_bearer_token)])
async def provision(payload: ProvisionPayload):
    """
    Execute a provisioning job against a target Docker container via LangGraph workflow.
    """
    # ── Resolve target_cluster_id FIRST ──
    try:
        container_name = topology_manager.resolve_cluster_container(payload.target_cluster_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"validation_errors": [str(exc)]})

    controller = DockerController(container_name=container_name)
    if not controller.health_check():
        raise HTTPException(
            status_code=503,
            detail={
                "validation_errors": [
                    f"Target Docker container '{container_name}' is offline or unreachable. "
                    "Please start Docker Desktop and run 'docker compose up -d' before submitting provision requests."
                ]
            },
        )

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
        source_cluster_id=payload.source_cluster_id,
        provisioning_type=payload.provisioning_type,
        status="pending",
        created_at=_now_iso(),
        logs=[f"[AGENT] ▶ Initiating provisioning workflow (Job ID: {job_id[:8]})"],
    )
    job_store.create_job(job)

    initial_state = {
        "job_id": job_id,
        "raw_prompt": None,
        "request": payload.model_dump(),
        "status": "pending",
        "logs": job.logs,
        "rca_report": None,
        "error": None,
    }

    logger.info("Starting job %s (%s → %s on %s)", job_id, payload.db_name, payload.provisioning_type, container_name)
    asyncio.create_task(asyncio.to_thread(langgraph_app.invoke, initial_state))

    return {
        "job_id": job_id,
        "status": "pending",
        "logs": job.logs,
    }


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
