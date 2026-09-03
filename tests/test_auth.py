"""
Unit tests for Bearer Token Authentication (Part 4).
"""

from unittest.mock import patch
from fastapi.testclient import TestClient
from backend.app import app

client = TestClient(app)
VALID_TOKEN = "dev-secret-key-123"


def test_topology_frames_missing_token_returns_401():
    r = client.get("/api/topology/frames")
    assert r.status_code == 401
    assert "Invalid or missing Bearer token" in r.json()["detail"]


def test_topology_frames_invalid_token_returns_401():
    headers = {"Authorization": "Bearer invalid-token-999"}
    r = client.get("/api/topology/frames", headers=headers)
    assert r.status_code == 401
    assert "Invalid or missing Bearer token" in r.json()["detail"]


def test_topology_frames_valid_token_returns_200():
    headers = {"Authorization": f"Bearer {VALID_TOKEN}"}
    r = client.get("/api/topology/frames", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_provision_endpoint_missing_token_returns_401():
    payload = {
        "db_name": "mydb1a",
        "db_unique_name": "mydb1a_sitea",
        "target_cluster_id": "cluster-exa-dev01",
        "provisioning_type": "seed",
    }
    r = client.post("/api/provision", json=payload)
    assert r.status_code == 401
    assert "Invalid or missing Bearer token" in r.json()["detail"]
