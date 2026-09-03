# Autonomous Oracle DB Provisioning Agent

Autonomous provisioning and topology orchestration of Oracle databases inside a Docker-hosted Exadata simulation environment.

---

> [!IMPORTANT]
> **What This Is / Is Not**:
> - **What it IS**: A local Docker-based Proof of Concept (POC) demonstrating topology resolution, validation, DBCA seed builds, RMAN DUPLICATE clone workflows, post-provisioning parameter injection, QA verification, and persistent job tracking.
> - **What it IS NOT**: This agent does **NOT** connect to real Oracle Exadata hardware, Exadata REST APIs, or an Autonomous Recovery Service (ARS) cloud endpoint. All actions execute locally inside Docker containers.

---

## Architecture Diagram

```
                                  ┌───────────────────────────────┐
                                  │   React / Tailwind Frontend   │
                                  │   ("DB Create Menu" UI)       │
                                  └──────────────┬────────────────┘
                                                 │ HTTP / Bearer Auth / SSE
                                                 ▼
┌────────────────────────────────────────────────────────────────────────────────────────┐
│  Python / FastAPI Orchestration Layer                                                  │
│                                                                                        │
│  ┌─────────────────────────┐  HTTP 400   ┌──────────────────────────┐                  │
│  │ Module 1: Validation    ├────────────►│ Return JSON Error List   │                  │
│  │ Engine & Standby Guard  │             └──────────────────────────┘                  │
│  └────────────┬────────────┘                                                           │
│               │ HTTP 202                                                               │
│               ▼                                                                        │
│  ┌─────────────────────────┐             ┌──────────────────────────┐                  │
│  │ Module 2: Topology     ├────────────►│ Inventory YAML           │                  │
│  │ Manager                 │             │ Cluster -> Container Res.│                  │
│  └────────────┬────────────┘             └──────────────────────────┘                  │
│               │                                                                        │
│               ▼                                                                        │
│  ┌─────────────────────────┐   DBCA      ┌──────────────────────────┐                  │
│  │ Module 3: Workflows     ├────────────►│ Seed Build (AL32UTF8)    │                  │
│  │ (Seed vs Clone)         │   RMAN      ├──────────────────────────┤                  │
│  └────────────┬────────────┘             │ ARS Duplicate (/backups) │                  │
│               │                          └──────────────────────────┘                  │
│               ▼                                                                        │
│  ┌─────────────────────────┐             ┌──────────────────────────┐                  │
│  │ Module 4: Post-Prov     ├────────────►│ 13 ALTER SYSTEM Params   │                  │
│  │ Parameter Injection     │             │ SHUTDOWN / STARTUP       │                  │
│  └────────────┬────────────┘             └──────────────────────────┘                  │
│               │                                                                        │
│               ▼                                                                        │
│  ┌─────────────────────────┐             ┌──────────────────────────┐                  │
│  │ Module 5: Verification  ├────────────►│ v$parameter Pass/Fail    │                  │
│  │ & QA                    │             │ RMAN Catalog PITR Check  │                  │
│  └────────────┬────────────┘             └──────────────────────────┘                  │
│               │                                                                        │
│               ▼                                                                        │
│  ┌─────────────────────────┐             ┌──────────────────────────┐                  │
│  │ JobStore Persistence    ├────────────►│ SQLite Database          │                  │
│  │ (SQLAlchemy / sqlite3)  │             │ (backend/jobs.db)        │                  │
│  └────────────┬────────────┘             └──────────────────────────┘                  │
│               │                                                                        │
│               ▼                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐                  │
│  │ Docker Execution Controller (Python Docker SDK)                  │                  │
│  └────────────────────────────┬─────────────────────────────────────┘                  │
└───────────────────────────────┼────────────────────────────────────────────────────────┘
                                │ Docker Exec
                                ▼
         ┌───────────────────────────────────────────────┐
         │ Docker Compose Test Stack                     │
         │                                               │
         │  [oracle-source]       [oracle-exadata-dev]  │
         │  (Auto-created DB)     (Software-only target) │
         │       │                      │                │
         │       └───────► /backups ◄───┘                │
         │            (ars_backups volume)               │
         └───────────────────────────────────────────────┘
```

---

## Docker Test Environment Setup

The test environment uses the official `gvenzl/oracle-free:23-slim` image and a shared Docker volume `ars_backups`.

### Stack Architecture
- **`oracle-source`**: Unmodified Oracle Free instance running on port `1522` that auto-creates a database on boot and writes an RMAN backupset to `/backups`.
- **`oracle-exadata-dev`**: Oracle Free instance running on port `1521` with entrypoint overridden to `tail -f /dev/null`. Acts as a software-only Exadata compute target into which the agent provisions databases.

### Automated Setup Script
Run the automated setup script to start the stack, wait for healthiness, generate the RMAN backup on `oracle-source`, and verify CLI tools:

```bash
# Linux / macOS
bash scripts/setup_test_env.sh

# Windows PowerShell
.\scripts\setup_test_env.ps1
```

### Automated Teardown Script
To bring down the compose stack:

```bash
# Keep the backup volume (default)
bash scripts/teardown_test_env.sh

# Prune the backup volume
bash scripts/teardown_test_env.sh --prune-volume
```

---

## Environment Variables

| Variable | Description | Default |
|---|---|---|
| `PROVISIONING_API_KEY` | Shared secret bearer token for API auth | `dev-secret-key-123` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated list of allowed CORS origins | `http://localhost:3000,http://127.0.0.1:3000` |
| `DB_SYS_PASSWORD` | SYS user password for DBCA response file | `ORACLE_PASSWORD` or `Oracle_4U` |
| `DB_SYSTEM_PASSWORD` | SYSTEM user password for DBCA response file | `ORACLE_PASSWORD` or `Oracle_4U` |
| `DB_DBSNMP_PASSWORD` | DBSNMP user password for DBCA response file | `ORACLE_PASSWORD` or `Oracle_4U` |
| `ORACLE_PASSWORD` | Fallback default password | `Oracle_4U` |

---

## Note on DBCA Execution Time

> [!NOTE]
> Executing `dbca -silent -createDatabase` to build an Oracle Database from scratch takes **10 to 30+ minutes** on a real run. The agent logs an early warning in the SSE log stream (`[SEED] ℹ NOTE: dbca -silent createDatabase typically takes 10-30+ minutes on a full run...`) to inform the operator that this delay is expected and not a system hang.

---

## Running the Backend & Frontend

### 1. Backend Server
```bash
cd C:\oracle_provisioner
python -m uvicorn server:app --reload --port 8000
```
- Interactive API Docs: `http://localhost:8000/docs`

### 2. Frontend UI Dashboard
```bash
cd C:\oracle_provisioner\frontend
npm run dev
```
- Dashboard UI: `http://localhost:3000`

---

## Running Automated Tests

```bash
cd C:\oracle_provisioner
pytest tests/ -v
```
All **47 unit and integration tests** pass out of the box in ~0.7 seconds.
To include the live container integration test against a running `oracle-exadata-dev` container:
```powershell
$env:RUN_LIVE_TESTS="1"; pytest tests/test_live_integration.py -v
```
