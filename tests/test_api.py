"""
Integration-style tests for Docker controller (mocked) and provisioning
API endpoints.

Run with:  pytest tests/test_api.py -v
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from backend.app import app, _jobs


@pytest.fixture(autouse=True)
def clear_jobs_and_mock_controller():
    """Isolate each test and mock the Docker controller to avoid Docker SDK network calls."""
    _jobs.clear()
    with patch("backend.app._controller") as mock_ctrl:
        mock_ctrl.health_check.return_value = True
        mock_ctrl.exec_shell.return_value = iter(["Mock shell output line 1", "Mock shell output line 2"])
        mock_ctrl.exec_sqlplus.return_value = iter(["Mock SQL output line 1", "PASS"])
        mock_ctrl.exec_rman.return_value = iter(["Mock RMAN output line 1"])
        yield mock_ctrl
    _jobs.clear()


client = TestClient(app)


# ─────────────────────────── /api/health ─────────────────────────────────────

class TestHealthEndpoint:

    def test_health_returns_200(self):
        r = client.get("/api/health")
        assert r.status_code == 200
        assert "reachable" in r.json()


# ─────────────────────────── /api/provision – validation ─────────────────────

class TestProvisionValidation:

    VALID_PAYLOAD = {
        "db_name": "mydb1a",
        "db_unique_name": "mydb1a_sitea",
        "provisioning_type": "seed",
        "character_set": "AL32UTF8",
        "national_character_set": "AL16UTF16",
    }

    def test_valid_payload_accepted(self):
        with patch("backend.app._run_provisioning", new_callable=AsyncMock):
            r = client.post("/api/provision", json=self.VALID_PAYLOAD)
        assert r.status_code == 202
        body = r.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    def test_db_name_too_long_rejected(self):
        payload = {**self.VALID_PAYLOAD, "db_name": "toolongname"}
        r = client.post("/api/provision", json=payload)
        assert r.status_code == 400
        detail = r.json()["detail"]
        assert "validation_errors" in detail
        assert any("8 characters" in e for e in detail["validation_errors"])

    def test_db_name_ends_in_digit_rejected(self):
        payload = {**self.VALID_PAYLOAD, "db_name": "mydb1"}
        r = client.post("/api/provision", json=payload)
        assert r.status_code == 400

    def test_db_name_special_char_rejected(self):
        payload = {**self.VALID_PAYLOAD, "db_name": "my!db1a"}
        r = client.post("/api/provision", json=payload)
        assert r.status_code == 400

    def test_db_unique_name_too_long_rejected(self):
        payload = {**self.VALID_PAYLOAD, "db_unique_name": "a" * 16}
        r = client.post("/api/provision", json=payload)
        assert r.status_code == 400

    def test_wrong_charset_rejected(self):
        payload = {**self.VALID_PAYLOAD, "character_set": "UTF8"}
        r = client.post("/api/provision", json=payload)
        assert r.status_code == 400
        errs = r.json()["detail"]["validation_errors"]
        assert any("AL32UTF8" in e for e in errs)

    def test_wrong_ncharset_rejected(self):
        payload = {**self.VALID_PAYLOAD, "national_character_set": "UTF16"}
        r = client.post("/api/provision", json=payload)
        assert r.status_code == 400


# ─────────────────────────── /api/jobs ───────────────────────────────────────

class TestJobQueue:

    def _submit(self, db_name="mydb1a", db_unique_name="mydb1a_sitea"):
        with patch("backend.app._run_provisioning", new_callable=AsyncMock):
            return client.post("/api/provision", json={
                "db_name": db_name,
                "db_unique_name": db_unique_name,
                "provisioning_type": "seed",
                "character_set": "AL32UTF8",
                "national_character_set": "AL16UTF16",
            })

    def test_list_jobs_empty(self):
        r = client.get("/api/jobs")
        assert r.status_code == 200
        data = r.json()
        assert data["pending"] == []
        assert data["running"] == []

    def test_job_appears_in_queue(self):
        r = self._submit()
        assert r.status_code == 202
        job_id = r.json()["job_id"]

        r2 = client.get("/api/jobs")
        all_jobs = (
            r2.json()["pending"]
            + r2.json()["running"]
            + r2.json()["completed"]
            + r2.json()["failed"]
        )
        ids = [j["job_id"] for j in all_jobs]
        assert job_id in ids

    def test_get_single_job(self):
        r = self._submit()
        job_id = r.json()["job_id"]
        r2 = client.get(f"/api/jobs/{job_id}")
        assert r2.status_code == 200
        assert r2.json()["job_id"] == job_id

    def test_get_missing_job_404(self):
        r = client.get("/api/jobs/does-not-exist")
        assert r.status_code == 404
