# Workflow Orchestrator — Design Specification

**Date:** 2026-06-10
**Status:** Draft
**Version:** 1.0

## 1. Overview

工作流编排器是一个封装 Claude Code CLI 的自动化任务编排工具。它允许用户通过 JSON 文件定义工作流（DAG），编排器按拓扑顺序驱动节点执行，支持条件分支、断点续跑、session 复用、定时触发等功能。

### 1.1 Core Constraints

| Constraint | Decision |
|---|---|
| Tech Stack | Python 3.11+ |
| Deployment | Single machine, lightweight |
| Storage | SQLite + file system |
| User Interface | CLI (primary) + Web (full operational panel) |
| Concurrency | Multiple workflows in parallel |

### 1.2 Key Design Decisions

- **Architecture**: Modular monolith — single FastAPI process containing API, Web UI static files, workflow engine, and APScheduler
- **CLI model**: Thin client that calls FastAPI via HTTP (`httpx`), ensuring single-writer to SQLite
- **Web UI**: Lightweight SPA using Alpine.js + HTMX, served as static files by FastAPI
- **Node types**: `agent` (Claude Code CLI subprocess) and `script` (Python function call)
- **Session model**: Each agent node gets a fixed session-id on first execution; subsequent revisits resume the same session
- **Node completion**: Structured output validation via Pydantic models
- **Error handling**: Auto-retry with session resume (agent learns from failure), then manual intervention
- **Scheduling**: Cron-triggered workflow instances via APScheduler

---

## 2. Architecture

### 2.1 Layer Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                   User Interfaces                             │
│   CLI Tool (Click) ◄──HTTP──► Web Browser (Alpine.js + HTMX)│
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼──────────────────────────────────┐
│                 FastAPI Application                           │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐                         │
│  │  REST API    │  │  Web UI      │                         │
│  │  /api/v1/*   │  │  / (SPA)     │                         │
│  └──────┬───────┘  └──────────────┘                         │
│         │                                                     │
│  ┌──────▼──────────────────────────────────────────────────┐ │
│  │                 Service Layer                             │ │
│  │  WorkflowSvc  │  ExecutionSvc  │  ScheduleSvc            │ │
│  └──────┬────────────────┬────────────────┬────────────────┘ │
│         │                │                │                   │
│  ┌──────▼────────────────▼────────────────▼────────────────┐ │
│  │                 Core Engine                               │ │
│  │  State Machine  │  Session Manager  │  Node Executor     │ │
│  │                  │                   │  ├─ Agent Runner   │ │
│  │                  │                   │  └─ Script Runner  │ │
│  └──────────────────────────────────────────────────────────┘ │
│         │                                                     │
│  ┌──────▼──────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  SQLite         │  │  File Store  │  │  APScheduler │    │
│  └─────────────────┘  └──────────────┘  └──────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 Module Responsibilities

| Module | Responsibility |
|---|---|
| **REST API** (`api/`) | Route handlers, request validation, dependency injection |
| **Web UI** (`web/`) | Static SPA files (Alpine.js + HTMX) |
| **CLI** (`cli/`) | `wflow` command, subcommands mapped to API calls via `httpx` |
| **Service Layer** (`services/`) | Business logic: workflow CRUD, execution orchestration, schedule management |
| **Core Engine** (`engine/`) | State machine, topological execution, session lifecycle, node execution |
| **Adapters** (`adapters/`) | Claude CLI subprocess wrapper, Python script/function invoker |
| **Models** (`models/`) | SQLAlchemy ORM, Pydantic schemas for validation and serialization |

---

## 3. Data Model

### 3.1 SQLite Schema (6 tables)

#### workflow — Workflow definition

| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | UUID |
| `name` | TEXT | Human-readable name |
| `description` | TEXT | Description |
| `config` | TEXT | JSON configuration (nodes, edges, I/O, settings) |
| `created_at` | TEXT | ISO timestamp |
| `updated_at` | TEXT | ISO timestamp |

#### workflow_run — Run instance

| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | UUID |
| `workflow_id` | TEXT FK | → workflow.id |
| `status` | TEXT | `pending`\|`running`\|`paused`\|`completed`\|`failed` |
| `current_node_id` | TEXT | Currently executing node ID (nullable) |
| `context` | TEXT | JSON runtime context (variables, node outputs) |
| `started_at` | TEXT | ISO timestamp |
| `finished_at` | TEXT | ISO timestamp |

#### node_execution — Per-node execution record

| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | UUID |
| `run_id` | TEXT FK | → workflow_run.id |
| `node_id` | TEXT | Node ID from JSON config |
| `type` | TEXT | `script` \| `agent` |
| `session_id` | TEXT | Claude session ID (agent only) |
| `status` | TEXT | `pending`\|`running`\|`completed`\|`failed` |
| `retry_count` | INT | Number of retries attempted |
| `input` | TEXT | JSON input data |
| `output` | TEXT | JSON structured output (validated) |
| `error` | TEXT | Error message on failure |
| `started_at` | TEXT | ISO timestamp |
| `finished_at` | TEXT | ISO timestamp |

#### session — Claude CLI session

One session per (run_id, node_id) pair. The session persists across retries and loop-backs — subsequent node_execution records for the same node reference the same session.

| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | UUID (session-id) |
| `run_id` | TEXT FK | → workflow_run.id |
| `node_id` | TEXT | Node ID from JSON config |
| `session_path` | TEXT | Filesystem path to session data |
| `status` | TEXT | `active`\|`completed`\|`expired` |
| `created_at` | TEXT | ISO timestamp |

#### cron_job — Scheduled task

| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | UUID |
| `workflow_id` | TEXT FK | → workflow.id |
| `cron_expr` | TEXT | Cron expression (e.g., `0 9 * * *`) |
| `enabled` | INT | 0 or 1 |
| `last_run_id` | TEXT FK | → workflow_run.id (nullable) |
| `next_fire_at` | TEXT | ISO timestamp |
| `created_at` | TEXT | ISO timestamp |

#### run_log — Execution log

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Auto-increment |
| `run_id` | TEXT FK | → workflow_run.id |
| `node_id` | TEXT | Node ID |
| `level` | TEXT | `info`\|`warn`\|`error` |
| `message` | TEXT | Log content |
| `timestamp` | TEXT | ISO timestamp |

---

## 4. Workflow JSON Configuration Format

### 4.1 Top-Level Structure

```json
{
  "name": "code-review-workflow",
  "version": "1.0",
  "description": "Automated coding + review loop",
  "config": {
    "max_retries": 3,
    "retry_delay_seconds": 30,
    "timeout_seconds": 1800
  },
  "nodes": [...],
  "edges": [...],
  "inputs": {...},
  "outputs": {...},
  "notifications": {
    "on_complete": {
      "channel": "welink",
      "config": {
        "webhook_url": "https://welink.example.com/api/..."
      },
      "message_template": "✅ Workflow '{{ workflow.name }}' completed. Status: {{ run.status }}"
    },
    "on_failure": {
      "channel": "welink",
      "config": {
        "webhook_url": "https://welink.example.com/api/..."
      },
      "message_template": "❌ Workflow '{{ workflow.name }}' FAILED at node: {{ run.current_node_id }}"
    }
  }
}
```

### 4.2 Node Definitions

#### Agent Node

```json
{
  "id": "coding",
  "name": "Code Implementation",
  "type": "agent",
  "prompt": "Write code based on: {{ inputs.requirement }}. Previous review feedback: {{ inputs.review_feedback }}",
  "tools": {
    "allowed": ["Read", "Write", "Edit", "Bash", "Grep"],
    "disallowed": ["WebFetch"],
    "auto_approve": ["Read", "Grep"]
  },
  "model": "sonnet",
  "output": {
    "schema": {
      "type": "object",
      "properties": {
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "test_results": {"type": "string"}
      },
      "required": ["files_changed", "summary"]
    }
  },
  "retry": {
    "max_retries": 2,
    "on_error": ["timeout", "parse_error"]
  }
}
```

#### Script Node

```json
{
  "id": "validate_output",
  "name": "Output Validation",
  "type": "script",
  "script": {
    "module": "builtin.validators",
    "function": "check_test_pass",
    "args": {
      "output": "{{ nodes.coding.output }}",
      "threshold": "{{ config.threshold }}"
    }
  },
  "output": {
    "schema": {
      "type": "object",
      "properties": {
        "passed": {"type": "boolean"},
        "message": {"type": "string"}
      },
      "required": ["passed"]
    }
  }
}
```

**Script node output mechanism:** The referenced Python function receives the resolved `args` dict as keyword arguments and must return a `dict`. The returned dict is validated against `output.schema`. If the function raises a `ScriptError`, the node is marked as failed and retry logic applies (see Section 10).

### 4.3 Edge Definitions with Conditions

When multiple edges exit from a single node, they are evaluated in array order. The first edge whose `condition` evaluates to `true` is followed. If no conditions match, the first edge without a `condition` field is taken (default path). If no default edge exists and no conditions match, the run is paused with an error. This pattern implements if/else-if/else branching.

```json
{
  "edges": [
    {
      "id": "e1",
      "from": "coding",
      "to": "review"
    },
    {
      "id": "e2",
      "from": "review",
      "to": "coding",
      "condition": "{{ nodes.review.output.approved }} == false"
    },
    {
      "id": "e3",
      "from": "review",
      "to": "done",
      "condition": "{{ nodes.review.output.approved }} == true"
    },
    {
      "id": "e4",
      "from": "coding",
      "to": "fallback",
      "condition": "{{ nodes.coding.retry_count }} >= 3"
    }
  ]
}
```

### 4.4 Template Variable System

Variables use `{{ }}` syntax and are available in: `prompt`, `script.args`, `condition` expressions.

| Variable | Description |
|---|---|
| `{{ inputs.<key> }}` | Workflow-level input parameters |
| `{{ nodes.<node_id>.output.<key> }}` | Any upstream node's structured output |
| `{{ nodes.<node_id>.retry_count }}` | Node retry count |
| `{{ nodes.<node_id>.status }}` | Node status (`pending`\|`running`\|`completed`\|`failed`) |
| `{{ run.id }}` | Current run instance ID |
| `{{ config.<key> }}` | Global config value |

---

## 5. Engine Design

```
pending → running ⇄ paused → completed
                       └→ failed
```

**Transitions:**
- `pending → running`: Start execution
- `running → paused`: Pause request (after current node completes)
- `running → completed`: All nodes executed successfully
- `running → failed`: Unrecoverable node failure or stop request
- `paused → running`: Resume execution
- `failed → running`: Manual retry of failed node (session resume)

### 5.2 Node Execution Flow (Agent Type)

```
1. Look up session for (run_id, node_id)
   ├─ Found → Resume mode: claude --resume <session-id> -p "<prompt>"
   └─ Not found → Create mode: claude --session-id <new-uuid> -p "<prompt>"

2. Execute Claude CLI via subprocess
   - Apply tool configuration (--allowedTools, --disallowedTools)
   - Set timeout (timeout_seconds)
   - Capture stdout/stderr

3. Parse structured output
   ├─ Matches Pydantic schema → Node completed
   └─ Parse error:
       ├─ retry_count < max_retries → Resume same session, increment retry_count
       └─ retry_count exhausted → Node failed, escalate to run level
```

### 5.3 Session Management

| Rule | Description |
|---|---|
| **Creation** | First execution of an agent node creates a new session (UUID) |
| **Resume** | Same run revisits same node → resume existing session |
| **Retry** | Node retries use the same session (agent sees previous failure context) |
| **Isolation** | Sessions are NOT shared across different runs |
| **Retention** | Sessions retained for 30 days after workflow completion, then auto-cleaned |
| **Persistence** | Session mapping stored in `session` table; actual data managed by Claude CLI filesystem |

### 5.4 Service Restart Recovery

On FastAPI startup:
1. Initialize SQLite connection pool
2. Load `cron_job` table → register with APScheduler
3. Query `workflow_run WHERE status IN ('running', 'paused')`
   - `running`: Locate `current_node_id`, resume execution (create or resume session as needed)
   - `paused`: Keep paused, await manual resume
4. Start background session cleanup task

### 5.5 Execution Model

- **Intra-workflow**: Serial — nodes execute in topological order, one at a time
- **Inter-workflow**: Parallel — multiple workflow runs execute concurrently
- **DAG validation**: JSON config is validated at import time for cycles and missing references

---

## 6. API Design

### 6.1 Workflow Management (`/api/v1/workflows`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/workflows` | List all workflows (`?status=active`) |
| `POST` | `/workflows` | Create/import workflow (JSON body) |
| `GET` | `/workflows/{id}` | Get workflow detail (including JSON config) |
| `PUT` | `/workflows/{id}` | Update workflow configuration |
| `DELETE` | `/workflows/{id}` | Delete workflow and all associated data |

### 6.2 Run Management (`/api/v1/runs`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/runs` | List runs (`?workflow_id=&status=`) |
| `POST` | `/runs` | Start new run `{workflow_id, inputs: {...}}` |
| `GET` | `/runs/{id}` | Get run detail (status, current node, node outputs) |
| `POST` | `/runs/{id}/pause` | Pause run (after current node completes) |
| `POST` | `/runs/{id}/resume` | Resume paused run |
| `POST` | `/runs/{id}/stop` | Force stop run |
| `POST` | `/runs/{id}/retry-node` | Retry failed node (session resume) |
| `GET` | `/runs/{id}/logs` | Get run logs (`?level=error&limit=50`) |
| `GET` | `/runs/{id}/nodes/{nid}` | Get node execution detail |

### 6.3 Cron Management (`/api/v1/cron`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/cron` | List all cron jobs |
| `POST` | `/cron` | Create cron job `{workflow_id, cron_expr, inputs}` |
| `PUT` | `/cron/{id}` | Update cron job |
| `DELETE` | `/cron/{id}` | Delete cron job |
| `POST` | `/cron/{id}/trigger` | Manually trigger cron job |

### 6.4 Status (`/api/v1/status`)

| Method | Path | Description |
|---|---|---|
| `GET` | `/status` | Health check (running workflows, active sessions count) |
| `GET` | `/status/sessions` | List all active Claude sessions |

---

## 7. CLI Design

### 7.1 Command Structure

```
wflow                                    # Show help
├── wflow server                         # Start FastAPI server (background)
│     --host 127.0.0.1
│     --port 8100
│     --db ./data/workflows.db
│
├── wflow list                           # List workflows
│     --status active
│
├── wflow show <id>                      # Show workflow detail
├── wflow create <file.json>             # Create workflow from JSON file
│
├── wflow run <id>                       # Start workflow run
│     --input key=value                  # Input parameters (repeatable)
│     --watch                            # Follow logs in real-time
│
├── wflow status <run-id>                # Check run status
├── wflow pause <run-id>                 # Pause run
├── wflow resume <run-id>                # Resume run
├── wflow stop <run-id>                  # Stop run
│
├── wflow logs <run-id>                  # View run logs
│     --follow                           # Tail -f mode
│     --level error                      # Filter by level
│
├── wflow retry <run-id>                 # Retry failed node
│
├── wflow cron                           # Cron job management
│     ├── list
│     ├── add <workflow-id> <cron-expr>
│     ├── remove <cron-id>
│     └── toggle <cron-id>
│
└── wflow sessions                       # View active sessions
      --cleanup                          # Clean expired sessions
```

### 7.2 CLI → API Mapping

All CLI commands are thin wrappers that send HTTP requests to the FastAPI server via `httpx`. The server URL defaults to `http://127.0.0.1:8100` and is configurable via environment variable `WFLOW_SERVER_URL`.

---

## 8. Web UI Design

### 8.1 Technology

- Alpine.js for reactivity
- HTMX for partial page updates
- Served as static files by FastAPI
- Zero build step, no npm/webpack

### 8.2 Pages

| Page | Path | Features |
|---|---|---|
| **Dashboard** | `/` | Stats cards, recent runs table, quick-start button |
| **Run Detail** | `/runs/{id}` | Status bar + controls, node topology diagram, node detail panel (input/output JSON) |
| **Workflows** | `/workflows` | List/CRUD, JSON editor with preview, topology visualization |
| **Cron Jobs** | `/cron` | List, add/edit, enable/disable toggle, human-readable cron preview |

### 8.3 Node Topology Visualization

The run detail page renders the workflow DAG as an ASCII/SVG diagram with node status indicators:
- ✓ Completed (green)
- ◉ Running (blue, animated)
- ✗ Failed (red)
- ○ Pending (gray)

Current node is highlighted; clicking a node shows its input/output JSON.

---

## 9. Scheduler Design

### 9.1 Cron Job Lifecycle

```
Cron job created → Registered in APScheduler → Triggers on schedule → Creates workflow_run
```

- APScheduler jobs are stored in SQLite (`cron_job` table) for durability
- On service restart, all enabled cron jobs are re-registered
- Last triggered run is tracked via `last_run_id` FK
- Next fire time is computed and displayed

### 9.2 Cron Expression Support

Standard 5-field cron expressions: `minute hour day month weekday`

Examples:
- `0 9 * * *` — Daily at 9:00 AM
- `*/30 * * * *` — Every 30 minutes
- `0 9 * * 1-5` — Weekdays at 9:00 AM

---

## 10. Error Handling & Retry

### 10.1 Retry Strategy

| Scenario | Behavior |
|---|---|
| Claude CLI timeout | Retry (resume session), max_retries from node config |
| Structured output parse error | Retry (resume session), agent sees parse error in context |
| Script node exception | Retry, max_retries from node config |
| Retry count exhausted | Node marked `failed`, run status → `failed`, await manual intervention |
| Manual retry (`wflow retry`) | Resume node's session, reset retry_count, continue |

### 10.2 Error Logging

All node errors are logged to `run_log` table with level `error`, including:
- Full error message
- Node ID
- Timestamp
- Available via API `/runs/{id}/logs?level=error`

---

## 11. Notification Channels

### 11.1 Concept

After workflow completion (success or failure), the engine dispatches notifications to configured channels. Channels are pluggable adapters implementing a common interface.

### 11.2 Interface Contract

```python
from abc import ABC, abstractmethod
from typing import Any


class NotificationChannel(ABC):
    """Abstract interface for notification channels."""

    @abstractmethod
    async def send(self, message: str, config: dict[str, Any]) -> bool:
        """Send a notification message.

        Args:
            message: Rendered message text.
            config: Channel-specific configuration from workflow JSON.

        Returns:
            True if sent successfully, False otherwise.
        """
        ...
```

### 11.3 WeLink Channel (Interface Only)

WeLink communicates via its own CLI tool. The channel adapter calls `welink` as a subprocess.

```python
class WeLinkNotifier(NotificationChannel):
    """Sends notifications via WeLink CLI tool.

    Config keys:
        webhook_url: str (required) — WeLink API endpoint
    """

    async def send(self, message: str, config: dict[str, Any]) -> bool:
        # NOT IMPLEMENTED in v1 — interface reserved for future
        # Implementation sketch:
        #   subprocess: welink send --url {config['webhook_url']} --message "{message}"
        raise NotImplementedError("WeLink notifier not yet implemented")
```

### 11.4 Registry Pattern

Notifiers are registered by channel name, similar to `ScriptRunner`:

```python
class NotifierRegistry:
    def __init__(self):
        self._channels: dict[str, NotificationChannel] = {}

    def register(self, name: str, channel: NotificationChannel) -> None:
        self._channels[name] = channel

    async def send(self, channel: str, message: str, config: dict[str, Any]) -> bool:
        if channel not in self._channels:
            return False
        return await self._channels[channel].send(message, config)
```

### 11.5 Workflow JSON Configuration

```json
{
  "notifications": {
    "on_complete": {
      "channel": "welink",
      "config": { "webhook_url": "https://welink.example.com/api/..." },
      "message_template": "✅ Workflow '{{ workflow.name }}' completed."
    },
    "on_failure": {
      "channel": "welink",
      "config": { "webhook_url": "https://welink.example.com/api/..." },
      "message_template": "❌ Workflow '{{ workflow.name }}' FAILED."
    }
  }
}
```

`message_template` supports the same `{{ }}` template variables as nodes.

### 11.6 Execution Flow

```
Workflow run completes (or fails)
  → Engine checks workflow.notifications
    → on_complete (if succeeded) or on_failure (if failed)
      → Resolve message_template with context
        → NotifierRegistry.send(channel, rendered_message, config)
```

---

## 12. Project Structure

```
workflow-orchestrator/
├── pyproject.toml
├── README.md
├── CLAUDE.md
│
├── src/
│   └── wflow/
│       ├── __init__.py
│       ├── main.py                 # FastAPI app factory + startup
│       │
│       ├── api/                    # REST API layer
│       │   ├── __init__.py
│       │   ├── deps.py             # Dependency injection
│       │   ├── workflows.py
│       │   ├── runs.py
│       │   ├── cron.py
│       │   └── status.py
│       │
│       ├── cli/                    # CLI layer
│       │   ├── __init__.py
│       │   ├── main.py             # wflow entry point
│       │   ├── server.py
│       │   ├── workflow.py
│       │   ├── run.py
│       │   └── cron.py
│       │
│       ├── engine/                 # Core engine
│       │   ├── __init__.py
│       │   ├── state_machine.py    # Run state machine
│       │   ├── executor.py         # Topological execution
│       │   ├── session_manager.py  # Session lifecycle
│       │   ├── node_runner.py      # Single node execution
│       │   └── scheduler.py        # APScheduler integration
│       │
│       ├── adapters/               # External tool adapters
│       │   ├── __init__.py
│       │   ├── claude_cli.py       # Claude CLI subprocess
│       │   ├── script_runner.py    # Python script/function runner
│       │   └── notifiers/          # Notification channels
│       │       ├── __init__.py
│       │       ├── base.py         # NotificationChannel ABC
│       │       ├── registry.py     # NotifierRegistry
│       │       └── welink.py       # WeLink notifier (interface only)
│       │
│       ├── models/                 # Data models
│       │   ├── __init__.py
│       │   ├── db.py               # SQLAlchemy ORM
│       │   ├── workflow.py         # Workflow JSON Pydantic schemas
│       │   └── schemas.py          # API request/response schemas
│       │
│       ├── services/               # Business logic
│       │   ├── __init__.py
│       │   ├── workflow_svc.py
│       │   ├── execution_svc.py
│       │   └── schedule_svc.py
│       │
│       └── web/                    # Web UI static files
│           ├── index.html
│           ├── css/
│           ├── js/
│           │   ├── app.js
│           │   └── utils.js
│           └── favicon.ico
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_engine/
│   │   ├── test_state_machine.py
│   │   ├── test_executor.py
│   │   └── test_session_manager.py
│   ├── test_api/
│   │   ├── test_workflows.py
│   │   ├── test_runs.py
│   │   └── test_cron.py
│   ├── test_adapters/
│   │   ├── test_claude_cli.py
│   │   └── test_script_runner.py
│   └── test_cli/
│       └── test_commands.py
│
├── data/                            # Runtime data (gitignored)
│   └── workflows.db
│
└── examples/                        # Example workflow JSON files
    ├── code-review.json
    └── simple-script.json
```

---

## 13. Core Dependencies

```toml
[project]
name = "wflow"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]",
    "sqlalchemy>=2.0",
    "aiosqlite",
    "pydantic>=2.0",
    "apscheduler>=3.10",
    "click>=8.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio",
    "pytest-cov",
]
```

---

## 14. Open Questions / Future Considerations

- **Script runner plugin system**: How to register custom script modules beyond `builtin.*`
- **Workflow versioning**: How to handle changes to a workflow JSON after runs exist
- **Additional notification channels**: WeLink interface is reserved; other channels (Slack, email, DingTalk) can be added by implementing `NotificationChannel`
- **Node-level notifications**: Currently only workflow-level (on_complete/on_failure); per-node notifications can be added later
- **Metrics export**: Prometheus metrics for workflow execution stats
- **Multi-machine**: Future scalability beyond single machine (out of scope for v1)
