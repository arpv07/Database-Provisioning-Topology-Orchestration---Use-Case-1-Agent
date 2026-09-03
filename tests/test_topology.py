"""
Unit tests for Topology Model and Container Resolution (Part 1 & 2).
"""

import pytest
from backend.topology import TopologyManager, FrameModel, Frame, Cluster


def test_topology_inventory_loading():
    tm = TopologyManager()
    frames = tm.get_all_frames()
    assert len(frames) >= 3

    frame = tm.get_frame("frame-exadata-01")
    assert frame is not None
    assert frame.model == FrameModel.X9M
    assert len(frame.storage_servers) >= 3
    assert frame.cluster.id == "cluster-exa-prod01"


def test_cluster_container_resolution_success():
    tm = TopologyManager()
    container = tm.resolve_cluster_container("cluster-exa-prod01")
    assert container == "oracle-exadata-dev"


def test_unknown_cluster_rejection():
    tm = TopologyManager()
    with pytest.raises(ValueError) as exc_info:
        tm.resolve_cluster_container("cluster-nonexistent")
    assert "Unknown target_cluster_id" in str(exc_info.value)


def test_storage_server_minimum_enforcement():
    with pytest.raises(ValueError) as exc_info:
        Frame(
            id="invalid-frame",
            model=FrameModel.X8,
            datacenter="us-east-1",
            storage_servers=["cel01.local", "cel02.local"],  # Only 2, min is 3
            cluster=Cluster(id="c1", frame_id="invalid-frame")
        )
    assert "must have at least 3 storage servers" in str(exc_info.value)
