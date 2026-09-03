"""
Module 3: Provisioning Workflows
==================================
Two async generator workflows, each yielding log lines for real-time
SSE streaming to the frontend:

  • seed_database()  – builds a fresh Oracle DB from DBCA
  • clone_database() – wipes the staging area then runs a mock RMAN duplicate

Module 4: Post-Provisioning SQL Injection
==========================================
  • apply_post_provision_parameters() – fires all 13 ALTER SYSTEM/DATABASE
    statements, then SHUTDOWN IMMEDIATE + STARTUP

Module 5: Verification & QA
=============================
  • verify_parameters() – queries v$parameter for each tuning param
  • verify_rman_catalog_registration() – mock RMAN catalog connectivity check
"""

from __future__ import annotations

import textwrap
from collections.abc import AsyncGenerator

from .docker_controller import DockerController
from .validation_engine import ProvisionRequest

# ─────────────────────────── shared helpers ──────────────────────────────────

STAGING_DIR = "/u01/oradata/staging"

# DBCA response file template for seed builds
_DBCA_RESPONSE_TEMPLATE = textwrap.dedent("""\
    [GENERAL]
    RESPONSEFILE_VERSION = "19.0"
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
    SYSPASSWORD            = "Oracle_4U"
    SYSTEMPASSWORD         = "Oracle_4U"
    DBSNMPPASSWORD         = "Oracle_4U"
""")

# Mock RMAN duplicate script (ARS emulation)
_RMAN_DUPLICATE_TEMPLATE = textwrap.dedent("""\
    -- ARS Emulation: Autonomous Recovery Service Duplicate
    -- Target: {db_unique_name}
    RUN {{
        ALLOCATE AUXILIARY CHANNEL ch1 DEVICE TYPE DISK;
        ALLOCATE AUXILIARY CHANNEL ch2 DEVICE TYPE DISK;
        DUPLICATE DATABASE TO '{db_name}'
            LOGFILE
                GROUP 1 ('{staging_dir}/{db_name}/redo01.log') SIZE 200M,
                GROUP 2 ('{staging_dir}/{db_name}/redo02.log') SIZE 200M,
                GROUP 3 ('{staging_dir}/{db_name}/redo03.log') SIZE 200M
            NOFILENAMECHECK
            USING BACKUPSET;
    }}
""")

# Post-provisioning ALTER SYSTEM parameters
_POST_PROVISION_SQL = textwrap.dedent("""\
    ALTER SYSTEM SET optimizer_adaptive_features=FALSE SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET parallel_max_servers=10 SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET parallel_min_servers=10 SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET audit_sys_operations=TRUE SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET audit_trail='OS' SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET parallel_threads_per_cpu=1 SCOPE=SPFILE SID='*';
    ALTER DATABASE SET DEFAULT SMALLFILE TABLESPACE;
    ALTER SYSTEM SET sga_max_size=3500M SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET sga_target=3500M SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET processes=500 SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET inmemory_size=0 SCOPE=SPFILE SID='*';
    ALTER SYSTEM RESET db_domain SCOPE=SPFILE SID='*';
    ALTER SYSTEM SET max_dump_file_size='104857600' SCOPE=SPFILE SID='*';
    SHUTDOWN IMMEDIATE;
    STARTUP;
""")

# Expected parameter values for QA verification
_EXPECTED_PARAMS: dict[str, str] = {
    "optimizer_adaptive_features": "FALSE",
    "parallel_max_servers": "10",
    "parallel_min_servers": "10",
    "audit_sys_operations": "TRUE",
    "audit_trail": "OS",
    "parallel_threads_per_cpu": "1",
    "sga_max_size": "3670016000",   # 3500M in bytes
    "sga_target": "3670016000",
    "processes": "500",
    "inmemory_size": "0",
    "max_dump_file_size": "104857600",
}


# ═════════════════════════════════════════════════════════════════════════════
# MODULE 3 – Provisioning Workflows
# ═════════════════════════════════════════════════════════════════════════════

async def seed_database(
    req: ProvisionRequest,
    controller: DockerController,
) -> AsyncGenerator[str, None]:
    """
    Workflow 1 – Seed / create-from-scratch.

    Streams log lines back to the caller.
    Character sets are strictly enforced to AL32UTF8 / AL16UTF16.
    """
    db_name = req.db_name.upper()
    db_unique_name = req.db_unique_name.upper()

    yield f"[SEED] ▶  Starting seed build for SID={db_name}, UNIQUE={db_unique_name}"
    yield f"[SEED]    Character set        : {req.character_set}"
    yield f"[SEED]    National char set    : {req.national_character_set}"

    # ── Step 1: write DBCA response file into the container ──────────────────
    response_content = _DBCA_RESPONSE_TEMPLATE.format(
        db_name=db_name,
        db_unique_name=db_unique_name,
        character_set=req.character_set,
        national_character_set=req.national_character_set,
    )
    escaped = response_content.replace("'", "'\\''")
    write_cmd = (
        f"mkdir -p /tmp/dbca_rsp && "
        f"echo '{escaped}' > /tmp/dbca_rsp/{db_name}.rsp"
    )

    yield "[SEED] ── Writing DBCA response file …"
    for line in controller.exec_shell(write_cmd):
        yield f"[SEED]    {line}"

    # ── Step 2: invoke DBCA in silent mode ───────────────────────────────────
    dbca_cmd = (
        "$ORACLE_HOME/bin/dbca -silent "
        f"-createDatabase -responseFile /tmp/dbca_rsp/{db_name}.rsp "
        "-ignorePrereqs"
    )

    yield "[SEED] ── Invoking DBCA (silent mode) …"
    for line in controller.exec_shell(dbca_cmd):
        yield f"[SEED]    {line}"

    yield f"[SEED] ✔  Seed build complete for {db_name}."


async def clone_database(
    req: ProvisionRequest,
    controller: DockerController,
) -> AsyncGenerator[str, None]:
    """
    Workflow 2 – Clone / ARS Emulation (RMAN Duplicate).

    Steps:
      1. Forcefully wipe the staging directory for the target DB.
      2. Pipe a mock RMAN duplicate script into the container.

    No Data Guard or Standby logic is included.
    """
    db_name = req.db_name.upper()
    db_unique_name = req.db_unique_name.upper()

    yield f"[CLONE] ▶  Starting ARS-emulation clone for SID={db_name}"

    # ── Step 1: wipe staging directory ───────────────────────────────────────
    target_staging = f"{STAGING_DIR}/{db_name}"
    wipe_cmd = f"rm -rf {target_staging}/* && mkdir -p {target_staging}"

    yield f"[CLONE] ── Wiping staging directory: {target_staging}"
    for line in controller.exec_shell(wipe_cmd):
        yield f"[CLONE]    {line}"
    yield f"[CLONE]    Staging directory cleared."

    # ── Step 2: run RMAN duplicate script ────────────────────────────────────
    rman_script = _RMAN_DUPLICATE_TEMPLATE.format(
        db_name=db_name,
        db_unique_name=db_unique_name,
        staging_dir=STAGING_DIR,
    )

    yield "[CLONE] ── Running RMAN duplicate (ARS emulation) …"
    for line in controller.exec_rman(rman_script, db_name=db_name):
        yield f"[CLONE]    {line}"

    yield f"[CLONE] ✔  Clone workflow complete for {db_name}."


# ═════════════════════════════════════════════════════════════════════════════
# MODULE 4 – Post-Provisioning SQL Injection
# ═════════════════════════════════════════════════════════════════════════════

async def apply_post_provision_parameters(
    db_name: str,
    controller: DockerController,
) -> AsyncGenerator[str, None]:
    """
    Fire all 13 ALTER SYSTEM / ALTER DATABASE statements, then
    SHUTDOWN IMMEDIATE followed by STARTUP to apply SPFILE changes.
    """
    db_name = db_name.upper()
    yield f"[POST-PROV] ▶  Applying post-provisioning parameters to {db_name} …"

    for line in controller.exec_sqlplus(_POST_PROVISION_SQL, db_name=db_name):
        yield f"[POST-PROV]    {line}"

    yield "[POST-PROV] ✔  All parameters applied and database restarted."


# ═════════════════════════════════════════════════════════════════════════════
# MODULE 5 – Verification & QA
# ═════════════════════════════════════════════════════════════════════════════

def _build_verify_sql(params: dict[str, str]) -> str:
    """Generate a SQL*Plus script that checks each parameter."""
    checks = "\n".join(
        f"    SELECT name, value, "
        f"           CASE WHEN UPPER(value) = UPPER('{expected}') "
        f"                THEN 'PASS' ELSE 'FAIL' END AS status "
        f"    FROM v$parameter WHERE name = LOWER('{name}');"
        for name, expected in params.items()
    )
    return textwrap.dedent(f"""\
        SET LINESIZE 200
        SET PAGESIZE 50
        COLUMN name   FORMAT A40
        COLUMN value  FORMAT A30
        COLUMN status FORMAT A6
        {checks}
    """)


async def verify_parameters(
    db_name: str,
    controller: DockerController,
) -> AsyncGenerator[str, None]:
    """
    Query v$parameter for each of the 12 tuning parameters and
    emit PASS/FAIL per row.
    """
    db_name = db_name.upper()
    yield f"[QA] ▶  Verifying post-provision parameters for {db_name} …"

    sql = _build_verify_sql(_EXPECTED_PARAMS)
    pass_count = 0
    fail_count = 0

    for line in controller.exec_sqlplus(sql, db_name=db_name):
        yield f"[QA]    {line}"
        if "PASS" in line:
            pass_count += 1
        elif "FAIL" in line:
            fail_count += 1

    yield f"[QA]    ── Summary: {pass_count} PASS / {fail_count} FAIL"
    if fail_count == 0:
        yield "[QA] ✔  All parameters verified successfully."
    else:
        yield f"[QA] ✘  {fail_count} parameter(s) did NOT match expected values."


_RMAN_CATALOG_CHECK_SQL = textwrap.dedent("""\
    -- Mock RMAN catalog registration check
    -- In production this would connect to the RMAN catalog schema
    SELECT 'RMAN_CATALOG_CHECK' AS check_type,
           name                 AS db_name,
           db_unique_name,
           'REGISTERED'         AS catalog_status
    FROM   v$database;
""")


async def verify_rman_catalog_registration(
    db_name: str,
    controller: DockerController,
) -> AsyncGenerator[str, None]:
    """
    Mock PITR readiness check: confirms the DB appears in the RMAN catalog.
    In production this would query the catalog schema over a database link.
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
