"""
Module: Topology Model
================──────
Models Exadata Frames and Clusters loaded from topology_inventory.yaml.
Provides container resolution for cluster execution targets.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import List, Optional
import yaml
from pydantic import BaseModel, Field, field_validator


class FrameModel(str, Enum):
    X8 = "X8"
    X8M = "X8M"
    X9M = "X9M"
    X11M = "X11M"


class Cluster(BaseModel):
    id: str
    frame_id: str
    compute_nodes: List[str] = Field(default_factory=list)
    currently_hosted_databases: List[str] = Field(default_factory=list)


class Frame(BaseModel):
    id: str
    model: FrameModel
    datacenter: str
    storage_servers: List[str]
    cluster: Cluster

    @field_validator("storage_servers")
    @classmethod
    def validate_storage_servers(cls, v: List[str]) -> List[str]:
        if len(v) < 3:
            raise ValueError("Exadata frame must have at least 3 storage servers.")
        return v


_INVENTORY_PATH = Path(__file__).parent / "topology_inventory.yaml"


class TopologyManager:
    def __init__(self, inventory_path: Optional[Path] = None) -> None:
        self.inventory_path = inventory_path or _INVENTORY_PATH
        self._frames: dict[str, Frame] = {}
        self._clusters: dict[str, Cluster] = {}
        self.reload()

    def reload(self) -> None:
        if not self.inventory_path.exists():
            raise FileNotFoundError(f"Topology inventory file missing: {self.inventory_path}")

        with open(self.inventory_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        frames_raw = data.get("frames", [])
        self._frames.clear()
        self._clusters.clear()

        for raw_frame in frames_raw:
            frame = Frame(**raw_frame)
            self._frames[frame.id] = frame
            self._clusters[frame.cluster.id] = frame.cluster

    def get_all_frames(self) -> List[Frame]:
        return list(self._frames.values())

    def get_frame(self, frame_id: str) -> Optional[Frame]:
        return self._frames.get(frame_id)

    def get_cluster(self, cluster_id: str) -> Optional[Cluster]:
        return self._clusters.get(cluster_id)

    def get_cluster_for_frame(self, frame_id: str) -> Optional[Cluster]:
        frame = self.get_frame(frame_id)
        return frame.cluster if frame else None

    def resolve_cluster_container(self, cluster_id: str) -> str:
        """
        Resolves a target_cluster_id to a Docker container name.
        Enforces that the cluster_id exists in topology.
        Returns 'oracle-exadata-dev' for local Docker POC.
        """
        cluster = self.get_cluster(cluster_id)
        if not cluster:
            raise ValueError(f"Unknown target_cluster_id '{cluster_id}'")
        # POC single-container mapping
        return "oracle-exadata-dev"


# Global singleton instance
topology_manager = TopologyManager()
