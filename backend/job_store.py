"""
Module: Job Store (Persistence)
================────────────────
Abstract interface and SQLite implementation for persisting provisioning jobs
and execution log streams across server restarts.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional


@dataclass
class JobRecord:
    job_id: str
    db_name: str
    db_unique_name: str
    target_cluster_id: str
    provisioning_type: str
    status: str
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    logs: Optional[List[str]] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["logs"] is None:
            d["logs"] = []
        return d


class BaseJobStore(ABC):
    @abstractmethod
    def create_job(self, job: JobRecord) -> None:
        pass

    @abstractmethod
    def update_job(self, job: JobRecord) -> None:
        pass

    @abstractmethod
    def append_log(self, job_id: str, log_line: str) -> None:
        pass

    @abstractmethod
    def get_job(self, job_id: str) -> Optional[JobRecord]:
        pass

    @abstractmethod
    def list_jobs(self) -> List[JobRecord]:
        pass

    @abstractmethod
    def delete_job(self, job_id: str) -> None:
        pass


class SQLiteJobStore(BaseJobStore):
    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or Path(__file__).parent / "jobs.db"
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    db_name TEXT NOT NULL,
                    db_unique_name TEXT NOT NULL,
                    target_cluster_id TEXT NOT NULL,
                    provisioning_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    logs TEXT NOT NULL,
                    error TEXT
                )
            """)
            conn.commit()

    def create_job(self, job: JobRecord) -> None:
        logs_json = json.dumps(job.logs or [])
        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO jobs (job_id, db_name, db_unique_name, target_cluster_id,
                                  provisioning_type, status, created_at, started_at,
                                  completed_at, logs, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.job_id,
                    job.db_name,
                    job.db_unique_name,
                    job.target_cluster_id,
                    job.provisioning_type,
                    job.status,
                    job.created_at,
                    job.started_at,
                    job.completed_at,
                    logs_json,
                    job.error,
                ),
            )
            conn.commit()

    def update_job(self, job: JobRecord) -> None:
        logs_json = json.dumps(job.logs or [])
        with self._get_connection() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, started_at = ?, completed_at = ?, logs = ?, error = ?
                WHERE job_id = ?
                """,
                (
                    job.status,
                    job.started_at,
                    job.completed_at,
                    logs_json,
                    job.error,
                    job.job_id,
                ),
            )
            conn.commit()

    def append_log(self, job_id: str, log_line: str) -> None:
        job = self.get_job(job_id)
        if not job:
            return
        job.logs = job.logs or []
        job.logs.append(log_line)
        self.update_job(job)

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if not row:
                return None
            return JobRecord(
                job_id=row["job_id"],
                db_name=row["db_name"],
                db_unique_name=row["db_unique_name"],
                target_cluster_id=row["target_cluster_id"],
                provisioning_type=row["provisioning_type"],
                status=row["status"],
                created_at=row["created_at"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                logs=json.loads(row["logs"]),
                error=row["error"],
            )

    def list_jobs(self) -> List[JobRecord]:
        with self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
            return [
                JobRecord(
                    job_id=row["job_id"],
                    db_name=row["db_name"],
                    db_unique_name=row["db_unique_name"],
                    target_cluster_id=row["target_cluster_id"],
                    provisioning_type=row["provisioning_type"],
                    status=row["status"],
                    created_at=row["created_at"],
                    started_at=row["started_at"],
                    completed_at=row["completed_at"],
                    logs=json.loads(row["logs"]),
                    error=row["error"],
                )
                for row in rows
            ]

    def delete_job(self, job_id: str) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))
            conn.commit()


# Default singleton instance
job_store = SQLiteJobStore()
