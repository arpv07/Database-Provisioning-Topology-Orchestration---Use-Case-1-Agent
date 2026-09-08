"""
Module: LangGraph StateGraph Workflow Orchestrator
===================================================
Defines a stateful graph DAG using LangGraph for multi-node 
Oracle DB Provisioning state transitions, conditional routing, and AI error handling.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .ai_agent import diagnose_provisioning_error, parse_natural_language_intent
from .job_store import job_store
from .topology import topology_manager
from .validation_engine import ProvisionRequest, validate_provision_request
from .workflows import (
    apply_post_provision_parameters,
    clone_database,
    seed_database,
    verify_parameters,
)

logger = logging.getLogger(__name__)


def _sync_to_job_store(state: ProvisioningState) -> None:
    job_id = state.get("job_id")
    if not job_id:
        return
    job = job_store.get_job(job_id)
    if job:
        job.status = state.get("status", job.status)
        job.logs = list(state.get("logs", []))
        if state.get("error"):
            job.error = state.get("error")
        if job.status in ("completed", "failed", "validation_failed"):
            from datetime import datetime, timezone
            job.completed_at = datetime.now(timezone.utc).isoformat()
        job_store.update_job(job)


class ProvisioningState(TypedDict):
    """LangGraph state schema passed between nodes."""
    job_id: str
    raw_prompt: Optional[str]
    request: Dict[str, Any]
    status: str
    logs: List[str]
    rca_report: Optional[Dict[str, Any]]
    error: Optional[str]


# ─────────────────────────── Node Definitions ────────────────────────────────

def parse_and_validate_node(state: ProvisioningState) -> ProvisioningState:
    """Node 1: Parses natural language (if prompt provided) and validates naming rules."""
    logs = list(state.get("logs", []))
    logs.append("[LANGGRAPH] ▶ Node 1: Parsing and validating provisioning parameters...")

    req_data = state.get("request", {})
    if state.get("raw_prompt"):
        parsed = parse_natural_language_intent(state["raw_prompt"])
        req_data.update({k: v for k, v in parsed.items() if v is not None})
        logs.append(f"[LANGGRAPH] AI Intent Parsed: {parsed.get('explanation')}")

    try:
        req = ProvisionRequest(**req_data)
        result = validate_provision_request(req)
        if not result.valid:
            error_str = " | ".join(result.errors)
            logs.append(f"[LANGGRAPH] ✘ Validation failed: {error_str}")
            res = {**state, "status": "validation_failed", "logs": logs, "error": error_str}
            _sync_to_job_store(res)
            return res
    except Exception as exc:
        logs.append(f"[LANGGRAPH] ✘ Schema error: {exc}")
        res = {**state, "status": "validation_failed", "logs": logs, "error": str(exc)}
        _sync_to_job_store(res)
        return res

    logs.append(f"[LANGGRAPH] ✔ Validation passed for SID={req.db_name}")
    res = {**state, "request": req_data, "logs": logs}
    _sync_to_job_store(res)
    return res


def execute_provision_node(state: ProvisioningState) -> ProvisioningState:
    """Node 2: Executes DBCA seed build or RMAN clone workflow."""
    logs = list(state.get("logs", []))
    logs.append("[LANGGRAPH] ▶ Node 2: Executing database provision workflow...")

    req_data = state["request"]
    req = ProvisionRequest(**req_data)
    container_name = topology_manager.resolve_cluster_container(req.target_cluster_id)
    controller = DockerController(container_name=container_name)

    try:
        if req.provisioning_type == "seed":
            gen = seed_database(req, controller)
        else:
            gen = clone_database(req, controller)

        for line in gen:
            logs.append(line)
            res_running = {**state, "status": "running", "logs": logs}
            _sync_to_job_store(res_running)

        res = {**state, "status": "provisioned", "logs": logs}
        _sync_to_job_store(res)
        return res
    except Exception as exc:
        logs.append(f"[LANGGRAPH] ✘ Provisioning failed: {exc}")
        res = {**state, "status": "failed", "logs": logs, "error": str(exc)}
        _sync_to_job_store(res)
        return res


def post_provision_tuning_node(state: ProvisioningState) -> ProvisioningState:
    """Node 3: Applies ALTER SYSTEM post-provisioning parameters & verifies v$parameter."""
    logs = list(state.get("logs", []))
    logs.append("[LANGGRAPH] ▶ Node 3: Applying post-provisioning tuning parameters...")

    req_data = state["request"]
    db_name = req_data["db_name"]
    container_name = topology_manager.resolve_cluster_container(req_data["target_cluster_id"])
    controller = DockerController(container_name=container_name)

    try:
        for line in apply_post_provision_parameters(db_name, controller):
            logs.append(line)
            _sync_to_job_store({**state, "status": "running", "logs": logs})
        for line in verify_parameters(db_name, controller):
            logs.append(line)
            _sync_to_job_store({**state, "status": "running", "logs": logs})

        logs.append(f"[LANGGRAPH] ✔ Successfully provisioned and tuned SID={db_name.upper()}")
        res = {**state, "status": "completed", "logs": logs}
        _sync_to_job_store(res)
        return res
    except Exception as exc:
        logs.append(f"[LANGGRAPH] ✘ Parameter tuning failed: {exc}")
        res = {**state, "status": "failed", "logs": logs, "error": str(exc)}
        _sync_to_job_store(res)
        return res


def ai_rca_diagnostic_node(state: ProvisioningState) -> ProvisioningState:
    """Node 4: Error Handling Node — Runs Groq / Llama-3.3-70b AI RCA Diagnosis on failure logs."""
    logs = list(state.get("logs", []))
    logs.append("[LANGGRAPH] ⚡ Node 4: Running Llama-3.3-70b AI Root Cause Analysis...")

    rca = diagnose_provisioning_error(logs)
    logs.append(f"[LANGGRAPH] [AI RCA] Root Cause: {rca.get('root_cause')}")
    logs.append(f"[LANGGRAPH] [AI RCA] Fix Guidance: {rca.get('recommended_fix')}")

    res = {**state, "status": "failed", "logs": logs, "rca_report": rca}
    _sync_to_job_store(res)
    return res


# ─────────────────────────── Conditional Routing ─────────────────────────────

def route_after_validation(state: ProvisioningState) -> str:
    if state.get("status") == "validation_failed":
        return "ai_rca_diagnostic"
    return "execute_provision"


def route_after_provision(state: ProvisioningState) -> str:
    if state.get("status") == "failed":
        return "ai_rca_diagnostic"
    return "post_provision_tuning"


# ─────────────────────────── Graph Construction ──────────────────────────────

def create_langgraph_workflow() -> StateGraph:
    """Builds and compiles the LangGraph StateGraph."""
    workflow = StateGraph(ProvisioningState)

    workflow.add_node("parse_and_validate", parse_and_validate_node)
    workflow.add_node("execute_provision", execute_provision_node)
    workflow.add_node("post_provision_tuning", post_provision_tuning_node)
    workflow.add_node("ai_rca_diagnostic", ai_rca_diagnostic_node)

    workflow.add_edge(START, "parse_and_validate")
    workflow.add_conditional_edges("parse_and_validate", route_after_validation)
    workflow.add_conditional_edges("execute_provision", route_after_provision)
    workflow.add_edge("post_provision_tuning", END)
    workflow.add_edge("ai_rca_diagnostic", END)

    return workflow.compile()


# Singleton compiled graph instance
langgraph_app = create_langgraph_workflow()
