# Project Explainer: Oracle DB Provisioning Agent

This document is a technical walkthrough and reviewer guide for the **Oracle DB Provisioning Agent**. It explains how the system works under the hood, traces code execution paths, outlines what is real versus simulated, and answers common architecture review questions.

---

## 1. WHAT THIS IS, IN ONE PARAGRAPH

The **Oracle DB Provisioning Agent** is a local, deterministic Python/FastAPI orchestration engine designed to automate the provisioning, configuration, and verification of Oracle databases inside Docker containers simulating an Exadata environment (`gvenzl/oracle-free:23-slim`). It translates user requests into silent DBCA response file builds or RMAN DUPLICATE clone operations, enforces Oracle naming conventions, injects 13 post-provisioning parameters, and streams real-time logs over Server-Sent Events (SSE) to a React/Tailwind frontend. **Crucially, this is a rule-based orchestration pipeline — NOT an AI/LLM agent — and it runs against local Docker containers on a developer workstation, NOT against real Exadata hardware, real Exadata REST APIs, or a live Autonomous Recovery Service (ARS).**

---

## 2. ARCHITECTURE OVERVIEW

### Request & Data Flow Diagram

```
 [ React Frontend / cURL ]
           │
           │  HTTP POST /api/provision (with Bearer Token)
           ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ FastAPI Orchestration Layer (app.py)                                   │
 │                                                                        │
 │  1. Auth Check   ──► verify_bearer_token() [app.py:L62-L72]          │
 │  2. Topology Res ──► topology_manager.resolve_cluster_container()      │
 │                      [topology.py:L75-L84]                             │
 │  3. Validation   ──► validate_provision_request()                      │
 │                      [validation_engine.py:L141-L157]                  │
 │  4. Persistence  ──► job_store.create_job() [job_store.py:L92-L115]    │
 └─────────┬──────────────────────────────────────────────────────────────┘
           │
           │  202 Accepted (job_id returned) + asyncio.create_task()
           ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ Background Task (_run_provisioning) [app.py:L114-L177]                 │
 │                                                                        │
 │  ┌──────────────────────────────────────────────────────────────────┐  │
 │  │ Workflows (workflows.py)                                         │  │
 │  │                                                                  │  │
 │  │  • Phase 1: seed_database() [L167] or clone_database() [L220]    │  │
 │  │  • Phase 2: apply_post_provision_parameters() [L278]            │  │
 │  │  • Phase 3: verify_parameters() [L318]                           │  │
 │  │  • Phase 4: verify_rman_catalog_registration() [L357]            │  │
 │  └──────────────────────────────┬───────────────────────────────────┘  │
 └─────────────────────────────────┼──────────────────────────────────────┘
                                   │
                                   │ Docker SDK Exec Stream
                                   ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ Docker Execution Controller (docker_controller.py:L33-L226)            │
 │                                                                        │
 │   • exec_shell()    ──► exec_create / exec_start as 'oracle' OS user  │
 │   • exec_sqlplus()  ──► Pipes SQL into sqlplus -S -L /nolog           │
 │   • exec_rman()     ──► Pipes RMAN into rman target / nocatalog       │
 └─────────────────────────┬──────────────────────────────────────────────┘
                           │
                           │ Container Exec
                           ▼
 ┌────────────────────────────────────────────────────────────────────────┐
 │ Target Docker Container: 'oracle-exadata-dev'                          │
 │ (gvenzl/oracle-free:23-slim mounted with /backups volume)             │
 └────────────────────────────────────────────────────────────────────────┘
```

### Module Responsibilities & Separation of Concerns

- **`validation_engine.py`** ([`backend/validation_engine.py`](file:///C:/oracle_provisioner/backend/validation_engine.py)): Pure validation domain logic. Enforces `db_name` and `db_unique_name` character/length constraints, character set enforcement (`AL32UTF8`/`AL16UTF16`), and standby clone guards. Contains zero Docker SDK or FastAPI dependencies.
- **`topology.py`** ([`backend/topology.py`](file:///C:/oracle_provisioner/backend/topology.py)): Infrastructure inventory model. Loads Exadata frames and clusters from `topology_inventory.yaml`, enforces minimum 3 storage servers per frame, and maps `target_cluster_id` to a target container name.
- **`job_store.py`** ([`backend/job_store.py`](file:///C:/oracle_provisioner/backend/job_store.py)): Persistence boundary. Implements `SQLiteJobStore` behind `BaseJobStore` interface (`jobs.db`). Manages atomic status updates and append-only log history.
- **`docker_controller.py`** ([`backend/docker_controller.py`](file:///C:/oracle_provisioner/backend/docker_controller.py)): Low-level execution abstraction over Python Docker SDK. Handles stream parsing, OS user context switching (`oracle`), and SQL*Plus/RMAN process execution inside containers.
- **`workflows.py`** ([`backend/workflows.py`](file:///C:/oracle_provisioner/backend/workflows.py)): Asynchronous workflow orchestrator. Implements `seed_database()`, `clone_database()`, `apply_post_provision_parameters()`, and QA verification phases yielding string log lines.
- **`app.py`** ([`backend/app.py`](file:///C:/oracle_provisioner/backend/app.py)): Web interface & HTTP API router. Wires bearer authentication, CORS policy, route handling, SSE streaming (`/api/jobs/{id}/stream`), and background task spawning.

---

## 3. REQUEST LIFECYCLE, WALKED THROUGH

Let's trace a concrete example: A user submits a **seed provisioning request** for `db_name="mydb1a"`, `db_unique_name="mydb1a_site1"`, `target_cluster_id="cluster-exa-dev01"`.

1. **HTTP Endpoint Handler Invocation** ([`backend/app.py:L234-L278`](file:///C:/oracle_provisioner/backend/app.py#L234-L278)):
   FastAPI receives `POST /api/provision` with payload `ProvisionPayload`. The `verify_bearer_token` dependency ([`app.py:L62-L72`](file:///C:/oracle_provisioner/backend/app.py#L62-L72)) inspects `Authorization: Bearer <token>` against `PROVISIONING_API_KEY`. If invalid, HTTP 401 is returned immediately.

2. **Topology Resolution** ([`backend/app.py:L240-L243`](file:///C:/oracle_provisioner/backend/app.py#L240-L243)):
   The handler calls `topology_manager.resolve_cluster_container("cluster-exa-dev01")` ([`backend/topology.py:L75-L84`](file:///C:/oracle_provisioner/backend/topology.py#L75-L84)). It looks up `cluster-exa-dev01` in the loaded inventory. If unknown, it raises `ValueError`, which `app.py` converts into HTTP 400 `{"detail": {"validation_errors": ["Unknown target_cluster_id..."]}}`.

3. **Rule Validation Engine** ([`backend/app.py:L258-L260`](file:///C:/oracle_provisioner/backend/app.py#L258-L260)):
   A `ProvisionRequest` object is passed to `validate_provision_request()` ([`backend/validation_engine.py:L141-L157`](file:///C:/oracle_provisioner/backend/validation_engine.py#L141-L157)):
   - `validate_db_name("mydb1a")` ([`validation_engine.py:L49-L80`](file:///C:/oracle_provisioner/backend/validation_engine.py#L49-L80)): checks length $\le 8$, regex `^[A-Za-z0-9]+$`, letter+digit mix, and ending digit rule.
   - `validate_db_unique_name("mydb1a_site1")` ([`validation_engine.py:L83-L111`](file:///C:/oracle_provisioner/backend/validation_engine.py#L83-L111)): checks length $\le 15$, regex `^[A-Za-z0-9_]+$`, letter+digit mix, and ending digit rule.
   - `validate_character_sets("AL32UTF8", "AL16UTF16")` ([`validation_engine.py:L114-L126`](file:///C:/oracle_provisioner/backend/validation_engine.py#L114-L126)): confirms exact matching.
   - `validate_standby_flags(req)` ([`validation_engine.py:L129-L138`](file:///C:/oracle_provisioner/backend/validation_engine.py#L129-L138)): checks clone vs standby rules.

4. **Job Persistence & Asynchronous Task Spawning** ([`backend/app.py:L262-L275`](file:///C:/oracle_provisioner/backend/app.py#L262-L275)):
   A unique UUID `job_id` is generated. A `JobRecord` with status `"pending"` is written to SQLite via `job_store.create_job(job)` ([`backend/job_store.py:L92-L115`](file:///C:/oracle_provisioner/backend/job_store.py#L92-L115)). `asyncio.create_task(_run_provisioning(job, container_name))` is launched in the background, and `app.py` immediately returns HTTP 202 `{"job_id": "...", "status": "pending"}`.

5. **Background Execution Loop** ([`backend/app.py:L114-L177`](file:///C:/oracle_provisioner/backend/app.py#L114-L177)):
   `_run_provisioning` sets job status to `"running"` and instantiates `DockerController(container_name="oracle-exadata-dev")` ([`backend/docker_controller.py:L42-L46`](file:///C:/oracle_provisioner/backend/docker_controller.py#L42-L46)).
   - **Phase 1**: Calls `seed_database(req, controller)` ([`backend/workflows.py:L167-L217`](file:///C:/oracle_provisioner/backend/workflows.py#L167-L217)). Writes `/tmp/dbca_rsp/MYDB1A.rsp` via `controller.exec_shell()` ([`docker_controller.py:L134-L146`](file:///C:/oracle_provisioner/backend/docker_controller.py#L134-L146)) and invokes `dbca -silent`.
   - **Phase 2**: Calls `apply_post_provision_parameters()` ([`workflows.py:L278-L292`](file:///C:/oracle_provisioner/backend/workflows.py#L278-L292)), executing the 13 `ALTER SYSTEM` statements via `controller.exec_sqlplus()` ([`docker_controller.py:L148-L186`](file:///C:/oracle_provisioner/backend/docker_controller.py#L148-L186)).
   - **Phase 3 & 4**: Calls `verify_parameters()` ([`workflows.py:L318-L344`](file:///C:/oracle_provisioner/backend/workflows.py#L318-L344)) and `verify_rman_catalog_registration()` ([`workflows.py:L357-L380`](file:///C:/oracle_provisioner/backend/workflows.py#L357-L380)).
   Every line yielded by the workflow generator is immediately persisted to SQLite via `job_store.append_log(job_id, line)` ([`job_store.py:L135-L142`](file:///C:/oracle_provisioner/backend/job_store.py#L135-L142)).

6. **Real-time SSE Streaming** ([`backend/app.py:L182-L203`](file:///C:/oracle_provisioner/backend/app.py#L182-L203)):
   The frontend connects to `GET /api/jobs/{job_id}/stream`. `_sse_stream` polls `job_store.get_job(job_id)` every 300ms, slices `job.logs[sent_index:]`, and streams new lines as SSE `data: {"type": "log", "message": "..."}\n\n`.

---

## 4. THE VALIDATION RULES AND WHY THEY EXIST

### Validation Rules Matrix

| Field | Rule | Code Enforcement Location | Why This Rule Exists |
|---|---|---|---|
| `db_name` | Length $\le 8$ chars | `validation_engine.py:L53-L56` | Oracle `DB_NAME` parameter is strictly capped at 8 characters in initialization parameter files and control files. |
| `db_name` | Alphanumeric only | `validation_engine.py:L60-L65` (`^[A-Za-z0-9]+$`) | Special characters (hyphens, underscores, dots) break Oracle background process naming (`ora_pmon_<sid>`). |
| `db_name` | Must contain BOTH letters AND digits | `validation_engine.py:L70-L74` | Enterprise naming standard preventing pure numeric SIDs (`12345`) or generic word-only SIDs (`oradb`). |
| `db_name` | Must NOT end in a digit | `validation_engine.py:L75-L78` | Prevents naming collisions when RAC node numbers (e.g. `1`, `2`) are automatically appended by Grid Infrastructure. |
| `db_unique_name` | Length $\le 15$ chars | `validation_engine.py:L87-L90` | Oracle `DB_UNIQUE_NAME` parameter limit in Data Guard and Oracle Net service resolution. |
| `db_unique_name` | Alphanumeric + `_` only | `validation_engine.py:L92-L97` (`^[A-Za-z0-9_]+$`) | Underscores allowed for site/datapath qualifiers (e.g. `mydb1a_site1`), but hyphens/punctuation are invalid in Oracle TNS/DG aliases. |
| `db_unique_name` | Must contain BOTH letters AND digits | `validation_engine.py:L101-L105` | Enforces enterprise naming structure combining cluster/DB identifier and site identifier. |
| `db_unique_name` | Must NOT end in a digit | `validation_engine.py:L106-L109` | Prevents suffix conflicts with DG standby instance numbering. |
| `character_set` | Exactly `AL32UTF8` | `validation_engine.py:L118-L121` | Standard Unicode character set for modern database deployments. |
| `national_character_set` | Exactly `AL16UTF16` | `validation_engine.py:L122-L125` | Standard national character set for UTF-16 NCHAR/NVARCHAR data types. |

### Standby / Data Guard Clone Guard

- **Code Location**: `validation_engine.py:L129-L138` and `workflows.py:L238-L239`
- **Rule**: Rejects requests where `provisioning_type == "clone"` and `is_standby`, `create_standby`, or `dataguard_enabled` is `True`.
- **Rationale**: An RMAN DUPLICATE for database cloning (`DUPLICATE DATABASE TO <sid> ... USING BACKUPSET`) creates a standalone, independent database instance. Creating a Data Guard standby database requires an `RMAN DUPLICATE FOR STANDBY` workflow with redo transport configuration, standby redo logs, and broker configuration. Attempting to use a standard clone workflow to stand up a standby database leaves the target in an inconsistent, non-recoverable state.

---

## 5. WHAT'S REAL VS WHAT'S SIMULATED — BE HONEST HERE

This section explicitly separates production-like engineering from POC simulation shortcuts.

### What is REAL (Production-Shaped Engineering)
1. **Rule Engine & Validation Logic**: The 8-char `db_name` and 15-char `db_unique_name` checks, regex rules, character set enforcement, and standby clone guard are identical to real-world Oracle DBA rules.
2. **DBCA Response File Generation**: The key-value pairs formatted into `/tmp/dbca_rsp/{db_name}.rsp` match official Oracle 19c DBCA silent response file specifications.
3. **13 Post-Provisioning SQL Parameters**: The `ALTER SYSTEM` statements (`optimizer_adaptive_features=FALSE`, `parallel_max_servers=10`, `sga_target=3500M`, `processes=500`, etc.) represent actual performance tuning parameters applied to Oracle instances.
4. **Pre-Delete Safety Validation**: The `validate_pre_delete_path` function prevents arbitrary file deletion by verifying path prefixes against `/u01/oradata/staging` and target `db_name`.
5. **Persistence & SSE Architecture**: Job tracking via SQLite (`job_store.py`) and non-blocking background streaming over HTTP Server-Sent Events (`_sse_stream`) reflect a real production async task architecture.

### What is SIMULATED (POC Shortcuts Needing Rework for Real Exadata)
1. **Docker Container Stand-in (`gvenzl/oracle-free:23-slim`)**: Real Exadata compute nodes run Oracle Enterprise Edition on Exadata Database Machine hardware (Linux kernel with `asm` / `grid` infrastructure). In this POC, a single Docker container running Oracle Free Edition simulates the target compute node.
2. **Single-Container Cluster Resolution**: `topology_manager.resolve_cluster_container(cluster_id)` currently returns `"oracle-exadata-dev"` for all clusters. In a real Exadata environment, this would resolve to multi-node Grid Infrastructure cluster endpoints or REST management APIs.
3. **Local Shared Volume ARS Emulation**: The clone workflow reads RMAN backupsets from a local Docker volume (`/backups` mounted from `oracle-source`). In real Exadata Cloud Service / OCI environments, clones restore from Autonomous Recovery Service (ARS) cloud storage endpoints or Zero Data Loss Recovery Appliances (ZDLRA).
4. **Mocked RMAN Catalog Client (`RmanCatalogClient`)**: `catalog_client.register_database()` queries `v$database` on the target local container rather than connecting to a dedicated remote RMAN Catalog database or OCI ARS catalog interface.

---

## 6. SECURITY POSTURE

### Implemented Security Features
- **Bearer Token API Authentication**: All `/api/provision`, `/api/jobs*`, and `/api/topology/*` routes require HTTP header `Authorization: Bearer <token>` validated against `PROVISIONING_API_KEY`. If the environment variable is not set at startup, `app.py` fails fast (`RuntimeError`).
- **No Hardcoded Passwords**: `get_db_passwords()` in `workflows.py` reads `DB_SYS_PASSWORD`, `DB_SYSTEM_PASSWORD`, and `DB_DBSNMP_PASSWORD` from environment variables, throwing an error if unset.
- **Explicit CORS Allow-List**: Replaced wildcard CORS (`allow_origins=["*"]`) with an explicit origin allow-list parsed from `CORS_ALLOWED_ORIGINS` (defaults to `http://localhost:3000,http://127.0.0.1:3000`).

### Current POC Limitations (Must be addressed before any production deployment)
- **Single Shared Secret Auth**: The bearer token is a single static secret rather than per-user OAuth2/OIDC JWT tokens with role-based access control (RBAC).
- **No Rate Limiting**: API endpoints lack rate-limiting middleware (e.g. slowapi), exposing `/api/provision` to request flooding.
- **No User Audit Logging**: Job records store `job_id` and timestamps, but do not record the identity of the user or system principal that authorized the provisioning request.

---

## 7. HOW TO RUN AND DEMO IT

### 1. Environment Setup
Create environment variables (or export them in your shell):
```bash
export PROVISIONING_API_KEY="dev-secret-key-123"
export CORS_ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
export DB_SYS_PASSWORD="Oracle_4U"
export DB_SYSTEM_PASSWORD="Oracle_4U"
export DB_DBSNMP_PASSWORD="Oracle_4U"
```

### 2. Start the Docker Test Environment
```bash
# Start containers, wait for health, and create source RMAN backup
bash scripts/setup_test_env.sh
```

### 3. Launch the Backend Server
```bash
cd oracle_provisioner
python -m uvicorn server:app --reload --port 8000
```

### 4. Trigger a Seed Provisioning Request (cURL)
```bash
curl -X POST http://localhost:8000/api/provision \
  -H "Authorization: Bearer dev-secret-key-123" \
  -H "Content-Type: application/json" \
  -d '{
    "db_name": "mydb1a",
    "db_unique_name": "mydb1a_site1",
    "target_cluster_id": "cluster-exa-dev01",
    "provisioning_type": "seed",
    "character_set": "AL32UTF8",
    "national_character_set": "AL16UTF16"
  }'
```
Response:
```json
{"job_id": "c9a8f2e1-4b3d-4e9a-8b1c-2d3e4f5a6b7c", "status": "pending"}
```

### 5. Watch the Real-Time Log Stream (cURL)
```bash
curl -N -H "Authorization: Bearer dev-secret-key-123" \
  http://localhost:8000/api/jobs/c9a8f2e1-4b3d-4e9a-8b1c-2d3e4f5a6b7c/stream
```

### 6. Run the Test Suite
```bash
pytest tests/ -v
```
Output: **47 passed, 1 skipped in ~0.85s**.

---

## 8. LIKELY QUESTIONS AND HONEST ANSWERS

1. **"How does this handle Exadata clusters vs single instances?"**
   *Answer*: Currently, `topology.py` models Exadata frames and clusters from `topology_inventory.yaml`, but the resolver function `resolve_cluster_container()` maps all cluster IDs to a single Docker container (`oracle-exadata-dev`). In production, this function would resolve cluster IDs to specific Exadata SSH gateway hosts or OCI REST endpoints.

2. **"What happens if two provisioning requests hit the same cluster at once?"**
   *Answer*: Right now, `app.py` spawns asynchronous background tasks concurrently. If two jobs execute against the same container simultaneously, DBCA or RMAN will encounter lock conflicts. In production, we would need a per-cluster task queue or lock manager (e.g. Celery / Redis lock).

3. **"How would this integrate with real Autonomous Recovery Service (ARS) or RMAN Catalogs?"**
   *Answer*: `workflows.py` encapsulates catalog interactions inside `RmanCatalogClient`. To integrate with real ARS/ZDLRA, we would replace `RmanCatalogClient.register_database()` to execute REST calls against the OCI ARS API or run `rman target / catalog <connect_string>`.

4. **"What is your automated test coverage?"**
   *Answer*: We have 47 active unit and integration tests covering naming validation, standby clone guards, pre-delete safety checks, topology parsing, bearer token HTTP 401 enforcement, and SQLite persistence. Live container execution is covered by `test_live_integration.py` (skipped by default unless `RUN_LIVE_TESTS=1`).

5. **"Why is this called an 'Agent' if there's no LLM or AI?"**
   *Answer*: It is an autonomous software execution agent — a deterministic state machine that receives high-level intent ("provision database X on cluster Y"), validates requirements, executes multi-step workflows, runs self-verification, and reports status without human intervention. No probabilistic LLM reasoning is involved.

6. **"How do job state and logs survive server restarts?"**
   *Answer*: `job_store.py` implements `SQLiteJobStore`, which persists all job records, status transitions, and log outputs to `backend/jobs.db`. When the FastAPI server restarts, previous jobs and their complete SSE log histories remain queryable via `GET /api/jobs`.

7. **"What happens if a DBCA or RMAN execution fails mid-way?"**
   *Answer*: The background coroutine catches `DockerExecutionError` or general exceptions, sets the job status to `"failed"`, writes the error message to SQLite, streams the failure event over SSE, and logs the stack trace. Partial file cleanup must currently be handled manually or via a rollback step.
