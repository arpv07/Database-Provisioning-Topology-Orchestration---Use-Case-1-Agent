"""
Module: Topology Model
================──────
Models Exadata Frames, Clusters, and Clone Sources loaded from topology_inventory.yaml.
Provides container resolution for cluster execution targets and clone sources.
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
    container_name: str = Field(default="oracle-exadata-dev")
    compute_nodes: List[str] = Field(default_factory=list)
    currently_hosted_databases: List[str] = Field(default_factory=list)


class CloneSource(BaseModel):
    db_name: str
    db_unique_name: str
    source_cluster_id: str
    container_name: str
    environment: str


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
        self._clone_sources: dict[str, CloneSource] = {}
        self.reload()

    def reload(self) -> None:
        if not self.inventory_path.exists():
            raise FileNotFoundError(f"Topology inventory file missing: {self.inventory_path}")

        with open(self.inventory_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        frames_raw = data.get("frames", [])
        clone_sources_raw = data.get("clone_sources", [])

        self._frames.clear()
        self._clusters.clear()
        self._clone_sources.clear()

        for raw_frame in frames_raw:
            frame = Frame(**raw_frame)
            self._frames[frame.id] = frame
            self._clusters[frame.cluster.id] = frame.cluster

        for raw_source in clone_sources_raw:
            cs = CloneSource(**raw_source)
            self._clone_sources[cs.source_cluster_id] = cs

    def get_all_frames(self) -> List[Frame]:
        return list(self._frames.values())

    def get_frame(self, frame_id: str) -> Optional[Frame]:
        return self._frames.get(frame_id)

    def get_cluster(self, cluster_id: str) -> Optional[Cluster]:
        return self._clusters.get(cluster_id)

    def get_cluster_for_frame(self, frame_id: str) -> Optional[Cluster]:
        frame = self.get_frame(frame_id)
        return frame.cluster if frame else None

    def get_all_clone_sources(self) -> List[CloneSource]:
        return list(self._clone_sources.values())

    def get_clone_source(self, source_cluster_id: str) -> Optional[CloneSource]:
        return self._clone_sources.get(source_cluster_id)

    def resolve_cluster_container(self, cluster_id: str) -> str:
        """
        Resolves a target_cluster_id to its configured Docker container name.
        Enforces that the cluster_id exists in topology.
        """
        cluster = self.get_cluster(cluster_id)
        if not cluster:
            raise ValueError(f"Unknown target_cluster_id '{cluster_id}'")
        return cluster.container_name


# Global singleton instance
topology_manager = TopologyManager()
