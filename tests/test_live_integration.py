"""
Live Integration Test (Part 0 & 3).
Marked slow / skip by default unless RUN_LIVE_TESTS env var is set and container is reachable.

Run with:  $env:RUN_LIVE_TESTS="1"; pytest tests/test_live_integration.py -v -m slow
"""

import os
import pytest
from backend.docker_controller import DockerController
from backend.validation_engine import ProvisionRequest
from backend.workflows import seed_database


def is_live_test_enabled() -> bool:
    if os.getenv("RUN_LIVE_TESTS") != "1":
        return False
    try:
        ctrl = DockerController(container_name="oracle-exadata-dev")
        return ctrl.health_check()
    except Exception:
        return False


@pytest.mark.slow
@pytest.mark.skipif(
    not is_live_test_enabled(),
    reason="RUN_LIVE_TESTS=1 not set or oracle-exadata-dev container is unreachable.",
)
@pytest.mark.asyncio
async def test_live_seed_workflow_dry_run():
    """Runs seed workflow steps against live oracle-exadata-dev container."""
    ctrl = DockerController(container_name="oracle-exadata-dev")
    req = ProvisionRequest(
        db_name="livedb1",
        db_unique_name="livedb1_site1",
        target_cluster_id="cluster-exa-dev01",
        provisioning_type="seed",
        character_set="AL32UTF8",
        national_character_set="AL16UTF16",
    )

    log_lines = []
    async for line in seed_database(req, ctrl):
        log_lines.append(line)
        if "Invoking DBCA" in line:
            break

    assert any("[SEED]" in line for line in log_lines)
