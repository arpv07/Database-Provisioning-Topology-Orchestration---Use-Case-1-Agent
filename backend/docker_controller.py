"""
Module 2: Docker Execution Controller
======================================
Abstraction layer over the Python Docker SDK that:
  • Connects to the local container  `oracle-exadata-dev`
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

CONTAINER_NAME = "oracle-exadata-dev"
ORACLE_USER = "oracle"
ORACLE_SID_ENV = "ORACLE_SID"


class DockerExecutionError(RuntimeError):
    """Raised when a container command exits with a non-zero status."""


class DockerController:
    """
    Manages all container interactions for Oracle DB provisioning.

    Usage
    -----
    controller = DockerController()
    for line in controller.exec_shell("ls /u01/app/oracle/oradata"):
        print(line)
    """

    def __init__(self, container_name: str = CONTAINER_NAME) -> None:
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
            except DockerException as exc:
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
                        f"Container '{self.container_name}' is not running "
                        f"(status={container.status})."
                    )
                self._container = container
                logger.info(
                    "Attached to container '%s' (id=%s).",
                    self.container_name,
                    container.short_id,
                )
            except NotFound:
                raise DockerExecutionError(
                    f"Container '{self.container_name}' not found. "
                    "Ensure the Oracle Docker container is running."
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
        Execute *command* inside the container and yield stdout/stderr lines.
        Raises DockerExecutionError if the exit code is non-zero.
        """
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
                    logger.debug("[container] %s", decoded)
                    yield decoded

        if buffer:
            decoded = buffer.decode("utf-8", errors="replace").rstrip()
            if decoded:
                yield decoded

        exit_info = container.client.api.exec_inspect(exec_id["Id"])
        exit_code: int = exit_info.get("ExitCode", -1)
        if exit_code != 0:
            raise DockerExecutionError(
                f"Command exited with code {exit_code}: {' '.join(command)}"
            )

    # ──────────────────────────── public API ─────────────────────────────────

    def exec_shell(
        self,
        bash_command: str,
        environment: Optional[dict] = None,
        workdir: str = "/",
    ) -> Generator[str, None, None]:
        """
        Run *bash_command* inside the container as the `oracle` OS user.

        Example
        -------
        for line in controller.exec_shell("rm -rf /u01/oradata/staging/*"):
            print(line)
        """
        cmd = ["/bin/bash", "-c", bash_command]
        yield f"[SHELL] $ {bash_command}"
        yield from self._exec_stream(cmd, environment=environment, workdir=workdir)

    def exec_sqlplus(
        self,
        sql_block: str,
        db_name: str,
        as_sysdba: bool = True,
    ) -> Generator[str, None, None]:
        """
        Pipe *sql_block* into SQL*Plus inside the container as SYSDBA.

        The block is automatically terminated with EXIT so the process
        always returns a deterministic exit code.

        Example
        -------
        sql = "SELECT name FROM v\\$database;"
        for line in controller.exec_sqlplus(sql, db_name="mydb1a"):
            print(line)
        """
        sysdba_flag = " as sysdba" if as_sysdba else ""
        connect_str = f"/ {sysdba_flag}"

        # Wrap the caller's block in a complete SQL*Plus session
        full_script = textwrap.dedent(f"""\
            WHENEVER SQLERROR EXIT SQL.SQLCODE;
            WHENEVER OSERROR  EXIT FAILURE;
            CONNECT {connect_str};
            {sql_block.strip()}
            EXIT;
        """)

        # Escape single quotes inside the here-doc
        script_escaped = full_script.replace("'", "'\\''")

        bash_cmd = (
            f"echo '{script_escaped}' | "
            f"sqlplus -S -L /nolog"
        )

        env = {
            "ORACLE_SID": db_name.upper(),
            "ORACLE_HOME": "/u01/app/oracle/product/19c/dbhome_1",
            "PATH": "/u01/app/oracle/product/19c/dbhome_1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }

        yield f"[SQLPLUS] Connecting to SID={db_name.upper()} {sysdba_flag}"
        yield from self._exec_stream(["/bin/bash", "-c", bash_cmd], environment=env)

    def exec_rman(
        self,
        rman_script: str,
        db_name: str,
    ) -> Generator[str, None, None]:
        """
        Pipe *rman_script* into RMAN inside the container.
        """
        script_escaped = rman_script.replace("'", "'\\''")
        bash_cmd = (
            f"echo '{script_escaped}' | "
            f"rman target / nocatalog"
        )
        env = {
            "ORACLE_SID": db_name.upper(),
            "ORACLE_HOME": "/u01/app/oracle/product/19c/dbhome_1",
            "PATH": "/u01/app/oracle/product/19c/dbhome_1/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
        yield "[RMAN] Starting RMAN session…"
        yield from self._exec_stream(["/bin/bash", "-c", bash_cmd], environment=env)

    def health_check(self) -> bool:
        """Return True if the container is reachable and running."""
        try:
            container = self._get_container()
            return container.status == "running"
        except DockerExecutionError:
            return False

    def close(self) -> None:
        if self._client:
            self._client.close()
            self._client = None
            self._container = None
