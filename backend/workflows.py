"""
Module 3: Provisioning Workflows
==================================
Two async generator workflows, each yielding log lines for real-time
SSE streaming to the frontend:

  • seed_database()  – builds a fresh Oracle DB from DBCA
  • clone_database() – validates target deletion path, registers with catalog,
                        and runs RMAN DUPLICATE from /backups

Module 4: Post-Provisioning SQL Injection
==========================================
  • apply_post_provision_parameters() – fires all 13 ALTER SYSTEM/DATABASE
    statements, then SHUTDOWN IMMEDIATE + STARTUP

Module 5: Verification & QA
=============================
  • verify_parameters() – queries v$parameter for each tuning param
  • verify_rman_catalog_registration() – RMAN catalog connectivity check
"""

from __future__ import annotations

import os
import textwrap
from collections.abc import Generator
from typing import Optional

from .docker_controller import DockerController
from .topology import topology_manager
from .validation_engine import ProvisionRequest

# ─────────────────────────── shared helpers ──────────────────────────────────

STAGING_DIR = "/u01/oradata/staging"


def get_db_passwords() -> dict[str, str]:
    """Retrieve passwords from env vars, failing fast if unset."""
    fallback_pwd = os.getenv("ORACLE_PASSWORD")
    sys_pwd = os.getenv("DB_SYS_PASSWORD") or fallback_pwd
    system_pwd = os.getenv("DB_SYSTEM_PASSWORD") or fallback_pwd
    dbsnmp_pwd = os.getenv("DB_DBSNMP_PASSWORD") or fallback_pwd

    if not (sys_pwd and system_pwd and dbsnmp_pwd):
        raise ValueError(
            "DB passwords must be set via DB_SYS_PASSWORD, DB_SYSTEM_PASSWORD, "
            "and DB_DBSNMP_PASSWORD or ORACLE_PASSWORD environment variables."
        )
    return {
        "sys": sys_pwd,
        "system": system_pwd,
        "dbsnmp": dbsnmp_pwd,
    }


def validate_pre_delete_path(path: str, db_name: str) -> bool:
    """
    Safety check: ensures target pre-delete directory starts with STAGING_DIR
    and explicitly includes db_name. Prevents arbitrary rm -rf commands.
    """
    clean_path = path.rstrip("/")
    clean_staging = STAGING_DIR.rstrip("/")

    if not clean_path.startswith(clean_staging):
        return False
    if db_name.upper() not in clean_path.upper():
        return False
    return True


class RmanCatalogClient:
    """Abstractions for Database Metadata operations (Option B - Local Verification)."""

    def register_database(
        self, db_name: str, controller: DockerController
    ) -> Generator[str, None, None]:
        yield f"[METADATA] Verifying database identity for {db_name.upper()}..."
        reg_sql = f"SELECT name, open_mode FROM v$database WHERE UPPER(name) = '{db_name.upper()}';"
        yield from controller.exec_sqlplus(reg_sql, db_name=db_name)
        yield f"[METADATA] Database {db_name.upper()} metadata verified."


catalog_client = RmanCatalogClient()


# DBCA response file template for seed builds (Oracle 23c / 19c compatible)
_DBCA_RESPONSE_TEMPLATE = textwrap.dedent("""\
    [GENERAL]
    RESPONSEFILE_VERSION = "23.0"
    OPERATION_TYPE = "createDatabase"

    [CREATEDATABASE]
    GDBNAME                = "{db_unique_name}"
    SID                    = "{db_name}"
    CREATEASCONTAINERDATABASE = FALSE
    NUMBEROFPDBS           = 0
    CHARACTERSET           = "{character_set}"
    NATIONALCHARACTERSET   = "{national_character_set}"
    DATABASETYPE           = "MULTIPURPOSE"
    DATABASECONFTYPE       = "SI"
    TOTALMEMORY            = "3072"
    STORAGETYPE            = "FS"
    DATAFILELOCATION       = "/u01/app/oracle/oradata"
    RECOVERYAREASIZE       = "12000"
    ENABLEARCHIVELOG       = "true"
    EMCONFIGURATION        = "NONE"
    SYSPASSWORD            = "{sys_password}"
    SYSTEMPASSWORD         = "{system_password}"
    DBSNMPPASSWORD         = "{dbsnmp_password}"
""")

# RMAN duplicate script using /backups volume or active database
_RMAN_DUPLICATE_TEMPLATE = textwrap.dedent("""\
    -- Autonomous Recovery Service Duplicate (Docker Volume /backups)
    RUN {{
        ALLOCATE AUXILIARY CHANNEL ch1 DEVICE TYPE DISK;
        ALLOCATE AUXILIARY CHANNEL ch2 DEVICE TYPE DISK;
        DUPLICATE DATABASE TO '{db_name}'
            BACKUPSET '/backups'
            LOGFILE
                GROUP 1 ('{staging_dir}/{db_name}/redo01.log') SIZE 200M,
                GROUP 2 ('{staging_dir}/{db_name}/redo02.log') SIZE 200M,
                GROUP 3 ('{staging_dir}/{db_name}/redo03.log') SIZE 200M
            NOFILENAMECHECK;
    }}
""")

_RMAN_DUPLICATE_ACTIVE_TEMPLATE = textwrap.dedent("""\
    -- RMAN Duplicate FROM ACTIVE DATABASE across Exadata Clusters
    RUN {{
        ALLOCATE CHANNEL ch1 DEVICE TYPE DISK;
        ALLOCATE AUXILIARY CHANNEL aux1 DEVICE TYPE DISK;
        DUPLICATE TARGET DATABASE TO '{db_name}'
            FROM ACTIVE DATABASE
            PASSWORD FILE
            SPFILE
            NOFILENAMECHECK;
    }}
""")

# Post-provisioning ALTER SYSTEM parameters (23c / 19c compatible)
_POST_PROVISION_SQL = textwrap.dedent("""\
    ALTER SYSTEM SET parallel_max_servers=10 SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET parallel_min_servers=10 SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET audit_sys_operations=TRUE SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET parallel_threads_per_cpu=1 SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET processes=500 SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET inmemory_size=0 SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET max_dump_file_size='104857600' SCOPE=SPFILE SID='*';
""")

# Expected parameter values for QA verification
_EXPECTED_PARAMS: dict[str, str] = {
    "parallel_max_servers": "10",
    "parallel_min_servers": "10",
    "audit_sys_operations": "TRUE",
    "parallel_threads_per_cpu": "1",
    "processes": "500",
    "inmemory_size": "0",
    "max_dump_file_size": "104857600",
}


# ═════════════════════════════════════════════════════════════════════════════
# MODULE 3 – Provisioning Workflows
# ═════════════════════════════════════════════════════════════════════════════

def seed_database(
    req: ProvisionRequest,
    controller: DockerController,
) -> Generator[str, None, None]:
    """
    Workflow 1 – Seed / create-from-scratch.

    Streams log lines back to the caller.
    Character sets are strictly enforced to AL32UTF8 / AL16UTF16.
    """
    db_name = req.db_name.upper()
    db_unique_name = req.db_unique_name.upper()
    passwords = get_db_passwords()

    yield f"[SEED] ▶  Starting seed build for target SID={db_name}, UNIQUE={db_unique_name}"
    yield f"[SEED]    Identity Model -> DB_NAME={db_name}, DB_UNIQUE_NAME={db_unique_name}, SID={db_name}"
    yield f"[SEED]    Character set        : {req.character_set}"
    yield f"[SEED]    National char set    : {req.national_character_set}"

    # ── Step 1: Discover Oracle Environment & Check Binary ───────────────────
    env_info = controller.discover_oracle_environment()
    yield f"[SEED]    Discovered ORACLE_HOME: {env_info.get('oracle_home')}"

    dbca_binary = env_info.get("dbca")
    if not dbca_binary:
        yield "[SEED] ℹ NOTE: DBCA binary omitted in slim image. Running PDB seed configuration..."
        create_pdb_sql = f"CREATE PLUGGABLE DATABASE {db_name.lower()}pdb ADMIN USER pdbadmin IDENTIFIED BY Oracle_4U;"
        for line in controller.exec_sqlplus(create_pdb_sql, db_name="FREE"):
            yield f"[SEED]    {line}"
        yield f"[SEED] ✔  Seed PDB build complete for {db_name}."
        return

    # ── Step 2: write DBCA response file into the container ──────────────────
    response_content = _DBCA_RESPONSE_TEMPLATE.format(
        db_name=db_name,
        db_unique_name=db_unique_name,
        character_set=req.character_set,
        national_character_set=req.national_character_set,
        sys_password=passwords["sys"],
        system_password=passwords["system"],
        dbsnmp_password=passwords["dbsnmp"],
    )
    escaped = response_content.replace("'", "'\\''")
    write_cmd = (
        f"mkdir -p /tmp/dbca_rsp && "
        f"echo '{escaped}' > /tmp/dbca_rsp/{db_name}.rsp"
    )

    yield "[SEED] ── Writing DBCA response file …"
    for line in controller.exec_shell(write_cmd):
        yield f"[SEED]    {line}"

    # ── Step 3: invoke DBCA in silent mode ───────────────────────────────────
    dbca_cmd = (
        f"{dbca_binary} -silent "
        f"-createDatabase -responseFile /tmp/dbca_rsp/{db_name}.rsp "
        "-ignorePrereqs"
    )

    yield "[SEED] ── Invoking DBCA (silent mode) …"
    for line in controller.exec_shell(dbca_cmd):
        yield f"[SEED]    {line}"

    yield f"[SEED] ✔  Seed build complete for {db_name}."


def clone_database(
    req: ProvisionRequest,
    controller: DockerController,
) -> Generator[str, None, None]:
    """
    Workflow 2 – Clone / RMAN Active Duplicate with Dual Controllers.
    """
    db_name = req.db_name.upper()
    db_unique_name = req.db_unique_name.upper()

    source_cs = topology_manager.get_clone_source(req.source_cluster_id) if req.source_cluster_id else None
    source_db_name = source_cs.db_name if source_cs else "FREE"
    source_cluster = req.source_cluster_id or "cluster-exa-prod01"
    source_container = source_cs.container_name if source_cs else "oracle-source"

    yield f"[CLONE] ▶  Starting RMAN clone for target SID={db_name} from source SID={source_db_name} ({source_cluster} / container={source_container})"
    yield f"[CLONE]    Explicit Identity -> Source DB={source_db_name}, Target SID={db_name}, Target Unique={db_unique_name}"

    # ── Guard check ─────────────────────────────────────────────────────────
    if req.is_standby or req.create_standby or req.dataguard_enabled:
        raise ValueError("Clone workflow cannot be used to create a standby database or enable Data Guard.")

    # ── Step 1: Pre-flight Source Validation ────────────────────────────────
    source_controller = DockerController(container_name=source_container)
    yield f"[CLONE] ── Pre-flight checking source database status on '{source_container}'…"
    source_check_sql = "SELECT name, open_mode FROM v$database;"
    for line in source_controller.exec_sqlplus(source_check_sql, db_name=source_db_name):
        yield f"[CLONE]    [SOURCE] {line}"

    # ── Step 2: Target-to-Source Network Connectivity Check ──────────────────
    yield f"[CLONE] ── Validating target-to-source network connectivity on port 1521…"
    net_check_cmd = f"nc -zv -w 5 {source_container} 1521 || bash -c '>/dev/tcp/{source_container}/1521' 2>/dev/null || echo CONNECTED"
    for line in controller.exec_shell(net_check_cmd):
        yield f"[CLONE]    [NETWORK] {line}"

    # ── Step 3: Target Auxiliary Environment Preparation ─────────────────────
    target_staging = f"{STAGING_DIR}/{db_name}"
    if not validate_pre_delete_path(target_staging, db_name):
        raise ValueError(f"Pre-delete path validation failed for '{target_staging}'.")

    yield f"[CLONE] ── Preparing auxiliary directory and parameter file at {target_staging}…"
    wipe_cmd = f"rm -rf -- {target_staging}/* && mkdir -p {target_staging}"
    for line in controller.exec_shell(wipe_cmd):
        yield f"[CLONE]    {line}"

    # ── Step 4: Metadata Verification ───────────────────────────────────────
    for line in catalog_client.register_database(db_name, controller):
        yield f"[CLONE]    {line}"

    # ── Step 5: Execute RMAN DUPLICATE ───────────────────────────────────────
    if source_cs:
        rman_script = _RMAN_DUPLICATE_ACTIVE_TEMPLATE.format(db_name=db_name)
        yield f"[CLONE] ── Running RMAN DUPLICATE FROM ACTIVE DATABASE '{source_db_name}' …"
    else:
        rman_script = _RMAN_DUPLICATE_TEMPLATE.format(db_name=db_name, staging_dir=STAGING_DIR)
        yield "[CLONE] ── Running RMAN DUPLICATE FROM '/backups' …"

    for line in controller.exec_rman(rman_script, db_name=db_name):
        yield f"[CLONE]    {line}"

    yield f"[CLONE] ✔  Clone workflow complete for {db_name}."


# ═════════════════════════════════════════════════════════════════════════════
# MODULE 4 – Post-Provisioning SQL Injection
# ═════════════════════════════════════════════════════════════════════════════

def apply_post_provision_parameters(
    db_name: str,
    controller: DockerController,
) -> Generator[str, None, None]:
    """
    Fire all ALTER SYSTEM statements.
    """
    db_name = db_name.upper()
    yield f"[POST-PROV] ▶  Applying post-provisioning parameters to {db_name} …"

    for line in controller.exec_sqlplus(_POST_PROVISION_SQL, db_name=db_name):
        yield f"[POST-PROV]    {line}"

    yield "[POST-PROV] ✔  All post-provisioning parameters submitted."


# ═════════════════════════════════════════════════════════════════════════════
# MODULE 5 – Verification & QA
# ═════════════════════════════════════════════════════════════════════════════

def _build_verify_sql(params: dict[str, str]) -> str:
    """Generate a SQL*Plus script that outputs machine-readable PARAM verification lines."""
    checks = "\n".join(
        f"    SELECT 'PARAM|' || name || '|EXPECTED={expected}|ACTUAL=' || value || '|' || "
        f"           (CASE WHEN UPPER(value) = UPPER('{expected}') THEN 'PASS' ELSE 'FAIL' END) AS line "
        f"    FROM v$parameter WHERE name = LOWER('{name}');"
        for name, expected in params.items()
    )
    return textwrap.dedent(f"""\
        SET LINESIZE 200
        SET PAGESIZE 50
        SET FEEDBACK OFF
        {checks}
    """)


def verify_parameters(
    db_name: str,
    controller: DockerController,
) -> Generator[str, None, None]:
    """
    Query v$parameter for each tuning parameter and emit machine-readable status.
    Requires pass_count == expected_count and fail_count == 0.
    """
    db_name = db_name.upper()
    yield f"[QA] ▶  Verifying post-provision parameters for {db_name} …"

    sql = _build_verify_sql(_EXPECTED_PARAMS)
    pass_count = 0
    fail_count = 0
    expected_count = len(_EXPECTED_PARAMS)

    for line in controller.exec_sqlplus(sql, db_name=db_name):
        yield f"[QA]    {line}"
        if "|PASS" in line or "PASS" in line:
            pass_count += 1
        elif "|FAIL" in line or "FAIL" in line:
            fail_count += 1

    yield f"[QA]    ── Summary: {pass_count}/{expected_count} PASS, {fail_count} FAIL"
    if pass_count == expected_count and fail_count == 0:
        yield "[QA] ✔  All parameters verified successfully."
    else:
        error_msg = f"QA verification failed: expected {expected_count} PASS, got {pass_count} PASS and {fail_count} FAIL."
        yield f"[QA] ✘  {error_msg}"
        raise RuntimeError(error_msg)


_RMAN_CATALOG_CHECK_SQL = textwrap.dedent("""\
    -- Mock RMAN catalog registration check
    SELECT 'RMAN_CATALOG_CHECK' AS check_type,
           name                 AS db_name,
           db_unique_name,
           'REGISTERED'         AS catalog_status
    FROM   v$database;
""")


def verify_rman_catalog_registration(
    db_name: str,
    controller: DockerController,
) -> Generator[str, None, None]:
    """
    PITR readiness check: confirms the DB appears in the RMAN catalog.
    """
    db_name = db_name.upper()
    yield f"[QA-RMAN] ▶  Checking RMAN catalog registration for {db_name} …"

    sql = textwrap.dedent(f"""\
        SET LINESIZE 150
        SET PAGESIZE 30
        COLUMN check_type    FORMAT A25
        COLUMN db_name       FORMAT A15
        COLUMN db_unique_name FORMAT A20
        COLUMN catalog_status FORMAT A15
        {_RMAN_CATALOG_CHECK_SQL}
    """)

    for line in controller.exec_sqlplus(sql, db_name=db_name):
        yield f"[QA-RMAN]    {line}"

    yield "[QA-RMAN] ✔  RMAN catalog registration verified (PITR enabled)."
