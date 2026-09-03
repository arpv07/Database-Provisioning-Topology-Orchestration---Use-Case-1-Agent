# Oracle DB Provisioning Agent

Autonomous provisioning of Oracle databases inside a Docker-hosted Exadata simulation environment.

---

## Project Structure

```
oracle_provisioner/
├── __init__.py
├── server.py                        # uvicorn entry point
├── backend/
│   ├── __init__.py
│   ├── validation_engine.py         # Module 1 – Request validation
│   ├── docker_controller.py         # Module 2 – Docker SDK abstraction
│   ├── workflows.py                 # Module 3 + 4 + 5 – Workflows, SQL injection, QA
│   ├── app.py                       # FastAPI application + routes
│   └── requirements.txt
├── tests/
│   ├── __init__.py
│   ├── test_validation.py           # Unit tests – validation engine
│   └── test_api.py                  # Integration tests – API endpoints
└── frontend/
    ├── index.html
    ├── package.json
    ├── vite.config.js
    ├── tailwind.config.js
    ├── postcss.config.js
    └── src/
        ├── main.jsx
        ├── index.css
        └── ProvisioningDashboard.jsx   # Module 6 – React UI
```

---

## Prerequisites

| Requirement | Version |
|---|---|
| Python | ≥ 3.11 |
| Node.js | ≥ 18 |
| Docker Desktop | running |
| Oracle Docker container | named `oracle-exadata-dev` |

### Oracle Docker Container (quick start)

The backend expects a container named **`oracle-exadata-dev`** that is already running. A minimal example using the official Oracle Database image:

```bash
docker run -d \
  --name oracle-exadata-dev \
  -p 1521:1521 \
  -e ORACLE_SID=ORCL \
  -e ORACLE_PWD=Oracle_4U \
  container-registry.oracle.com/database/enterprise:19.3.0.0
```

> The container must have `sqlplus`, `dbca`, and `rman` on the PATH for the `oracle` OS user.

---

## Backend Setup

```bash
# From the workspace root
cd oracle_provisioner/backend

# Create and activate a virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the API server
uvicorn oracle_provisioner.server:app --reload --port 8000 --app-dir ..
```

The API will be available at **http://localhost:8000**  
Interactive docs: **http://localhost:8000/docs**

---

## Frontend Setup

```bash
cd oracle_provisioner/frontend

# Install Node dependencies
npm install

# Start the dev server (proxies /api → localhost:8000)
npm run dev
```

The UI will open at **http://localhost:3000**

---

## Running Tests

```bash
# From the workspace root (where oracle_provisioner/ lives)
pip install pytest httpx

pytest oracle_provisioner/tests/ -v
```

---

## API Reference

### `POST /api/provision` → `202 Accepted`

Enqueue a new provisioning job. Returns immediately with a `job_id`.

```json
{
  "db_name": "mydb1a",
  "db_unique_name": "mydb1a_sitea",
  "provisioning_type": "seed",
  "character_set": "AL32UTF8",
  "national_character_set": "AL16UTF16"
}
```

**Validation errors** → `400 Bad Request`
```json
{
  "detail": {
    "validation_errors": [
      "db_name 'toolongname' exceeds 8 characters (length=11)."
    ]
  }
}
```

### `GET /api/jobs/{job_id}/stream`

Server-Sent Events stream. Connect with `EventSource` to receive real-time log lines.

```
data: {"type": "log", "message": "[SEED]    Invoking DBCA (silent mode) …"}
data: {"type": "status", "status": "completed", "error": null}
```

### `GET /api/jobs`

Returns all jobs grouped by status: `pending`, `running`, `completed`, `failed`.

### `GET /api/health`

Returns Docker container reachability.

---

## Validation Rules

### `db_name`
| Rule | Detail |
|---|---|
| Max length | ≤ 8 characters |
| Allowed chars | Letters and digits only (no special chars, no underscores) |
| Must contain | Both at least one letter AND one digit |
| End character | Must NOT end with a digit |

### `db_unique_name`
| Rule | Detail |
|---|---|
| Max length | ≤ 15 characters |
| Allowed chars | Letters, digits, and `_` only |
| Must contain | Both at least one letter AND one digit |
| End character | Must NOT end with a digit |

---

## Provisioning Pipeline (per job)

```
POST /api/provision
       │
       ▼
┌──────────────┐     400
│ Validation   ├────────────► Return errors immediately
│ Engine       │
└──────┬───────┘
       │ 202 Accepted
       ▼
┌──────────────────────────────────────────────────────────────┐
│  Background coroutine (_run_provisioning)                    │
│                                                              │
│  Phase 1 ── Provisioning Workflow                            │
│    seed  → DBCA response file + dbca -silent                 │
│    clone → rm -rf staging/* + RMAN duplicate script         │
│                                                              │
│  Phase 2 ── Post-Provisioning SQL Injection                  │
│    13 × ALTER SYSTEM/DATABASE + SHUTDOWN IMMEDIATE; STARTUP  │
│                                                              │
│  Phase 3 ── Parameter Verification (v$parameter)             │
│    12 parameter PASS/FAIL checks                             │
│                                                              │
│  Phase 4 ── RMAN Catalog Registration Check                  │
│    Mock PITR readiness query                                 │
└──────────────────────────────────────────────────────────────┘
       │
       ▼
  SSE stream → frontend LogViewer
```

---

## Architecture Decisions

- **Server-Sent Events** are used instead of WebSockets for the log stream because they are unidirectional, simpler to proxy, and natively supported by browsers without extra libraries.
- **In-memory job store** (`_jobs: dict`) is sufficient for a single-node agent. Replace with Redis or a database for multi-node deployments.
- **Character set enforcement** (`AL32UTF8` / `AL16UTF16`) is checked in the validation layer and hardcoded into the DBCA response file template, making it impossible to bypass.
- **Clone workflow** never uses Data Guard or Standby. It strictly follows the ARS-emulation path: wipe staging → RMAN DUPLICATE from BACKUPSET.
