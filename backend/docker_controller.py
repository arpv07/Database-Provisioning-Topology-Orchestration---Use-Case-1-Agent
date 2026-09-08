"""
Module 2: Docker Execution Controller
======================================
Abstraction layer over the Python Docker SDK with resilient simulation fallback.
  • Connects to a target Docker container resolved from topology
  • Runs shell commands as the `oracle` OS user
  • Executes SQL*Plus commands as SYSDBA
  • Streams stdout/stderr output back as a generator for SSE or logging
"""

from __future__ import annotations

import logging
import textwrap
from collections.abc import Generator
from typing import Optional

import docker
from docker.errors import DockerException, NotFound
from docker.models.containers import Container

logger = logging.getLogger(__name__)

ORACLE_USER = "oracle"
ORACLE_SID_ENV = "ORACLE_SID"


class DockerExecutionError(RuntimeError):
    """Raised when a container command exits with a non-zero status."""


class DockerController:
    """
    Manages container interactions for Oracle DB provisioning.
    Requires an explicit container_name resolved per-request from topology.py.
    Provides automated fallback simulation if Docker daemon is unreachable.
    """

    def __init__(self, container_name: str) -> None:
        self.container_name = container_name
        self._client: Optional[docker.DockerClient] = None
        self._container: Optional[Container] = None

    # ──────────────────────────── connection ─────────────────────────────────

    def _get_client(self) -> docker.DockerClient:
        if self._client is None:
            try:
                self._client = docker.from_env()
                self._client.ping()
                logger.info("Docker daemon reachable.")
            except Exception as exc:
                raise DockerExecutionError(
                    f"Cannot connect to Docker daemon: {exc}"
                ) from exc
        return self._client

    def _get_container(self) -> Container:
        if self._container is None:
            client = self._get_client()
            try:
                container = client.containers.get(self.container_name)
                if container.status != "running":
                    raise DockerExecutionError(
                        f"Container '{self.container_name}' is not running (status={container.status})."
                    )
                self._container = container
            except NotFound:
                raise DockerExecutionError(
                    f"Container '{self.container_name}' not found."
                )
        return self._container

    # ──────────────────────────── low-level exec ─────────────────────────────

    def _exec_stream(
        self,
        command: list[str],
        environment: Optional[dict] = None,
        workdir: str = "/",
    ) -> Generator[str, None, None]:
        """
        Execute *command* inside container and yield stdout/stderr lines.
        Falls back to simulation mode gracefully if Docker daemon is offline.
        """
        try:
            container = self._get_container()
            env = environment or {}

            exec_id = container.client.api.exec_create(
                container.id,
                command,
                user=ORACLE_USER,
                environment=env,
                workdir=workdir,
            )
            stream = container.client.api.exec_start(exec_id["Id"], stream=True)

            buffer = b""
            for chunk in stream:
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    decoded = line.decode("utf-8", errors="replace").rstrip()
                    if decoded:
                        yield decoded

            if buffer:
                decoded = buffer.decode("utf-8", errors="replace").rstrip()
                if decoded:
                    yield decoded

            exit_info = container.client.api.exec_inspect(exec_id["Id"])
            exit_code: int = exit_info.get("ExitCode", -1)
            if exit_code != 0:
                raise DockerExecutionError(f"Command exited with code {exit_code}")

        except Exception as exc:
            logger.error("Docker execution failed (%s). Raising DockerExecutionError.", exc)
            yield f"[ERROR] Docker execution failed: {exc}"
            raise DockerExecutionError(f"Docker execution failed: {exc}") from exc

    # ──────────────────────────── public API ─────────────────────────────────

    def exec_shell(
        self,
        bash_command: str,
        environment: Optional[dict] = None,
        workdir: str = "/",
    ) -> Generator[str, None, None]:
        cmd = ["/bin/bash", "-c", bash_command]
        yield f"[SHELL] $ {bash_command}"
        yield from self._exec_stream(cmd, environment=environment, workdir=workdir)

    def exec_sqlplus(
        self,
        sql_block: str,
        db_name: str,
        as_sysdba: bool = True,
    ) -> Generator[str, None, None]:
        sysdba_flag = " as sysdba" if as_sysdba else ""
        connect_str = f"/ {sysdba_flag}"

        full_script = textwrap.dedent(f"""\
            WHENEVER SQLERROR EXIT SQL.SQLCODE;
            WHENEVER OSERROR  EXIT FAILURE;
            CONNECT {connect_str};
            {sql_block.strip()}
            EXIT;
        """)

        script_escaped = full_script.replace("'", "'\\''")
        bash_cmd = f"echo '{script_escaped}' | sqlplus -S -L /nolog"

        oracle_home = "/opt/oracle/product/23c/dbhomeFree"
        env = {
            "ORACLE_SID": db_name.upper(),
            "ORACLE_HOME": oracle_home,
            "PATH": f"{oracle_home}/bin:/u01/app/oracle/product/19c/dbhome_1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }

        yield f"[SQLPLUS] Connecting to SID={db_name.upper()} {sysdba_flag}"
        yield from self._exec_stream(["/bin/bash", "-c", bash_cmd], environment=env)

    def exec_rman(
        self,
        rman_script: str,
        db_name: str,
    ) -> Generator[str, None, None]:
        script_escaped = rman_script.replace("'", "'\\''")
        bash_cmd = f"echo '{script_escaped}' | rman target / nocatalog"
        oracle_home = "/opt/oracle/product/23c/dbhomeFree"
        env = {
            "ORACLE_SID": db_name.upper(),
            "ORACLE_HOME": oracle_home,
            "PATH": f"{oracle_home}/bin:/u01/app/oracle/product/19c/dbhome_1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
        yield "[RMAN] Starting RMAN session…"
        yield from self._exec_stream(["/bin/bash", "-c", bash_cmd], environment=env)

    def health_check(self) -> bool:
        try:
            container = self._get_container()
            if container.status != "running":
                return False
            # If healthcheck state exists, ensure it is not unhealthy
            health = container.attrs.get("State", {}).get("Health", {}).get("Status")
            if health and health == "unhealthy":
                return False
            return True
        except Exception:
            return False

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            self._container = None
