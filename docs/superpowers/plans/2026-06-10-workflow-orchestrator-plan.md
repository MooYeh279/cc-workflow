# Workflow Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude Code CLI workflow orchestrator — JSON-defined DAG workflows with conditional branching, session persistence, cron scheduling, CLI + Web interfaces.

**Architecture:** Modular monolith — FastAPI process hosting REST API, Web UI static files, workflow engine, and APScheduler. CLI is a thin HTTP client. SQLite + filesystem persistence.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.0 (async, aiosqlite), Pydantic v2, APScheduler, Click, httpx, Alpine.js + HTMX

---

### Task 1: Project Scaffold and Dependencies

**Files:**
- Create: `pyproject.toml`
- Create: `src/wflow/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `.gitignore`

- [ ] **Step 1: Write pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=68.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[project]
name = "wflow"
version = "0.1.0"
description = "Claude Code CLI workflow orchestrator"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]",
    "sqlalchemy[asyncio]>=2.0",
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
    "httpx",
]

[project.scripts]
wflow = "wflow.cli.main:cli"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-v --cov=src/wflow --cov-report=term-missing"
```

- [ ] **Step 2: Write .gitignore**

```gitignore
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
dist/
data/
.coverage
htmlcov/
.pytest_cache/
.superpowers/
```

- [ ] **Step 3: Install package in development mode**

Run: `pip install -e ".[dev]"`

- [ ] **Step 4: Run tests to verify setup**

Run: `pytest tests/ -v`
Expected: 0 tests collected (empty, but no errors)

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore: scaffold project with dependencies"
```

---

### Task 2: Database Models (SQLAlchemy ORM)

**Files:**
- Create: `src/wflow/models/__init__.py`
- Create: `src/wflow/models/db.py`
- Create: `tests/test_models/__init__.py`
- Create: `tests/test_models/test_db.py`

- [ ] **Step 1: Write failing test for database models**

Create `tests/test_models/test_db.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from wflow.models.db import Base, Workflow, WorkflowRun, NodeExecution, Session, CronJob, RunLog


@pytest.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///file:test?mode=memory&cache=shared&uri=true")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.mark.asyncio
async def test_create_workflow(db_session):
    import uuid
    wid = str(uuid.uuid4())
    wf = Workflow(id=wid, name="test-wf", description="a test", config='{"nodes":[]}')
    db_session.add(wf)
    await db_session.commit()

    result = await db_session.get(Workflow, wid)
    assert result is not None
    assert result.name == "test-wf"
    assert result.status == "active"


@pytest.mark.asyncio
async def test_create_workflow_run(db_session):
    import uuid
    wid = str(uuid.uuid4())
    wf = Workflow(id=wid, name="test-wf", config='{"nodes":[]}')
    db_session.add(wf)
    await db_session.commit()

    rid = str(uuid.uuid4())
    run = WorkflowRun(id=rid, workflow_id=wid, status="pending", context="{}")
    db_session.add(run)
    await db_session.commit()

    result = await db_session.get(WorkflowRun, rid)
    assert result is not None
    assert result.workflow_id == wid
    assert result.status == "pending"


@pytest.mark.asyncio
async def test_node_execution_lifecycle(db_session):
    import uuid
    wid = str(uuid.uuid4())
    wf = Workflow(id=wid, name="test-wf", config='{"nodes":[]}')
    rid = str(uuid.uuid4())
    run = WorkflowRun(id=rid, workflow_id=wid, status="running", context="{}")
    db_session.add_all([wf, run])
    await db_session.commit()

    nid = str(uuid.uuid4())
    ne = NodeExecution(id=nid, run_id=rid, node_id="coding", type="agent",
                       session_id="sess-1", status="completed", retry_count=0,
                       input='{"task":"test"}', output='{"ok":true}')
    db_session.add(ne)
    await db_session.commit()

    result = await db_session.get(NodeExecution, nid)
    assert result.output == '{"ok":true}'


@pytest.mark.asyncio
async def test_run_log_insert(db_session):
    import uuid
    wid = str(uuid.uuid4())
    wf = Workflow(id=wid, name="test-wf", config='{"nodes":[]}')
    rid = str(uuid.uuid4())
    run = WorkflowRun(id=rid, workflow_id=wid, status="running", context="{}")
    db_session.add_all([wf, run])
    await db_session.commit()

    log = RunLog(run_id=rid, node_id="coding", level="info", message="started")
    db_session.add(log)
    await db_session.commit()

    assert log.id is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wflow.models.db'`

- [ ] **Step 3: Write db.py with all ORM models**

Create `src/wflow/models/db.py`:

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Integer, Text, ForeignKey
from sqlalchemy.orm import DeclarativeBase, relationship


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Workflow(Base):
    __tablename__ = "workflow"

    id = Column(String, primary_key=True, default=_uuid)
    name = Column(String, nullable=False)
    description = Column(String, default="")
    config = Column(Text, nullable=False, default="{}")
    status = Column(String, default="active")
    created_at = Column(String, default=_now)
    updated_at = Column(String, default=_now, onupdate=_now)

    runs = relationship("WorkflowRun", back_populates="workflow", cascade="all, delete-orphan")
    cron_jobs = relationship("CronJob", back_populates="workflow", cascade="all, delete-orphan")


class WorkflowRun(Base):
    __tablename__ = "workflow_run"

    id = Column(String, primary_key=True, default=_uuid)
    workflow_id = Column(String, ForeignKey("workflow.id"), nullable=False)
    status = Column(String, default="pending")
    current_node_id = Column(String, nullable=True)
    context = Column(Text, default="{}")
    started_at = Column(String, default=_now)
    finished_at = Column(String, nullable=True)

    workflow = relationship("Workflow", back_populates="runs")
    node_executions = relationship("NodeExecution", back_populates="run", cascade="all, delete-orphan")
    logs = relationship("RunLog", back_populates="run", cascade="all, delete-orphan")


class NodeExecution(Base):
    __tablename__ = "node_execution"

    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String, ForeignKey("workflow_run.id"), nullable=False)
    node_id = Column(String, nullable=False)
    type = Column(String, nullable=False)
    session_id = Column(String, nullable=True)
    status = Column(String, default="pending")
    retry_count = Column(Integer, default=0)
    input = Column(Text, default="{}")
    output = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(String, default=_now)
    finished_at = Column(String, nullable=True)

    run = relationship("WorkflowRun", back_populates="node_executions")


class Session(Base):
    __tablename__ = "session"

    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String, ForeignKey("workflow_run.id"), nullable=False)
    node_id = Column(String, nullable=False)
    session_path = Column(String, nullable=True)
    status = Column(String, default="active")
    created_at = Column(String, default=_now)


class CronJob(Base):
    __tablename__ = "cron_job"

    id = Column(String, primary_key=True, default=_uuid)
    workflow_id = Column(String, ForeignKey("workflow.id"), nullable=False)
    cron_expr = Column(String, nullable=False)
    enabled = Column(Integer, default=1)
    inputs = Column(Text, default="{}")
    last_run_id = Column(String, ForeignKey("workflow_run.id"), nullable=True)
    next_fire_at = Column(String, nullable=True)
    created_at = Column(String, default=_now)

    workflow = relationship("Workflow", back_populates="cron_jobs")


class RunLog(Base):
    __tablename__ = "run_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey("workflow_run.id"), nullable=False)
    node_id = Column(String, nullable=True)
    level = Column(String, default="info")
    message = Column(Text, nullable=False)
    timestamp = Column(String, default=_now)

    run = relationship("WorkflowRun", back_populates="logs")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models/test_db.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add SQLAlchemy ORM models (6 tables)"
```

---

### Task 3: Pydantic Schemas — Workflow JSON Validation

**Files:**
- Create: `src/wflow/models/workflow.py`
- Create: `tests/test_models/test_workflow_schema.py`

- [ ] **Step 1: Write failing test for workflow JSON validation**

Create `tests/test_models/test_workflow_schema.py`:

```python
import pytest
from pydantic import ValidationError
from wflow.models.workflow import (
    WorkflowConfig, AgentNode, ScriptNode, Edge, WorkflowSpec, ToolConfig
)


def test_validate_valid_agent_node():
    node = AgentNode(
        id="coding",
        name="Code Implementation",
        type="agent",
        prompt="Write code for: {{ inputs.requirement }}",
        tools=ToolConfig(allowed=["Read", "Write"], disallowed=[], auto_approve=["Read"]),
        output_schema={"type": "object", "properties": {"done": {"type": "boolean"}}, "required": ["done"]},
    )
    assert node.id == "coding"
    assert node.type == "agent"
    assert node.retry.max_retries == 3


def test_validate_valid_script_node():
    node = ScriptNode(
        id="validate",
        name="Validation",
        type="script",
        script_module="builtin.validators",
        script_function="check",
        script_args={"output": "{{ nodes.coding.output }}"},
        output_schema={"type": "object", "properties": {"passed": {"type": "boolean"}}, "required": ["passed"]},
    )
    assert node.type == "script"


def test_validate_workflow_with_edges():
    spec = WorkflowSpec(
        name="test-wf",
        config=WorkflowConfig(max_retries=2, retry_delay_seconds=10, timeout_seconds=600),
        nodes=[
            AgentNode(
                id="start", name="start", type="agent", prompt="go",
                output_schema={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
            ),
            ScriptNode(
                id="end", name="end", type="script",
                script_module="builtin.validators", script_function="noop",
                output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            ),
        ],
        edges=[
            Edge(id="e1", from_="start", to="end"),
        ],
    )
    assert len(spec.nodes) == 2
    assert len(spec.edges) == 1


def test_duplicate_node_ids_raise_error():
    with pytest.raises(ValidationError):
        WorkflowSpec(
            name="bad",
            nodes=[
                AgentNode(id="same", name="a", type="agent", prompt="x",
                          output_schema={"type": "object", "properties": {}, "required": []}),
                ScriptNode(id="same", name="b", type="script",
                           script_module="m", script_function="f",
                           output_schema={"type": "object", "properties": {}, "required": []}),
            ],
            edges=[],
        )


def test_condition_edge_with_template():
    edge = Edge(id="e1", from_="review", to="coding",
                condition="{{ nodes.review.output.approved }} == false")
    assert edge.condition == "{{ nodes.review.output.approved }} == false"


def test_workflow_with_inputs_and_outputs():
    spec = WorkflowSpec(
        name="io-test",
        nodes=[
            AgentNode(id="n1", name="n1", type="agent", prompt="{{ inputs.task }}",
                      output_schema={"type": "object", "properties": {"result": {"type": "string"}}, "required": ["result"]}),
        ],
        edges=[Edge(id="e1", from_="n1", to=None)],
        inputs={"task": {"type": "string", "required": True}},
        outputs={"final_result": "{{ nodes.n1.output.result }}"},
    )
    assert spec.inputs["task"]["type"] == "string"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_models/test_workflow_schema.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write workflow.py with Pydantic models**

Create `src/wflow/models/workflow.py`:

```python
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator, model_validator


class ToolConfig(BaseModel):
    allowed: list[str] = Field(default_factory=list)
    disallowed: list[str] = Field(default_factory=list)
    auto_approve: list[str] = Field(default_factory=list)


class RetryConfig(BaseModel):
    max_retries: int = 3
    on_error: list[str] = Field(default_factory=lambda: ["timeout", "parse_error"])


class AgentNode(BaseModel):
    id: str
    name: str
    type: str = "agent"
    prompt: str
    tools: ToolConfig = Field(default_factory=ToolConfig)
    model: str = "sonnet"
    output_schema: dict[str, Any] = Field(alias="output")
    retry: RetryConfig = Field(default_factory=RetryConfig)

    class Config:
        populate_by_name = True


class ScriptNode(BaseModel):
    id: str
    name: str
    type: str = "script"
    script_module: str = Field(alias="module")
    script_function: str = Field(alias="function")
    script_args: dict[str, str] = Field(default_factory=dict, alias="args")
    output_schema: dict[str, Any] = Field(alias="output")
    retry: RetryConfig = Field(default_factory=RetryConfig)

    class Config:
        populate_by_name = True
        extra = "allow"


WorkflowNode = AgentNode | ScriptNode


class Edge(BaseModel):
    id: str
    from_: str = Field(alias="from")
    to: Optional[str] = None
    condition: Optional[str] = None

    class Config:
        populate_by_name = True


class WorkflowConfig(BaseModel):
    max_retries: int = 3
    retry_delay_seconds: int = 30
    timeout_seconds: int = 1800


class WorkflowSpec(BaseModel):
    name: str
    version: str = "1.0"
    description: str = ""
    config: WorkflowConfig = Field(default_factory=WorkflowConfig)
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)

    @field_validator("nodes")
    @classmethod
    def validate_unique_ids(cls, v: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ids = [n["id"] for n in v]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate node IDs found: {ids}")
        return v

    @field_validator("edges")
    @classmethod
    def validate_edge_refs(cls, v: list[dict[str, Any]], info) -> list[dict[str, Any]]:
        node_ids = {n["id"] for n in info.data.get("nodes", [])}
        for edge in v:
            if edge["from"] not in node_ids:
                raise ValueError(f"Edge '{edge['id']}' references unknown 'from' node: {edge['from']}")
            if edge.get("to") is not None and edge["to"] not in node_ids:
                raise ValueError(f"Edge '{edge['id']}' references unknown 'to' node: {edge['to']}")
        return v

    def get_node(self, node_id: str) -> dict[str, Any]:
        for n in self.nodes:
            if n["id"] == node_id:
                return n
        raise KeyError(f"Node not found: {node_id}")

    def get_outgoing_edges(self, node_id: str) -> list[dict[str, Any]]:
        return [e for e in self.edges if e["from"] == node_id]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_models/test_workflow_schema.py -v`
Expected: 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add Pydantic workflow config schemas"
```

---

### Task 4: Template Variable Resolver

**Files:**
- Create: `src/wflow/engine/__init__.py`
- Create: `src/wflow/engine/template.py`
- Create: `tests/test_engine/__init__.py`
- Create: `tests/test_engine/test_template.py`

- [ ] **Step 1: Write failing test for template resolver**

Create `tests/test_engine/test_template.py`:

```python
import pytest
from wflow.engine.template import resolve_template, TemplateContext


def make_context(**overrides) -> TemplateContext:
    defaults = {
        "inputs": {"requirement": "Build a TODO app"},
        "nodes": {
            "coding": {"output": {"files_changed": ["app.py"], "summary": "done"}, "status": "completed", "retry_count": 0},
        },
        "run": {"id": "run-123"},
        "config": {"max_retries": 3},
    }
    defaults.update(overrides)
    return defaults


def test_resolve_inputs_variable():
    result = resolve_template("{{ inputs.requirement }}", make_context())
    assert result == "Build a TODO app"


def test_resolve_node_output():
    result = resolve_template("{{ nodes.coding.output.files_changed }}", make_context())
    assert result == ['app.py']


def test_resolve_node_status():
    result = resolve_template("{{ nodes.coding.status }}", make_context())
    assert result == "completed"


def test_resolve_node_retry_count():
    result = resolve_template("{{ nodes.coding.retry_count }}", make_context())
    assert result == 0


def test_resolve_run_id():
    result = resolve_template("{{ run.id }}", make_context())
    assert result == "run-123"


def test_resolve_config_value():
    result = resolve_template("{{ config.max_retries }}", make_context())
    assert result == 3


def test_resolve_multiple_variables_in_string():
    result = resolve_template("Status: {{ nodes.coding.status }}, by {{ run.id }}", make_context())
    assert result == "Status: completed, by run-123"


def test_resolve_missing_variable_returns_empty_string():
    result = resolve_template("{{ nodes.nonexistent.output.x }}", make_context())
    assert result == ""


def test_resolve_dict_values_recursively():
    args = {
        "task": "{{ inputs.requirement }}",
        "output": "{{ nodes.coding.output }}",
        "retries": "{{ nodes.coding.retry_count }}",
    }
    result = resolve_template(args, make_context())
    assert result["task"] == "Build a TODO app"
    assert result["output"] == {"files_changed": ["app.py"], "summary": "done"}
    assert result["retries"] == 0


def test_no_template_returns_original():
    result = resolve_template("plain string", make_context())
    assert result == "plain string"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_engine/test_template.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write template resolver**

Create `src/wflow/engine/template.py`:

```python
"""Template variable resolver for {{ var.path }} syntax."""

from __future__ import annotations

import re
from typing import Any

TemplateContext = dict[str, Any]

_VAR_PATTERN = re.compile(r"\{\{\s*([^}]+)\s*\}\}")


def _resolve_path(path: str, context: TemplateContext) -> Any:
    """Resolve a dotted path like 'nodes.coding.output.files_changed' against context."""
    parts = path.strip().split(".")
    current: Any = context
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif hasattr(current, part):
            current = getattr(current, part)
        else:
            return ""
        if current is None:
            return ""
    return current


def resolve_template(value: Any, context: TemplateContext) -> Any:
    """Resolve {{ }} template variables in a value.

    Supports:
    - Plain strings with embedded templates: "Status: {{ nodes.x.status }}"
    - Entire values that are single templates: "{{ inputs.foo }}"
    - Nested dicts: recursively resolves string values
    """
    if isinstance(value, dict):
        return {k: resolve_template(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_template(item, context) for item in value]
    if not isinstance(value, str):
        return value

    if "{{" not in value:
        return value

    matches = _VAR_PATTERN.findall(value)

    # If the entire string is a single template expression, return the raw value
    if _VAR_PATTERN.fullmatch(value.strip()):
        return _resolve_path(matches[0], context)

    # Otherwise, substitute each variable with its string representation
    result = value
    for var_path in matches:
        resolved = _resolve_path(var_path, context)
        if not isinstance(resolved, str):
            resolved = str(resolved)
        result = result.replace("{{ " + var_path + " }}", resolved)
        result = result.replace("{{" + var_path + "}}", resolved)
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine/test_template.py -v`
Expected: 10 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add template variable resolver"
```

---

### Task 5: Claude CLI Adapter

**Files:**
- Create: `src/wflow/adapters/__init__.py`
- Create: `src/wflow/adapters/claude_cli.py`
- Create: `tests/test_adapters/__init__.py`
- Create: `tests/test_adapters/test_claude_cli.py`

- [ ] **Step 1: Write failing test for ClaudeCLI adapter**

Create `tests/test_adapters/test_claude_cli.py`:

```python
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from wflow.adapters.claude_cli import ClaudeCLI, ClaudeCLIError, ClaudeCLITimeout


@pytest.fixture
def claude():
    return ClaudeCLI()


@pytest.mark.asyncio
async def test_build_command_basic(claude):
    cmd = claude._build_command(
        prompt="Write a function",
        session_id="sess-001",
        is_resume=False,
        tools_allowed=["Read", "Write"],
    )
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "Write a function" in cmd
    assert "--session-id" in cmd
    assert "sess-001" in cmd
    assert "--allowedTools" in cmd
    assert "Read,Write" in cmd


@pytest.mark.asyncio
async def test_build_command_resume(claude):
    cmd = claude._build_command(
        prompt="Fix the bug",
        session_id="sess-001",
        is_resume=True,
        tools_allowed=["Edit", "Bash"],
    )
    assert "--resume" in cmd
    assert "sess-001" in cmd


@pytest.mark.asyncio
async def test_parse_output_valid_json():
    cli = ClaudeCLI()
    stdout = '{"result": "ok", "data": [1, 2, 3]}'
    result = cli._parse_output(stdout, {"type": "object", "properties": {}})
    assert result == {"result": "ok", "data": [1, 2, 3]}


@pytest.mark.asyncio
async def test_parse_output_extracts_json_from_text():
    cli = ClaudeCLI()
    stdout = 'Here is the result:\n```json\n{"summary": "done"}\n```\nMore text.'
    result = cli._parse_output(stdout, {"type": "object", "properties": {}})
    assert result == {"summary": "done"}


@pytest.mark.asyncio
async def test_parse_output_no_json_raises():
    cli = ClaudeCLI()
    with pytest.raises(ClaudeCLIError, match="No valid JSON found"):
        cli._parse_output("Just some text output", {"type": "object", "properties": {}})


@pytest.mark.asyncio
async def test_parse_output_strips_trailing_commas():
    cli = ClaudeCLI()
    stdout = '{"a": 1, "b": 2,}'
    result = cli._parse_output(stdout, {"type": "object", "properties": {}})
    assert result == {"a": 1, "b": 2}


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_creates_session(mock_subprocess, claude):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b'{"ok": true}', b""))
    mock_subprocess.return_value = mock_proc

    result = await claude.run(
        prompt="Write code",
        node_id="coding",
        session_id=None,
        is_resume=False,
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
        tools_allowed=["Read", "Write"],
        timeout_seconds=30,
    )

    assert result["ok"] is True
    assert result["_session_id"] is not None


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_resumes_existing_session(mock_subprocess, claude):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b'{"fixed": true}', b""))
    mock_subprocess.return_value = mock_proc

    result = await claude.run(
        prompt="Fix the bug",
        node_id="coding",
        session_id="existing-sess",
        is_resume=True,
        output_schema={"type": "object", "properties": {"fixed": {"type": "boolean"}}, "required": ["fixed"]},
        tools_allowed=["Edit"],
        timeout_seconds=30,
    )

    assert result["fixed"] is True
    assert result["_session_id"] == "existing-sess"


@patch("asyncio.create_subprocess_exec")
@pytest.mark.asyncio
async def test_run_timeout(mock_subprocess, claude):
    import asyncio
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_subprocess.return_value = mock_proc

    with pytest.raises(ClaudeCLITimeout):
        await claude.run(
            prompt="long task",
            node_id="test",
            session_id=None,
            is_resume=False,
            output_schema={"type": "object", "properties": {}},
            tools_allowed=[],
            timeout_seconds=1,
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_adapters/test_claude_cli.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write ClaudeCLI adapter**

Create `src/wflow/adapters/claude_cli.py`:

```python
"""Claude Code CLI adapter — subprocess management."""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from typing import Any


class ClaudeCLIError(Exception):
    """Raised when Claude CLI returns an error or produces unparseable output."""


class ClaudeCLITimeout(ClaudeCLIError):
    """Raised when Claude CLI exceeds timeout."""


class ClaudeCLI:
    """Wraps Claude Code CLI (`claude`) subprocess calls."""

    def _build_command(
        self,
        prompt: str,
        session_id: str,
        is_resume: bool,
        tools_allowed: list[str] | None = None,
        tools_disallowed: list[str] | None = None,
        model: str | None = None,
    ) -> list[str]:
        cmd = ["claude", "-p", prompt]
        if is_resume:
            cmd.extend(["--resume", session_id])
        else:
            cmd.extend(["--session-id", session_id])
        if tools_allowed:
            cmd.extend(["--allowedTools", ",".join(tools_allowed)])
        if tools_disallowed:
            cmd.extend(["--disallowedTools", ",".join(tools_disallowed)])
        if model:
            cmd.extend(["--model", model])
        cmd.append("--output-format")
        cmd.append("text")
        return cmd

    def _parse_output(self, stdout: str, output_schema: dict[str, Any]) -> dict[str, Any]:
        """Extract JSON from Claude CLI stdout. Tries multiple extraction strategies."""
        # Strategy 1: Try raw parse first
        try:
            return json.loads(stdout.strip())
        except json.JSONDecodeError:
            pass

        # Strategy 2: Extract from ```json ... ``` blocks
        json_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", stdout, re.DOTALL)
        if json_block:
            try:
                return json.loads(json_block.group(1).strip())
            except json.JSONDecodeError:
                pass

        # Strategy 3: Find balanced braces
        brace_start = stdout.find("{")
        brace_end = stdout.rfind("}")
        if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
            candidate = stdout[brace_start:brace_end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

        raise ClaudeCLIError(f"No valid JSON found in Claude output: {stdout[:500]}")

    async def run(
        self,
        prompt: str,
        node_id: str,
        session_id: str | None,
        is_resume: bool,
        output_schema: dict[str, Any],
        tools_allowed: list[str] | None = None,
        tools_disallowed: list[str] | None = None,
        model: str | None = None,
        timeout_seconds: int = 1800,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Execute Claude CLI and return parsed structured output.

        Returns the dict output with an extra `_session_id` key.
        """
        sid = session_id or str(uuid.uuid4())
        cmd = self._build_command(
            prompt=prompt,
            session_id=sid,
            is_resume=is_resume,
            tools_allowed=tools_allowed,
            tools_disallowed=tools_disallowed,
            model=model,
        )

        merged_env = {**os.environ, **(env or {})}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            raise ClaudeCLITimeout(f"Claude CLI timed out after {timeout_seconds}s for node '{node_id}'")

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if proc.returncode != 0 and proc.returncode is not None:
            raise ClaudeCLIError(
                f"Claude CLI exited with code {proc.returncode} for node '{node_id}'. "
                f"stderr: {stderr[:500]}"
            )

        parsed = self._parse_output(stdout, output_schema)
        parsed["_session_id"] = sid
        return parsed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_adapters/test_claude_cli.py -v`
Expected: 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add Claude CLI adapter"
```

---

### Task 6: Script Runner Adapter

**Files:**
- Create: `src/wflow/adapters/script_runner.py`
- Create: `tests/test_adapters/test_script_runner.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_adapters/test_script_runner.py`:

```python
import pytest
from wflow.adapters.script_runner import ScriptRunner, ScriptError


@pytest.fixture
def runner():
    return ScriptRunner()


@pytest.mark.asyncio
async def test_run_registered_function(runner):
    def echo(**kwargs):
        return {"echo": kwargs}

    runner.register("test.echo", echo)

    result = await runner.run("test.echo", "my_func", {"message": "hello"})
    assert result["echo"]["message"] == "hello"


@pytest.mark.asyncio
async def test_run_unregistered_module_raises(runner):
    with pytest.raises(ScriptError, match="not registered"):
        await runner.run("unknown.module", "func", {})


@pytest.mark.asyncio
async def test_run_missing_function_raises(runner):
    def some_fn(**kwargs):
        return {"ok": True}

    runner.register("test.mods", some_fn)

    with pytest.raises(ScriptError, match="not found"):
        await runner.run("test.mods", "nonexistent_func", {})


@pytest.mark.asyncio
async def test_run_function_that_raises(runner):
    def will_fail(**kwargs):
        raise ValueError("something went wrong")

    runner.register("test.fails", will_fail)

    with pytest.raises(ScriptError, match="something went wrong"):
        await runner.run("test.fails", "will_fail", {})
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_adapters/test_script_runner.py -v`
Expected: FAIL

- [ ] **Step 3: Write script runner**

Create `src/wflow/adapters/script_runner.py`:

```python
"""Script node runner — calls registered Python functions."""

from __future__ import annotations

import asyncio
from typing import Any, Callable


class ScriptError(Exception):
    """Raised when a script node fails."""


class ScriptRunner:
    """Registry-based script runner.

    Modules register callables via `register(module_name, callable)`.
    The callable's __name__ is used as the function identifier.
    """

    def __init__(self):
        self._registry: dict[str, dict[str, Callable[..., dict[str, Any]]]] = {}

    def register(self, module_name: str, fn: Callable[..., dict[str, Any]]) -> None:
        """Register a function under a module name. The function's __name__ is the key."""
        if module_name not in self._registry:
            self._registry[module_name] = {}
        self._registry[module_name][fn.__name__] = fn

    async def run(
        self, module_name: str, function_name: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a registered script function. Returns its dict output."""
        if module_name not in self._registry:
            raise ScriptError(f"Script module '{module_name}' not registered")

        fn = self._registry[module_name].get(function_name)
        if fn is None:
            registered = list(self._registry[module_name].keys())
            raise ScriptError(
                f"Function '{function_name}' not found in module '{module_name}'. "
                f"Available: {registered}"
            )

        try:
            if asyncio.iscoroutinefunction(fn):
                result = await fn(**args)
            else:
                result = fn(**args)
        except ScriptError:
            raise
        except Exception as e:
            raise ScriptError(f"Script '{module_name}.{function_name}' failed: {e}") from e

        if not isinstance(result, dict):
            raise ScriptError(
                f"Script '{module_name}.{function_name}' must return a dict, "
                f"got {type(result).__name__}"
            )
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_adapters/test_script_runner.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add script runner adapter"
```

---

### Task 7: Engine — Session Manager

**Files:**
- Create: `src/wflow/engine/session_manager.py`
- Create: `tests/test_engine/test_session_manager.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_engine/test_session_manager.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from wflow.engine.session_manager import SessionManager


@pytest.fixture
def db_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


@pytest.fixture
def session_mgr(db_session):
    return SessionManager(db_session)


@pytest.mark.asyncio
async def test_get_or_create_session_creates_new(session_mgr, db_session):
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db_session.execute.return_value = result

    session_id = await session_mgr.get_or_create("run-1", "coding")

    assert session_id is not None
    assert len(session_id) > 0
    db_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_get_or_create_session_returns_existing(session_mgr, db_session):
    from wflow.models.db import Session as SessionModel
    existing = SessionModel(id="sess-existing", run_id="run-1", node_id="coding", status="active")

    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    db_session.execute.return_value = result

    session_id = await session_mgr.get_or_create("run-1", "coding")

    assert session_id == "sess-existing"


@pytest.mark.asyncio
async def test_is_resume_returns_true_when_session_exists(session_mgr, db_session):
    from wflow.models.db import Session as SessionModel
    existing = SessionModel(id="sess-1", run_id="run-1", node_id="coding", status="active")

    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    db_session.execute.return_value = result

    assert await session_mgr.is_resume("run-1", "coding") is True


@pytest.mark.asyncio
async def test_is_resume_returns_false_when_no_session(session_mgr, db_session):
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db_session.execute.return_value = result

    assert await session_mgr.is_resume("run-1", "new-node") is False


@pytest.mark.asyncio
async def test_cleanup_expired_sessions(session_mgr, db_session):
    db_session.execute.return_value = None
    await session_mgr.cleanup_expired(retention_days=30)
    db_session.execute.assert_called_once()
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_engine/test_session_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Write session manager**

Create `src/wflow/engine/session_manager.py`:

```python
"""Session lifecycle manager for Claude CLI sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.models.db import Session


class SessionManager:
    """Manages Claude CLI session creation, lookup, and cleanup.

    One session per (run_id, node_id) pair.
    """

    def __init__(self, db: AsyncSession):
        self._db = db

    async def get_or_create(self, run_id: str, node_id: str) -> str:
        """Get existing session ID or create a new one for (run_id, node_id)."""
        stmt = select(Session).where(
            Session.run_id == run_id,
            Session.node_id == node_id,
        )
        result = await self._db.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            return existing.id

        session_id = str(uuid.uuid4())
        session = Session(
            id=session_id,
            run_id=run_id,
            node_id=node_id,
            status="active",
        )
        self._db.add(session)
        await self._db.commit()
        return session_id

    async def is_resume(self, run_id: str, node_id: str) -> bool:
        """Check if a session already exists for this (run_id, node_id)."""
        stmt = select(Session).where(
            Session.run_id == run_id,
            Session.node_id == node_id,
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none() is not None

    async def mark_completed(self, session_id: str) -> None:
        """Mark a session as completed (no longer active)."""
        stmt = select(Session).where(Session.id == session_id)
        result = await self._db.execute(stmt)
        session = result.scalar_one_or_none()
        if session:
            session.status = "completed"
            await self._db.commit()

    async def cleanup_expired(self, retention_days: int = 30) -> int:
        """Delete sessions older than retention_days. Returns count deleted."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        stmt = delete(Session).where(
            Session.status == "completed",
            Session.created_at < cutoff,
        )
        result = await self._db.execute(stmt)
        await self._db.commit()
        return result.rowcount
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine/test_session_manager.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add session manager"
```

---

### Task 8: Engine — State Machine

**Files:**
- Create: `src/wflow/engine/state_machine.py`
- Create: `tests/test_engine/test_state_machine.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_engine/test_state_machine.py`:

```python
import pytest
from wflow.engine.state_machine import RunStateMachine, RunStatus


def test_valid_transitions():
    sm = RunStateMachine()
    assert sm.can_transition(RunStatus.PENDING, RunStatus.RUNNING) is True
    assert sm.can_transition(RunStatus.RUNNING, RunStatus.PAUSED) is True
    assert sm.can_transition(RunStatus.RUNNING, RunStatus.COMPLETED) is True
    assert sm.can_transition(RunStatus.RUNNING, RunStatus.FAILED) is True
    assert sm.can_transition(RunStatus.PAUSED, RunStatus.RUNNING) is True
    assert sm.can_transition(RunStatus.FAILED, RunStatus.RUNNING) is True


def test_invalid_transitions():
    sm = RunStateMachine()
    assert sm.can_transition(RunStatus.COMPLETED, RunStatus.RUNNING) is False
    assert sm.can_transition(RunStatus.COMPLETED, RunStatus.PAUSED) is False
    assert sm.can_transition(RunStatus.COMPLETED, RunStatus.FAILED) is False
    assert sm.can_transition(RunStatus.PENDING, RunStatus.COMPLETED) is False
    assert sm.can_transition(RunStatus.PAUSED, RunStatus.COMPLETED) is False


def test_transition_raises_on_invalid():
    sm = RunStateMachine()
    with pytest.raises(ValueError, match="Invalid transition"):
        sm.transition(RunStatus.COMPLETED, RunStatus.RUNNING)


def test_transition_returns_new_status():
    sm = RunStateMachine()
    new_status = sm.transition(RunStatus.PENDING, RunStatus.RUNNING)
    assert new_status == RunStatus.RUNNING


def test_failed_can_retry_to_running():
    sm = RunStateMachine()
    assert sm.can_transition(RunStatus.FAILED, RunStatus.RUNNING) is True  # manual retry
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_engine/test_state_machine.py -v`
Expected: FAIL

- [ ] **Step 3: Write state machine**

Create `src/wflow/engine/state_machine.py`:

```python
"""Run-level state machine for workflow execution."""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


_TRANSITIONS: dict[RunStatus, set[RunStatus]] = {
    RunStatus.PENDING: {RunStatus.RUNNING},
    RunStatus.RUNNING: {RunStatus.PAUSED, RunStatus.COMPLETED, RunStatus.FAILED},
    RunStatus.PAUSED: {RunStatus.RUNNING},
    RunStatus.FAILED: {RunStatus.RUNNING},   # manual retry
    RunStatus.COMPLETED: set(),               # terminal
}


class RunStateMachine:
    """Validates and executes run-level state transitions."""

    def can_transition(self, current: RunStatus, target: RunStatus) -> bool:
        """Check if a transition is valid."""
        return target in _TRANSITIONS.get(current, set())

    def transition(self, current: RunStatus, target: RunStatus) -> RunStatus:
        """Execute a state transition, raising ValueError if invalid."""
        if not self.can_transition(current, target):
            raise ValueError(
                f"Invalid transition: {current.value} -> {target.value}. "
                f"Allowed from '{current.value}': {[s.value for s in _TRANSITIONS.get(current, set())]}"
            )
        return target
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine/test_state_machine.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add run state machine"
```

---

---

### Task 9: Engine — Node Runner

**Files:**
- Create: `src/wflow/engine/node_runner.py`
- Create: `tests/test_engine/test_node_runner.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_engine/test_node_runner.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from wflow.engine.node_runner import NodeRunner
from wflow.adapters.claude_cli import ClaudeCLI
from wflow.adapters.script_runner import ScriptRunner


@pytest.fixture
def claude_cli():
    cli = MagicMock(spec=ClaudeCLI)
    cli.run = AsyncMock(return_value={"result": "ok", "_session_id": "sess-1"})
    return cli


@pytest.fixture
def script_runner():
    sr = ScriptRunner()
    def echo(**kwargs):
        return {"echo": kwargs}
    sr.register("builtin.test", echo)
    return sr


@pytest.fixture
def node_runner(claude_cli, script_runner):
    return NodeRunner(claude=claude_cli, script_runner=script_runner)


@pytest.mark.asyncio
async def test_run_agent_node_create_mode(node_runner, claude_cli):
    node_config = {
        "id": "coding", "type": "agent", "prompt": "Write code",
        "tools": {"allowed": ["Read"], "disallowed": [], "auto_approve": []},
        "retry": {"max_retries": 2, "on_error": ["timeout"]},
        "output": {"type": "object", "properties": {}, "required": []},
    }
    context = {"inputs": {}, "nodes": {}, "run": {"id": "run-1"}, "config": {}}

    result = await node_runner.run(node_config, context, session_id=None)

    claude_cli.run.assert_called_once()
    call_kwargs = claude_cli.run.call_args.kwargs
    assert call_kwargs["is_resume"] is False
    assert result["result"] == "ok"


@pytest.mark.asyncio
async def test_run_agent_node_resume_mode(node_runner, claude_cli):
    node_config = {
        "id": "coding", "type": "agent", "prompt": "Fix the bug",
        "tools": {"allowed": ["Edit"], "disallowed": [], "auto_approve": []},
        "retry": {"max_retries": 2, "on_error": []},
        "output": {"type": "object", "properties": {}, "required": []},
    }
    context = {"inputs": {}, "nodes": {}, "run": {"id": "run-1"}, "config": {}}

    result = await node_runner.run(node_config, context, session_id="sess-existing")

    call_kwargs = claude_cli.run.call_args.kwargs
    assert call_kwargs["is_resume"] is True
    assert call_kwargs["session_id"] == "sess-existing"


@pytest.mark.asyncio
async def test_run_script_node(node_runner):
    node_config = {
        "id": "validate", "type": "script",
        "script": {"module": "builtin.test", "function": "echo", "args": {"msg": "hello"}},
        "output": {"type": "object", "properties": {}, "required": []},
    }
    context = {"inputs": {}, "nodes": {}, "run": {"id": "run-1"}, "config": {}}

    result = await node_runner.run(node_config, context, session_id=None)

    assert result["echo"]["msg"] == "hello"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_engine/test_node_runner.py -v`
Expected: FAIL

- [ ] **Step 3: Write node runner**

Create `src/wflow/engine/node_runner.py`:

```python
"""Node runner — dispatches agent or script node execution."""

from __future__ import annotations

from typing import Any

from wflow.adapters.claude_cli import ClaudeCLI
from wflow.adapters.script_runner import ScriptRunner
from wflow.engine.template import resolve_template, TemplateContext


class NodeRunner:
    """Executes a single workflow node (agent or script)."""

    def __init__(self, claude: ClaudeCLI, script_runner: ScriptRunner):
        self._claude = claude
        self._script = script_runner

    async def run(
        self,
        node_config: dict[str, Any],
        context: TemplateContext,
        session_id: str | None,
    ) -> dict[str, Any]:
        """Execute a node and return its output dict."""
        node_type = node_config["type"]
        is_resume = session_id is not None

        if node_type == "agent":
            return await self._run_agent(node_config, context, session_id, is_resume)
        elif node_type == "script":
            return await self._run_script(node_config, context)
        else:
            raise ValueError(f"Unknown node type: {node_type}")

    async def _run_agent(
        self,
        node: dict[str, Any],
        context: TemplateContext,
        session_id: str | None,
        is_resume: bool,
    ) -> dict[str, Any]:
        prompt = resolve_template(node["prompt"], context)
        tools = node.get("tools", {})
        retry = node.get("retry", {})
        model = node.get("model")

        return await self._claude.run(
            prompt=prompt,
            node_id=node["id"],
            session_id=session_id,
            is_resume=is_resume,
            output_schema=node["output"],
            tools_allowed=tools.get("allowed"),
            tools_disallowed=tools.get("disallowed"),
            model=model,
            timeout_seconds=retry.get("timeout_seconds", 1800),
        )

    async def _run_script(
        self,
        node: dict[str, Any],
        context: TemplateContext,
    ) -> dict[str, Any]:
        script = node["script"]
        args = resolve_template(script.get("args", {}), context)
        return await self._script.run(
            module_name=script["module"],
            function_name=script["function"],
            args=args,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine/test_node_runner.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add node runner"
```

---

### Task 10: Engine — Executor (Topological)

**Files:**
- Create: `src/wflow/engine/executor.py`
- Create: `tests/test_engine/test_executor.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_engine/test_executor.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from wflow.engine.executor import WorkflowExecutor
from wflow.models.workflow import WorkflowSpec
from wflow.engine.template import TemplateContext


def make_spec() -> WorkflowSpec:
    return WorkflowSpec(
        name="test-wf",
        config={"max_retries": 2, "retry_delay_seconds": 0, "timeout_seconds": 30},
        nodes=[
            {"id": "start", "name": "Start", "type": "agent", "prompt": "go",
             "tools": {"allowed": [], "disallowed": [], "auto_approve": []},
             "retry": {"max_retries": 1, "on_error": []},
             "output": {"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]}},
            {"id": "end", "name": "End", "type": "script",
             "script": {"module": "builtin.test", "function": "echo", "args": {}},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "start", "to": "end"},
        ],
    )


def make_context() -> TemplateContext:
    return {"inputs": {}, "nodes": {}, "run": {"id": "run-1"}, "config": {"max_retries": 2}}


@pytest.mark.asyncio
async def test_executor_runs_nodes_in_order():
    spec = make_spec()

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        {"x": 42, "_session_id": "s1"},    # start node
        {"echo": {}, "_session_id": "s2"},  # end node
    ])

    session_mgr = MagicMock()
    session_mgr.get_or_create = AsyncMock(side_effect=["s1", "s2"])
    session_mgr.is_resume = AsyncMock(return_value=False)

    db_session = AsyncMock()
    executor = WorkflowExecutor(
        db=db_session,
        node_runner=node_runner,
        session_manager=session_mgr,
    )

    result = await executor.execute(spec, "run-1", make_context())

    assert result is True
    assert node_runner.run.call_count == 2


@pytest.mark.asyncio
async def test_executor_evaluates_conditions():
    spec = WorkflowSpec(
        name="conditional",
        nodes=[
            {"id": "check", "name": "Check", "type": "agent", "prompt": "check",
             "tools": {"allowed": [], "disallowed": [], "auto_approve": []},
             "retry": {"max_retries": 1, "on_error": []},
             "output": {"type": "object", "properties": {"pass": {"type": "boolean"}}, "required": ["pass"]}},
            {"id": "good", "name": "Good", "type": "script",
             "script": {"module": "builtin.test", "function": "echo", "args": {}},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "bad", "name": "Bad", "type": "script",
             "script": {"module": "builtin.test", "function": "echo", "args": {}},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[
            {"id": "e1", "from": "check", "to": "good", "condition": "{{ nodes.check.output.pass }} == true"},
            {"id": "e2", "from": "check", "to": "bad", "condition": "{{ nodes.check.output.pass }} == false"},
        ],
    )

    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        {"pass": True, "_session_id": "s1"},   # check passes
        {"echo": {}, "_session_id": "s2"},      # goes to good
    ])

    session_mgr = MagicMock()
    session_mgr.get_or_create = AsyncMock(side_effect=["s1", "s2"])
    session_mgr.is_resume = AsyncMock(return_value=False)

    db_session = AsyncMock()
    executor = WorkflowExecutor(
        db=db_session,
        node_runner=node_runner,
        session_manager=session_mgr,
    )

    result = await executor.execute(spec, "run-1", make_context())

    assert result is True
    # Should have run 'check' then 'good' (not 'bad')
    calls = [c.kwargs["node_config"]["id"] for c in node_runner.run.call_args_list]
    assert calls == ["check", "good"]


@pytest.mark.asyncio
async def test_executor_handles_retry_and_failure():
    spec = WorkflowSpec(
        name="retry-test",
        nodes=[
            {"id": "flakey", "name": "Flakey", "type": "agent", "prompt": "try",
             "tools": {"allowed": [], "disallowed": [], "auto_approve": []},
             "retry": {"max_retries": 2, "on_error": ["parse_error"]},
             "output": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}},
        ],
        edges=[{"id": "e1", "from": "flakey", "to": None}],
    )

    from wflow.adapters.claude_cli import ClaudeCLIError
    node_runner = MagicMock()
    node_runner.run = AsyncMock(side_effect=[
        ClaudeCLIError("parse failed"),  # first attempt fails
        ClaudeCLIError("parse failed"),  # retry 1 fails
        {"ok": True, "_session_id": "s1"},  # retry 2 succeeds
    ])

    session_mgr = MagicMock()
    session_mgr.get_or_create = AsyncMock(return_value="s1")
    session_mgr.is_resume = AsyncMock(return_value=True)

    db_session = AsyncMock()
    executor = WorkflowExecutor(
        db=db_session,
        node_runner=node_runner,
        session_manager=session_mgr,
    )

    result = await executor.execute(spec, "run-1", make_context())

    assert result is True
    assert node_runner.run.call_count == 3
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_engine/test_executor.py -v`
Expected: FAIL

- [ ] **Step 3: Write executor**

Create `src/wflow/engine/executor.py`:

```python
"""Topological workflow executor — drives node-by-node execution."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from wflow.models.workflow import WorkflowSpec
from wflow.models.db import NodeExecution, RunLog
from wflow.engine.template import resolve_template, TemplateContext
from wflow.engine.node_runner import NodeRunner
from wflow.engine.session_manager import SessionManager
from wflow.adapters.claude_cli import ClaudeCLIError, ClaudeCLITimeout
from wflow.adapters.script_runner import ScriptError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evaluate_condition(condition: str | None, context: TemplateContext) -> bool:
    """Evaluate an edge condition string against context. Returns True if no condition."""
    if not condition:
        return True
    resolved = resolve_template(condition, context)
    if not isinstance(resolved, str):
        resolved = str(resolved)
    resolved = resolved.strip()
    if not resolved:
        return True
    return resolved.lower() in ("true", "yes", "1")


class WorkflowExecutor:
    """Drives execution of a workflow run through its DAG, one node at a time."""

    def __init__(
        self,
        db: AsyncSession,
        node_runner: NodeRunner,
        session_manager: SessionManager,
    ):
        self._db = db
        self._node_runner = node_runner
        self._session_mgr = session_manager

    async def execute(
        self,
        spec: WorkflowSpec,
        run_id: str,
        context: TemplateContext,
    ) -> bool:
        """Execute the workflow. Returns True on success, False on failure."""
        current_node_id = self._find_start_node(spec)
        if not current_node_id:
            self._log(run_id, "warn", "No start node found (no edges with 'from' that isn't a 'to')")
            return False

        while current_node_id is not None:
            node_config = spec.get_node(current_node_id)
            self._log(run_id, "info", f"Executing node: {current_node_id} ({node_config['type']})")

            # Session management
            session_id = None
            if node_config["type"] == "agent":
                session_id = await self._session_mgr.get_or_create(run_id, current_node_id)

            # Create node_execution record
            ne = NodeExecution(
                id=str(__import__("uuid").uuid4()),
                run_id=run_id,
                node_id=current_node_id,
                type=node_config["type"],
                session_id=session_id,
                status="running",
                input=json.dumps(context.get("nodes", {}).get(current_node_id, {})),
            )
            self._db.add(ne)
            await self._db.commit()

            # Execute with retries
            retry_config = node_config.get("retry", {})
            max_retries = retry_config.get("max_retries", 3)
            success = False

            for attempt in range(max_retries + 1):
                try:
                    is_resume_session = attempt > 0 or (await self._session_mgr.is_resume(run_id, current_node_id))
                    output = await self._node_runner.run(
                        node_config, context,
                        session_id=session_id if is_resume_session else None,
                    )

                    # Update context with node output
                    session_id_out = output.pop("_session_id", session_id)
                    context["nodes"][current_node_id] = {
                        "output": output,
                        "status": "completed",
                        "retry_count": attempt,
                    }

                    ne.output = json.dumps(output)
                    ne.status = "completed"
                    ne.retry_count = attempt
                    ne.finished_at = _now()
                    await self._db.commit()

                    if node_config["type"] == "agent" and session_id_out:
                        await self._session_mgr.mark_completed(session_id_out)

                    success = True
                    break

                except (ClaudeCLIError, ClaudeCLITimeout, ScriptError) as e:
                    ne.retry_count = attempt + 1
                    ne.error = str(e)[:1000]
                    await self._db.commit()

                    if attempt >= max_retries:
                        ne.status = "failed"
                        ne.finished_at = _now()
                        await self._db.commit()
                        context["nodes"][current_node_id] = {
                            "output": {}, "status": "failed", "retry_count": attempt,
                        }
                        self._log(run_id, "error", f"Node '{current_node_id}' failed after {max_retries} retries: {e}")
                        return False

                    self._log(run_id, "warn", f"Node '{current_node_id}' retry {attempt + 1}/{max_retries}: {e}")
                    await asyncio.sleep(retry_config.get("retry_delay_seconds", 30))

            if not success:
                return False

            # Find next node
            next_id = self._find_next_node(spec, current_node_id, context)
            current_node_id = next_id

        self._log(run_id, "info", "Workflow completed successfully")
        return True

    def _find_start_node(self, spec: WorkflowSpec) -> str | None:
        """Find the node that has outgoing edges but is never a target."""
        from_ids = {e["from"] for e in spec.edges if "from" in e}
        to_ids = {e["to"] for e in spec.edges if e.get("to") is not None}
        starts = from_ids - to_ids
        if starts:
            return next(iter(starts))
        # Fallback: use first node
        if spec.nodes:
            return spec.nodes[0]["id"]
        return None

    def _find_next_node(
        self, spec: WorkflowSpec, current_node_id: str, context: TemplateContext
    ) -> str | None:
        """Evaluate outgoing edges and return the next node ID, or None if end."""
        outgoing = spec.get_outgoing_edges(current_node_id)
        if not outgoing:
            return None

        default_edge = None
        for edge in outgoing:
            if not edge.get("condition"):
                default_edge = edge
                continue
            if _evaluate_condition(edge["condition"], context):
                if edge.get("to") is None:
                    return None  # terminal edge
                return edge["to"]

        if default_edge:
            if default_edge.get("to") is None:
                return None
            return default_edge["to"]

        return None  # No edge matched, stop

    def _log(self, run_id: str, level: str, message: str) -> None:
        log = RunLog(run_id=run_id, level=level, message=message, node_id=None)
        self._db.add(log)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine/test_executor.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add topological workflow executor"
```

---

### Task 11: Engine — Scheduler (APScheduler Integration)

**Files:**
- Create: `src/wflow/engine/scheduler.py`
- Create: `tests/test_engine/test_scheduler.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_engine/test_scheduler.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from wflow.engine.scheduler import WorkflowScheduler, CronJobManager


@pytest.fixture
def db_session():
    return AsyncMock()


@pytest.fixture
def execution_svc():
    svc = MagicMock()
    svc.start_run = AsyncMock(return_value="new-run-id")
    return svc


@pytest.mark.asyncio
async def test_cron_job_manager_create_job(db_session):
    import uuid
    mgr = CronJobManager(db_session)

    job_id = await mgr.create(
        workflow_id="wf-1",
        cron_expr="0 9 * * *",
        inputs={"task": "daily"},
    )
    assert job_id is not None
    db_session.add.assert_called_once()


@pytest.mark.asyncio
async def test_cron_job_manager_list_jobs(db_session):
    from wflow.models.db import CronJob
    job = CronJob(id="cj-1", workflow_id="wf-1", cron_expr="0 9 * * *", enabled=1)
    result = MagicMock()
    result.scalars.return_value.all.return_value = [job]
    db_session.execute = AsyncMock(return_value=result)

    mgr = CronJobManager(db_session)
    jobs = await mgr.list_all()

    assert len(jobs) == 1
    assert jobs[0].cron_expr == "0 9 * * *"


@pytest.mark.asyncio
async def test_cron_job_manager_toggle(db_session):
    from wflow.models.db import CronJob
    job = CronJob(id="cj-1", workflow_id="wf-1", cron_expr="0 * * * *", enabled=1)

    result = MagicMock()
    result.scalar_one_or_none.return_value = job
    db_session.execute = AsyncMock(return_value=result)

    mgr = CronJobManager(db_session)
    updated = await mgr.toggle("cj-1")

    assert updated is not None
    assert updated.enabled == 0


@pytest.mark.asyncio
async def test_scheduler_triggers_workflow(execution_svc):
    sched = WorkflowScheduler(execution_svc)
    await sched._execute_job("wf-1", {"task": "test"})
    execution_svc.start_run.assert_called_once_with("wf-1", {"task": "test"})
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_engine/test_scheduler.py -v`
Expected: FAIL

- [ ] **Step 3: Write scheduler**

Create `src/wflow/engine/scheduler.py`:

```python
"""APScheduler integration for cron-triggered workflows."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.models.db import CronJob


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CronJobManager:
    """CRUD operations for cron jobs in SQLite."""

    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(
        self, workflow_id: str, cron_expr: str, inputs: dict[str, Any] | None = None
    ) -> str:
        job_id = str(uuid.uuid4())
        job = CronJob(
            id=job_id,
            workflow_id=workflow_id,
            cron_expr=cron_expr,
            inputs=inputs or {},
            enabled=1,
        )
        self._db.add(job)
        await self._db.commit()
        return job_id

    async def list_all(self) -> list[CronJob]:
        stmt = select(CronJob).order_by(CronJob.created_at.desc())
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, job_id: str) -> CronJob | None:
        stmt = select(CronJob).where(CronJob.id == job_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, job_id: str, **kwargs: Any) -> CronJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        for key, value in kwargs.items():
            if hasattr(job, key):
                setattr(job, key, value)
        await self._db.commit()
        return job

    async def toggle(self, job_id: str) -> CronJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        job.enabled = 1 if job.enabled == 0 else 0
        await self._db.commit()
        return job

    async def delete(self, job_id: str) -> bool:
        job = await self.get(job_id)
        if job is None:
            return False
        await self._db.delete(job)
        await self._db.commit()
        return True

    async def get_enabled(self) -> list[CronJob]:
        stmt = select(CronJob).where(CronJob.enabled == 1)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())


class WorkflowScheduler:
    """Manages APScheduler lifecycle for cron-triggered workflow execution."""

    def __init__(self, execution_svc: Any):
        self._scheduler = AsyncIOScheduler()
        self._execution_svc = execution_svc

    def start(self) -> None:
        self._scheduler.start()

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)

    def add_job(self, cron_job: CronJob) -> str:
        trigger = CronTrigger.from_crontab(cron_job.cron_expr)
        inputs = cron_job.inputs or {}
        aps_job = self._scheduler.add_job(
            self._execute_job,
            trigger=trigger,
            args=[cron_job.workflow_id, inputs],
            id=f"cron-{cron_job.id}",
            replace_existing=True,
        )
        return aps_job.id

    def remove_job(self, cron_job_id: str) -> None:
        try:
            self._scheduler.remove_job(f"cron-{cron_job_id}")
        except Exception:
            pass

    def pause_job(self, cron_job_id: str) -> None:
        try:
            self._scheduler.pause_job(f"cron-{cron_job_id}")
        except Exception:
            pass

    def resume_job(self, cron_job_id: str) -> None:
        try:
            self._scheduler.resume_job(f"cron-{cron_job_id}")
        except Exception:
            pass

    async def _execute_job(self, workflow_id: str, inputs: dict[str, Any]) -> None:
        await self._execution_svc.start_run(workflow_id, inputs)

    async def restore_jobs(self, db_session_factory) -> None:
        """Restore all enabled cron jobs from database on startup."""
        async with db_session_factory() as db:
            mgr = CronJobManager(db)
            jobs = await mgr.get_enabled()
            for job in jobs:
                self.add_job(job)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_engine/test_scheduler.py -v`
Expected: 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add cron scheduler (APScheduler)"
```

---

### Task 12: Service Layer

**Files:**
- Create: `src/wflow/services/__init__.py`
- Create: `src/wflow/services/workflow_svc.py`
- Create: `src/wflow/services/execution_svc.py`
- Create: `src/wflow/services/schedule_svc.py`
- Create: `tests/test_services/__init__.py`
- Create: `tests/test_services/test_services.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_services/test_services.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from wflow.services.workflow_svc import WorkflowService
from wflow.services.execution_svc import ExecutionService
from wflow.services.schedule_svc import ScheduleService


@pytest.fixture
def db_session():
    return AsyncMock()


@pytest.mark.asyncio
async def test_workflow_service_create(db_session):
    from wflow.models.db import Workflow
    svc = WorkflowService(db_session)

    wf = await svc.create(
        name="test-wf",
        config={
            "nodes": [{"id": "n1", "type": "script",
                       "script": {"module": "m", "function": "f", "args": {}},
                       "output": {"type": "object", "properties": {}, "required": []}}],
            "edges": [],
        },
    )

    assert wf.name == "test-wf"
    db_session.add.assert_called_once()
    db_session.commit.assert_called()


@pytest.mark.asyncio
async def test_workflow_service_list(db_session):
    from wflow.models.db import Workflow
    wf1 = Workflow(id="w1", name="a", config="{}")
    wf2 = Workflow(id="w2", name="b", config="{}")
    result = MagicMock()
    result.scalars.return_value.all.return_value = [wf1, wf2]
    db_session.execute = AsyncMock(return_value=result)

    svc = WorkflowService(db_session)
    workflows = await svc.list_all()

    assert len(workflows) == 2


@pytest.mark.asyncio
async def test_execution_service_start_run(db_session):
    from wflow.models.db import Workflow, WorkflowRun
    wf = Workflow(id="w1", name="test", config=(
        '{"nodes":[{"id":"n1","type":"script","script":{"module":"m","function":"f","args":{}},'
        '"output":{"type":"object","properties":{},"required":[]}}],"edges":[]}'
    ))

    result_wf = MagicMock()
    result_wf.scalar_one_or_none.return_value = wf
    db_session.execute = AsyncMock(return_value=result_wf)

    engine = MagicMock()
    engine.start_run = AsyncMock()

    svc = ExecutionService(db_session, engine)
    run = await svc.start_run("w1", {"task": "hello"})

    assert run.workflow_id == "w1"
    assert run.status == "pending"


@pytest.mark.asyncio
async def test_execution_service_pause_run(db_session):
    from wflow.models.db import WorkflowRun
    run = WorkflowRun(id="r1", workflow_id="w1", status="running", context="{}")
    result = MagicMock()
    result.scalar_one_or_none.return_value = run
    db_session.execute = AsyncMock(return_value=result)

    svc = ExecutionService(db_session, MagicMock())
    updated = await svc.pause_run("r1")

    assert updated.status == "paused"


@pytest.mark.asyncio
async def test_schedule_service_create(db_session):
    svc = ScheduleService(db_session)
    job = await svc.create("wf-1", "0 9 * * *")
    assert job.workflow_id == "wf-1"
    assert job.cron_expr == "0 9 * * *"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_services/test_services.py -v`
Expected: FAIL

- [ ] **Step 3: Write services**

Create `src/wflow/services/workflow_svc.py`:

```python
"""Workflow CRUD service."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.models.db import Workflow


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkflowService:
    def __init__(self, db: AsyncSession):
        self._db = db

    async def create(self, name: str, config: dict[str, Any], description: str = "") -> Workflow:
        wf = Workflow(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            config=json.dumps(config),
        )
        self._db.add(wf)
        await self._db.commit()
        return wf

    async def list_all(self, status: str | None = None) -> list[Workflow]:
        stmt = select(Workflow)
        if status:
            stmt = stmt.where(Workflow.status == status)
        stmt = stmt.order_by(Workflow.created_at.desc())
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, workflow_id: str) -> Workflow | None:
        stmt = select(Workflow).where(Workflow.id == workflow_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def update(self, workflow_id: str, **kwargs: Any) -> Workflow | None:
        wf = await self.get(workflow_id)
        if wf is None:
            return None
        for key, value in kwargs.items():
            if key == "config" and isinstance(value, dict):
                value = json.dumps(value)
            if hasattr(wf, key):
                setattr(wf, key, value)
        wf.updated_at = _now()
        await self._db.commit()
        return wf

    async def delete(self, workflow_id: str) -> bool:
        wf = await self.get(workflow_id)
        if wf is None:
            return False
        await self._db.delete(wf)
        await self._db.commit()
        return True
```

Create `src/wflow/services/execution_svc.py`:

```python
"""Execution service — manages workflow runs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.models.db import WorkflowRun, NodeExecution, RunLog
from wflow.models.workflow import WorkflowSpec
from wflow.engine.state_machine import RunStateMachine, RunStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionService:
    def __init__(self, db: AsyncSession, engine: Any):
        self._db = db
        self._engine = engine
        self._state_machine = RunStateMachine()

    async def start_run(self, workflow_id: str, inputs: dict[str, Any]) -> WorkflowRun:
        run = WorkflowRun(
            id=str(uuid.uuid4()),
            workflow_id=workflow_id,
            status=RunStatus.PENDING.value,
            context=json.dumps({"inputs": inputs}),
        )
        self._db.add(run)
        await self._db.commit()
        return run

    async def get_run(self, run_id: str) -> WorkflowRun | None:
        stmt = select(WorkflowRun).where(WorkflowRun.id == run_id)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_runs(
        self, workflow_id: str | None = None, status: str | None = None, limit: int = 50
    ) -> list[WorkflowRun]:
        stmt = select(WorkflowRun)
        if workflow_id:
            stmt = stmt.where(WorkflowRun.workflow_id == workflow_id)
        if status:
            stmt = stmt.where(WorkflowRun.status == status)
        stmt = stmt.order_by(WorkflowRun.started_at.desc()).limit(limit)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def pause_run(self, run_id: str) -> WorkflowRun | None:
        run = await self.get_run(run_id)
        if run is None:
            return None
        run.status = self._state_machine.transition(
            RunStatus(run.status), RunStatus.PAUSED
        ).value
        await self._db.commit()
        return run

    async def resume_run(self, run_id: str) -> WorkflowRun | None:
        run = await self.get_run(run_id)
        if run is None:
            return None
        run.status = self._state_machine.transition(
            RunStatus(run.status), RunStatus.RUNNING
        ).value
        await self._db.commit()
        return run

    async def stop_run(self, run_id: str) -> WorkflowRun | None:
        run = await self.get_run(run_id)
        if run is None:
            return None
        run.status = RunStatus.FAILED.value
        run.finished_at = _now()
        await self._db.commit()
        return run

    async def get_logs(
        self, run_id: str, level: str | None = None, limit: int = 100
    ) -> list[RunLog]:
        stmt = select(RunLog).where(RunLog.run_id == run_id)
        if level:
            stmt = stmt.where(RunLog.level == level)
        stmt = stmt.order_by(RunLog.timestamp.desc()).limit(limit)
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get_node_execution(self, run_id: str, node_id: str) -> NodeExecution | None:
        stmt = select(NodeExecution).where(
            NodeExecution.run_id == run_id,
            NodeExecution.node_id == node_id,
        ).order_by(NodeExecution.started_at.desc())
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_node_executions(self, run_id: str) -> list[NodeExecution]:
        stmt = select(NodeExecution).where(
            NodeExecution.run_id == run_id
        ).order_by(NodeExecution.started_at.asc())
        result = await self._db.execute(stmt)
        return list(result.scalars().all())
```

Create `src/wflow/services/schedule_svc.py`:

```python
"""Schedule service — cron job management."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from wflow.engine.scheduler import CronJobManager
from wflow.models.db import CronJob


class ScheduleService:
    def __init__(self, db: AsyncSession):
        self._db = db
        self._mgr = CronJobManager(db)

    async def create(self, workflow_id: str, cron_expr: str) -> CronJob:
        return await self._mgr.create(workflow_id, cron_expr)

    async def list_all(self) -> list[CronJob]:
        return await self._mgr.list_all()

    async def get(self, job_id: str) -> CronJob | None:
        return await self._mgr.get(job_id)

    async def update(self, job_id: str, **kwargs) -> CronJob | None:
        return await self._mgr.update(job_id, **kwargs)

    async def delete(self, job_id: str) -> bool:
        return await self._mgr.delete(job_id)

    async def toggle(self, job_id: str) -> CronJob | None:
        return await self._mgr.toggle(job_id)
```

- [ ] **Step 4: Run tests to verify**

Run: `pytest tests/test_services/test_services.py -v`
Expected: 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add service layer"
```

---

### Task 13: REST API — App Factory, Dependencies, Status Routes

**Files:**
- Create: `src/wflow/main.py`
- Create: `src/wflow/api/__init__.py`
- Create: `src/wflow/api/deps.py`
- Create: `src/wflow/api/status.py`

- [ ] **Step 1: Write main.py (FastAPI app factory)**

Create `src/wflow/main.py`:

```python
"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from wflow.models.db import Base


def create_app(db_url: str = "sqlite+aiosqlite:///./data/workflows.db") -> FastAPI:
    engine = create_async_engine(db_url, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app.state.engine = engine
        app.state.SessionLocal = SessionLocal
        yield
        # Shutdown
        await engine.dispose()

    app = FastAPI(title="WFlow", version="0.1.0", lifespan=lifespan)

    from wflow.api.status import router as status_router
    app.include_router(status_router, prefix="/api/v1")

    # Serve Web UI static files
    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app
```

- [ ] **Step 2: Write deps.py**

Create `src/wflow/api/deps.py`:

```python
"""FastAPI dependency injection."""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncSession:
    """Yield an async database session."""
    session_local = request.app.state.SessionLocal
    async with session_local() as session:
        yield session
```

- [ ] **Step 3: Write status.py**

Create `src/wflow/api/status.py`:

```python
"""Status and health-check endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from wflow.api.deps import get_db
from wflow.models.db import WorkflowRun, Session

router = APIRouter(tags=["status"])


@router.get("/status")
async def get_status(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select, func
    running = await db.execute(
        select(func.count()).select_from(WorkflowRun).where(WorkflowRun.status == "running")
    )
    running_count = running.scalar() or 0

    active_sessions = await db.execute(
        select(func.count()).select_from(Session).where(Session.status == "active")
    )
    active_session_count = active_sessions.scalar() or 0

    return {
        "status": "ok",
        "running_workflows": running_count,
        "active_sessions": active_session_count,
    }


@router.get("/status/sessions")
async def list_sessions(db: AsyncSession = Depends(get_db)):
    stmt = select(Session).where(Session.status == "active").limit(100)
    result = await db.execute(stmt)
    sessions = result.scalars().all()
    return [
        {
            "id": s.id,
            "run_id": s.run_id,
            "node_id": s.node_id,
            "status": s.status,
            "created_at": s.created_at,
        }
        for s in sessions
    ]
```

- [ ] **Step 4: Test app starts successfully**

Run:
```bash
python -c "from wflow.main import create_app; app = create_app('sqlite+aiosqlite:///file:test-status?mode=memory&cache=shared&uri=true'); print('App created:', app.title)"
```
Expected: `App created: WFlow`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add FastAPI app factory, deps, and status routes"
```

---

### Task 14: REST API — Workflow Routes

**Files:**
- Create: `src/wflow/api/workflows.py`
- Create: `tests/test_api/__init__.py`
- Create: `tests/test_api/test_workflows.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_api/test_workflows.py`:

```python
import json
import pytest
from httpx import AsyncClient, ASGITransport
from wflow.main import create_app


@pytest.fixture
def app():
    return create_app("sqlite+aiosqlite:///file:test-wf-api?mode=memory&cache=shared&uri=true")


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_workflow(client):
    payload = {
        "name": "test-wf",
        "description": "A test workflow",
        "config": {
            "nodes": [{
                "id": "n1", "type": "script",
                "script": {"module": "m", "function": "f", "args": {}},
                "output": {"type": "object", "properties": {}, "required": []},
            }],
            "edges": [],
        },
    }
    resp = await client.post("/api/v1/workflows", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "test-wf"
    assert data["id"] is not None


@pytest.mark.asyncio
async def test_list_workflows(client):
    resp = await client.get("/api/v1/workflows")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_workflow_not_found(client):
    resp = await client.get("/api/v1/workflows/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: Write workflow routes**

Create `src/wflow/api/workflows.py`:

```python
"""Workflow CRUD endpoints."""

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from wflow.api.deps import get_db
from wflow.services.workflow_svc import WorkflowService

router = APIRouter(prefix="/workflows", tags=["workflows"])


class CreateWorkflowRequest(BaseModel):
    name: str
    description: str = ""
    config: dict


@router.post("", status_code=201)
async def create_workflow(req: CreateWorkflowRequest, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    wf = await svc.create(name=req.name, config=req.config, description=req.description)
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "config": json.loads(wf.config),
        "status": wf.status,
        "created_at": wf.created_at,
        "updated_at": wf.updated_at,
    }


@router.get("")
async def list_workflows(status: str | None = None, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    workflows = await svc.list_all(status=status)
    return [
        {
            "id": w.id,
            "name": w.name,
            "description": w.description,
            "status": w.status,
            "created_at": w.created_at,
        }
        for w in workflows
    ]


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    wf = await svc.get(workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {
        "id": wf.id,
        "name": wf.name,
        "description": wf.description,
        "config": json.loads(wf.config),
        "status": wf.status,
        "created_at": wf.created_at,
        "updated_at": wf.updated_at,
    }


@router.put("/{workflow_id}")
async def update_workflow(workflow_id: str, req: CreateWorkflowRequest, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    wf = await svc.update(workflow_id, name=req.name, config=req.config, description=req.description)
    if wf is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return {"id": wf.id, "name": wf.name, "status": wf.status}


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(workflow_id: str, db: AsyncSession = Depends(get_db)):
    svc = WorkflowService(db)
    deleted = await svc.delete(workflow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Workflow not found")
```

Now register in main.py. Edit `src/wflow/main.py` to add the import and router registration.

- [ ] **Step 3: Run tests to verify**

Run: `pytest tests/test_api/test_workflows.py -v`
Expected: 3 tests PASS

- [ ] **Step 4: Update main.py**

Edit `src/wflow/main.py`, add after the status router line:
```python
from wflow.api.workflows import router as workflow_router
app.include_router(workflow_router, prefix="/api/v1")
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add workflow REST API routes"
```

---

### Task 15: REST API — Run Routes

**Files:**
- Create: `src/wflow/api/runs.py`
- Create: `tests/test_api/test_runs.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_api/test_runs.py`:

```python
import json
import pytest
from httpx import AsyncClient, ASGITransport
from wflow.main import create_app


@pytest.fixture
def app():
    return create_app("sqlite+aiosqlite:///file:test-run-api?mode=memory&cache=shared&uri=true")


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_and_list_runs(client):
    # First create a workflow
    wf_payload = {
        "name": "test", "config": {
            "nodes": [{
                "id": "n1", "type": "script",
                "script": {"module": "m", "function": "f", "args": {}},
                "output": {"type": "object", "properties": {}, "required": []},
            }],
            "edges": [],
        },
    }
    wf_resp = await client.post("/api/v1/workflows", json=wf_payload)
    wf_id = wf_resp.json()["id"]

    # Start a run
    run_resp = await client.post("/api/v1/runs", json={"workflow_id": wf_id, "inputs": {"task": "hello"}})
    assert run_resp.status_code == 201
    data = run_resp.json()
    assert data["workflow_id"] == wf_id
    assert data["status"] == "pending"

    run_id = data["id"]

    # Get run detail
    detail = await client.get(f"/api/v1/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == run_id

    # List runs
    runs = await client.get("/api/v1/runs")
    assert runs.status_code == 200
    assert len(runs.json()) >= 1


@pytest.mark.asyncio
async def test_run_not_found(client):
    resp = await client.get("/api/v1/runs/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: Write run routes**

Create `src/wflow/api/runs.py`:

```python
"""Run management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from wflow.api.deps import get_db
from wflow.services.execution_svc import ExecutionService

router = APIRouter(prefix="/runs", tags=["runs"])


class StartRunRequest(BaseModel):
    workflow_id: str
    inputs: dict = {}


@router.post("", status_code=201)
async def start_run(req: StartRunRequest, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.start_run(req.workflow_id, req.inputs)
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "status": run.status,
        "current_node_id": run.current_node_id,
        "started_at": run.started_at,
    }


@router.get("")
async def list_runs(
    workflow_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    svc = ExecutionService(db, engine=None)
    runs = await svc.list_runs(workflow_id=workflow_id, status=status, limit=limit)
    return [
        {
            "id": r.id,
            "workflow_id": r.workflow_id,
            "status": r.status,
            "current_node_id": r.current_node_id,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
        }
        for r in runs
    ]


@router.get("/{run_id}")
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    node_executions = await svc.get_node_executions(run_id)
    return {
        "id": run.id,
        "workflow_id": run.workflow_id,
        "status": run.status,
        "current_node_id": run.current_node_id,
        "context": run.context,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "nodes": [
            {
                "id": ne.id,
                "node_id": ne.node_id,
                "type": ne.type,
                "status": ne.status,
                "retry_count": ne.retry_count,
                "input": ne.input,
                "output": ne.output,
                "error": ne.error,
            }
            for ne in node_executions
        ],
    }


@router.post("/{run_id}/pause")
async def pause_run(run_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.pause_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"id": run.id, "status": run.status}


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.resume_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"id": run.id, "status": run.status}


@router.post("/{run_id}/stop")
async def stop_run(run_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    run = await svc.stop_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"id": run.id, "status": run.status}


@router.get("/{run_id}/logs")
async def get_logs(
    run_id: str, level: str | None = None, limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    svc = ExecutionService(db, engine=None)
    run = await svc.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    logs = await svc.get_logs(run_id, level=level, limit=limit)
    return [
        {"id": l.id, "node_id": l.node_id, "level": l.level,
         "message": l.message, "timestamp": l.timestamp}
        for l in logs
    ]


@router.get("/{run_id}/nodes/{node_id}")
async def get_node_execution(run_id: str, node_id: str, db: AsyncSession = Depends(get_db)):
    svc = ExecutionService(db, engine=None)
    ne = await svc.get_node_execution(run_id, node_id)
    if ne is None:
        raise HTTPException(status_code=404, detail="Node execution not found")
    return {
        "id": ne.id,
        "node_id": ne.node_id,
        "type": ne.type,
        "status": ne.status,
        "session_id": ne.session_id,
        "retry_count": ne.retry_count,
        "input": ne.input,
        "output": ne.output,
        "error": ne.error,
        "started_at": ne.started_at,
        "finished_at": ne.finished_at,
    }
```

Register in main.py and add the import.

- [ ] **Step 3: Run tests**

Run: `pytest tests/test_api/test_runs.py -v`
Expected: 2 tests PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: add run REST API routes"
```

---

### Task 16: REST API — Cron Routes + Full main.py Assembly

**Files:**
- Create: `src/wflow/api/cron.py`
- Create: `tests/test_api/test_cron.py`
- Modify: `src/wflow/main.py` (final assembly)

- [ ] **Step 1: Write cron routes**

Create `src/wflow/api/cron.py`:

```python
"""Cron job management endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from wflow.api.deps import get_db
from wflow.services.schedule_svc import ScheduleService

router = APIRouter(prefix="/cron", tags=["cron"])


class CreateCronRequest(BaseModel):
    workflow_id: str
    cron_expr: str
    inputs: dict = {}


@router.post("", status_code=201)
async def create_cron_job(req: CreateCronRequest, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db)
    job = await svc.create(req.workflow_id, req.cron_expr)
    return {
        "id": job.id,
        "workflow_id": job.workflow_id,
        "cron_expr": job.cron_expr,
        "enabled": bool(job.enabled),
        "created_at": job.created_at,
    }


@router.get("")
async def list_cron_jobs(db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db)
    jobs = await svc.list_all()
    return [
        {
            "id": j.id,
            "workflow_id": j.workflow_id,
            "cron_expr": j.cron_expr,
            "enabled": bool(j.enabled),
            "last_run_id": j.last_run_id,
            "next_fire_at": j.next_fire_at,
            "created_at": j.created_at,
        }
        for j in jobs
    ]


@router.put("/{job_id}")
async def update_cron_job(job_id: str, req: CreateCronRequest, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db)
    job = await svc.update(job_id, cron_expr=req.cron_expr)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    return {"id": job.id, "cron_expr": job.cron_expr}


@router.delete("/{job_id}", status_code=204)
async def delete_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db)
    deleted = await svc.delete(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Cron job not found")


@router.post("/{job_id}/toggle")
async def toggle_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db)
    job = await svc.toggle(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    return {"id": job.id, "enabled": bool(job.enabled)}


@router.post("/{job_id}/trigger")
async def trigger_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    svc = ScheduleService(db)
    job = await svc.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Cron job not found")
    # Trigger the workflow execution
    from wflow.services.execution_svc import ExecutionService
    exec_svc = ExecutionService(db, engine=None)
    run = await exec_svc.start_run(job.workflow_id, {})
    return {"run_id": run.id, "status": run.status}
```

- [ ] **Step 2: Write cron test**

Create `tests/test_api/test_cron.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from wflow.main import create_app


@pytest.fixture
def app():
    return create_app("sqlite+aiosqlite:///file:test-cron-api?mode=memory&cache=shared&uri=true")


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.mark.asyncio
async def test_create_and_list_cron(client):
    # Create a workflow first
    wf = await client.post("/api/v1/workflows", json={
        "name": "wf", "config": {
            "nodes": [{
                "id": "n1", "type": "script",
                "script": {"module": "m", "function": "f", "args": {}},
                "output": {"type": "object", "properties": {}, "required": []},
            }],
            "edges": [],
        },
    })
    wf_id = wf.json()["id"]

    # Create cron
    resp = await client.post("/api/v1/cron", json={
        "workflow_id": wf_id, "cron_expr": "0 9 * * *",
    })
    assert resp.status_code == 201
    assert resp.json()["cron_expr"] == "0 9 * * *"

    # List cron
    jobs = await client.get("/api/v1/cron")
    assert jobs.status_code == 200
    assert len(jobs.json()) == 1

    # Toggle
    job_id = resp.json()["id"]
    toggle = await client.post(f"/api/v1/cron/{job_id}/toggle")
    assert toggle.status_code == 200
    assert toggle.json()["enabled"] is False
```

- [ ] **Step 3: Final main.py assembly**

Rewrite `src/wflow/main.py` to include all routers:

```python
"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from wflow.models.db import Base


def create_app(db_url: str = "sqlite+aiosqlite:///./data/workflows.db") -> FastAPI:
    engine = create_async_engine(db_url, echo=False)
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app.state.engine = engine
        app.state.SessionLocal = SessionLocal
        yield
        await engine.dispose()

    app = FastAPI(title="WFlow", version="0.1.0", lifespan=lifespan)

    from wflow.api.status import router as status_router
    from wflow.api.workflows import router as workflow_router
    from wflow.api.runs import router as run_router
    from wflow.api.cron import router as cron_router

    app.include_router(status_router, prefix="/api/v1")
    app.include_router(workflow_router, prefix="/api/v1")
    app.include_router(run_router, prefix="/api/v1")
    app.include_router(cron_router, prefix="/api/v1")

    web_dir = Path(__file__).parent / "web"
    if web_dir.exists():
        app.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")

    return app
```

- [ ] **Step 4: Run all API tests**

Run: `pytest tests/test_api/ -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add cron routes, finalize FastAPI assembly"
```

---

### Task 17: CLI — Main + Server Commands

**Files:**
- Create: `src/wflow/cli/__init__.py`
- Create: `src/wflow/cli/main.py`
- Create: `src/wflow/cli/server.py`
- Create: `tests/test_cli/__init__.py`
- Create: `tests/test_cli/test_commands.py`

- [ ] **Step 1: Write CLI main**

Create `src/wflow/cli/main.py`:

```python
"""WFlow CLI — thin HTTP client over FastAPI."""

import click
import os

API_URL = os.environ.get("WFLOW_SERVER_URL", "http://127.0.0.1:8100")


@click.group()
@click.version_option(version="0.1.0")
def cli():
    """WFlow — Claude Code CLI workflow orchestrator."""
    pass


@cli.group()
def server():
    """Server management commands."""
    pass


@server.command("start")
@click.option("--host", default="127.0.0.1", help="Bind address")
@click.option("--port", default=8100, type=int, help="Bind port")
@click.option("--db", default="./data/workflows.db", help="SQLite database path")
def server_start(host, port, db):
    """Start the WFlow server."""
    import uvicorn
    click.echo(f"Starting WFlow server on {host}:{port}...")
    click.echo(f"Database: {db}")
    os.environ["WFLOW_DB_URL"] = f"sqlite+aiosqlite:///{db}"
    uvicorn.run("wflow.main:create_app", host=host, port=port, factory=True)


@cli.group()
def workflow():
    """Workflow management commands."""
    pass


@workflow.command("list")
@click.option("--status", default=None, help="Filter by status")
def workflow_list(status):
    """List all workflows."""
    import httpx
    params = {}
    if status:
        params["status"] = status
    resp = httpx.get(f"{API_URL}/api/v1/workflows", params=params)
    resp.raise_for_status()
    workflows = resp.json()
    if not workflows:
        click.echo("No workflows found.")
        return
    for w in workflows:
        click.echo(f"  {w['id'][:8]}  {w['name']:20s}  {w['status']:10s}  {w['created_at']}")


@workflow.command("show")
@click.argument("workflow_id")
def workflow_show(workflow_id):
    """Show workflow details."""
    import httpx, json
    resp = httpx.get(f"{API_URL}/api/v1/workflows/{workflow_id}")
    if resp.status_code == 404:
        click.echo(f"Workflow '{workflow_id}' not found.", err=True)
        return
    resp.raise_for_status()
    wf = resp.json()
    click.echo(f"Name: {wf['name']}")
    click.echo(f"Status: {wf['status']}")
    click.echo(f"Config:\n{json.dumps(wf['config'], indent=2)}")


@workflow.command("create")
@click.argument("file", type=click.Path(exists=True))
def workflow_create(file):
    """Create a workflow from a JSON file."""
    import httpx, json
    with open(file, "r", encoding="utf-8") as f:
        config_data = json.load(f)
    payload = {
        "name": config_data.pop("name", Path(file).stem),
        "config": config_data,
    }
    resp = httpx.post(f"{API_URL}/api/v1/workflows", json=payload)
    resp.raise_for_status()
    wf = resp.json()
    click.echo(f"Created workflow: {wf['id']} ({wf['name']})")


@cli.group()
def run():
    """Run management commands."""
    pass


@run.command("start")
@click.argument("workflow_id")
@click.option("--input", "-i", "inputs", multiple=True, help="Input key=value (repeatable)")
@click.option("--watch", is_flag=True, help="Follow logs after starting")
def run_start(workflow_id, inputs, watch):
    """Start a workflow run."""
    import httpx, json, time
    input_dict = {}
    for inp in inputs:
        key, _, value = inp.partition("=")
        input_dict[key] = value

    resp = httpx.post(f"{API_URL}/api/v1/runs", json={
        "workflow_id": workflow_id,
        "inputs": input_dict,
    })
    resp.raise_for_status()
    run = resp.json()
    click.echo(f"Started run: {run['id']} (status: {run['status']})")

    if watch:
        click.echo("Watching logs (Ctrl+C to stop)...")
        try:
            while True:
                time.sleep(2)
                log_resp = httpx.get(f"{API_URL}/api/v1/runs/{run['id']}/logs?limit=5")
                for log in reversed(log_resp.json()):
                    click.echo(f"  [{log['level']}] {log['message']}")
                status_resp = httpx.get(f"{API_URL}/api/v1/runs/{run['id']}")
                status = status_resp.json()["status"]
                if status in ("completed", "failed"):
                    click.echo(f"Run {status}.")
                    break
        except KeyboardInterrupt:
            click.echo("\nStopped watching.")


@run.command("status")
@click.argument("run_id")
def run_status(run_id):
    """Check run status."""
    import httpx, json
    resp = httpx.get(f"{API_URL}/api/v1/runs/{run_id}")
    if resp.status_code == 404:
        click.echo(f"Run '{run_id}' not found.", err=True)
        return
    resp.raise_for_status()
    r = resp.json()
    click.echo(f"Run: {r['id']}")
    click.echo(f"Status: {r['status']}")
    click.echo(f"Current node: {r.get('current_node_id', 'N/A')}")
    click.echo(f"Nodes:")
    for n in r.get("nodes", []):
        icon = {"completed": "✓", "running": "◉", "failed": "✗", "pending": "○"}.get(n["status"], "?")
        click.echo(f"  {icon} {n['node_id']} ({n['type']}) — {n['status']}")


@run.command("pause")
@click.argument("run_id")
def run_pause(run_id):
    """Pause a running workflow."""
    import httpx
    resp = httpx.post(f"{API_URL}/api/v1/runs/{run_id}/pause")
    resp.raise_for_status()
    click.echo(f"Run '{run_id}' paused.")


@run.command("resume")
@click.argument("run_id")
def run_resume(run_id):
    """Resume a paused workflow."""
    import httpx
    resp = httpx.post(f"{API_URL}/api/v1/runs/{run_id}/resume")
    resp.raise_for_status()
    click.echo(f"Run '{run_id}' resumed.")


@run.command("stop")
@click.argument("run_id")
def run_stop(run_id):
    """Stop a running workflow."""
    import httpx
    resp = httpx.post(f"{API_URL}/api/v1/runs/{run_id}/stop")
    resp.raise_for_status()
    click.echo(f"Run '{run_id}' stopped.")


@run.command("logs")
@click.argument("run_id")
@click.option("--follow", "-f", is_flag=True, help="Follow logs")
@click.option("--level", default=None, help="Filter by level")
def run_logs(run_id, follow, level):
    """View run logs."""
    import httpx, time
    if follow:
        click.echo("Following logs (Ctrl+C to stop)...")
        seen = set()
        try:
            while True:
                params = {"limit": 50}
                if level:
                    params["level"] = level
                resp = httpx.get(f"{API_URL}/api/v1/runs/{run_id}/logs", params=params)
                for log in reversed(resp.json()):
                    if log["id"] not in seen:
                        seen.add(log["id"])
                        click.echo(f"[{log['level']:5s}] {log['timestamp']} {log['message']}")
                time.sleep(2)
        except KeyboardInterrupt:
            click.echo("\nDone.")
    else:
        params = {"limit": 100}
        if level:
            params["level"] = level
        resp = httpx.get(f"{API_URL}/api/v1/runs/{run_id}/logs", params=params)
        for log in reversed(resp.json()):
            click.echo(f"[{log['level']:5s}] {log['message']}")


@cli.group()
def cron():
    """Cron job management commands."""
    pass


@cron.command("list")
def cron_list():
    """List cron jobs."""
    import httpx
    resp = httpx.get(f"{API_URL}/api/v1/cron")
    resp.raise_for_status()
    jobs = resp.json()
    if not jobs:
        click.echo("No cron jobs.")
        return
    for j in jobs:
        enabled_str = "✓" if j["enabled"] else "✗"
        click.echo(f"  {j['id'][:8]}  {j['workflow_id'][:8]}  [{enabled_str}]  {j['cron_expr']}")


@cron.command("add")
@click.argument("workflow_id")
@click.argument("cron_expr")
def cron_add(workflow_id, cron_expr):
    """Add a cron job."""
    import httpx
    resp = httpx.post(f"{API_URL}/api/v1/cron", json={
        "workflow_id": workflow_id,
        "cron_expr": cron_expr,
    })
    resp.raise_for_status()
    click.echo(f"Created cron job: {resp.json()['id']}")


@cron.command("remove")
@click.argument("cron_id")
def cron_remove(cron_id):
    """Remove a cron job."""
    import httpx
    resp = httpx.delete(f"{API_URL}/api/v1/cron/{cron_id}")
    resp.raise_for_status()
    click.echo(f"Cron job '{cron_id}' removed.")


@cron.command("toggle")
@click.argument("cron_id")
def cron_toggle(cron_id):
    """Enable/disable a cron job."""
    import httpx
    resp = httpx.post(f"{API_URL}/api/v1/cron/{cron_id}/toggle")
    resp.raise_for_status()
    enabled = "enabled" if resp.json()["enabled"] else "disabled"
    click.echo(f"Cron job '{cron_id}' {enabled}.")
```

Note: add `from pathlib import Path` to the import at the top since `workflow_create` uses `Path`.

- [ ] **Step 2: Write CLI test**

Create `tests/test_cli/test_commands.py`:

```python
from click.testing import CliRunner
from wflow.cli.main import cli


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "workflow" in result.output
    assert "run" in result.output
    assert "cron" in result.output


def test_server_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["server", "--help"])
    assert result.exit_code == 0
    assert "start" in result.output


def test_workflow_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["workflow", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "show" in result.output
```

- [ ] **Step 3: Run CLI tests**

Run: `pytest tests/test_cli/test_commands.py -v`
Expected: 3 tests PASS

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: add CLI commands"
```

---

### Task 18: Web UI — Static Files

**Files:**
- Create: `src/wflow/web/index.html`
- Create: `src/wflow/web/css/style.css`
- Create: `src/wflow/web/js/app.js`
- Create: `src/wflow/web/js/utils.js`
- Create: `src/wflow/web/favicon.ico`

- [ ] **Step 1: Write index.html**

Create `src/wflow/web/index.html`:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>WFlow — Workflow Orchestrator</title>
  <link rel="stylesheet" href="/css/style.css">
  <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
  <script defer src="https://cdn.jsdelivr.net/npm/htmx.org@1.9.x/dist/htmx.min.js"></script>
  <script defer src="/js/utils.js"></script>
  <script defer src="/js/app.js"></script>
</head>
<body x-data="app()" x-init="init()">
  <nav class="topnav">
    <h1>⚡ WFlow</h1>
    <div class="nav-links">
      <a href="#" @click.prevent="page='dashboard'" :class="{active: page==='dashboard'}">Dashboard</a>
      <a href="#" @click.prevent="page='workflows'" :class="{active: page==='workflows'}">Workflows</a>
      <a href="#" @click.prevent="page='runs'" :class="{active: page==='runs'}">Runs</a>
      <a href="#" @click.prevent="page='cron'" :class="{active: page==='cron'}">Cron</a>
    </div>
  </nav>

  <main>
    <!-- Dashboard -->
    <div x-show="page === 'dashboard'" x-html="dashboardHTML"></div>

    <!-- Workflows -->
    <div x-show="page === 'workflows'">
      <h2>Workflows</h2>
      <button @click="showCreateWorkflow=true">+ Create Workflow</button>
      <div x-show="showCreateWorkflow" style="margin:16px 0">
        <textarea x-ref="wfConfig" rows="10" placeholder='{"name":"...","nodes":[...],"edges":[...]}'></textarea>
        <button @click="createWorkflow()">Save</button>
        <button @click="showCreateWorkflow=false">Cancel</button>
      </div>
      <table>
        <thead><tr><th>Name</th><th>Status</th><th>Created</th><th>Actions</th></tr></thead>
        <tbody>
          <template x-for="w in workflows" :key="w.id">
            <tr>
              <td x-text="w.name"></td>
              <td><span class="badge" x-text="w.status" :class="w.status"></span></td>
              <td x-text="w.created_at?.slice(0,10)"></td>
              <td>
                <button @click="startRun(w.id)">Run</button>
                <button @click="viewWorkflow(w.id)">View</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>

    <!-- Runs -->
    <div x-show="page === 'runs'" x-html="runsHTML"></div>

    <!-- Cron -->
    <div x-show="page === 'cron'">
      <h2>Cron Jobs</h2>
      <table>
        <thead><tr><th>Workflow</th><th>Cron</th><th>Enabled</th><th>Actions</th></tr></thead>
        <tbody>
          <template x-for="j in cronJobs" :key="j.id">
            <tr>
              <td x-text="j.workflow_id?.slice(0,8)"></td>
              <td><code x-text="j.cron_expr"></code></td>
              <td x-text="j.enabled ? '✓' : '✗'"></td>
              <td>
                <button @click="toggleCron(j.id)">Toggle</button>
                <button @click="deleteCron(j.id)">Delete</button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
    </div>
  </main>

  <footer>WFlow v0.1.0</footer>
</body>
</html>
```

- [ ] **Step 2: Write CSS**

Create `src/wflow/web/css/style.css`:

```css
:root {
  --bg: #0d1117;
  --surface: #161b22;
  --border: #30363d;
  --text: #c9d1d9;
  --muted: #8b949e;
  --accent: #58a6ff;
  --green: #3fb950;
  --red: #f85149;
  --yellow: #d2991d;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  background: var(--bg); color: var(--text); min-height: 100vh; }
.topnav { display: flex; align-items: center; gap: 24px; padding: 12px 24px;
  background: var(--surface); border-bottom: 1px solid var(--border); }
.topnav h1 { font-size: 18px; color: var(--accent); }
.nav-links { display: flex; gap: 16px; }
.nav-links a { color: var(--muted); text-decoration: none; padding: 4px 8px; border-radius: 4px; }
.nav-links a:hover, .nav-links a.active { color: var(--text); background: var(--border); }
main { max-width: 1100px; margin: 24px auto; padding: 0 24px; }
h2 { margin-bottom: 16px; font-size: 22px; }
table { width: 100%; border-collapse: collapse; margin-top: 12px; }
th, td { text-align: left; padding: 10px 14px; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-weight: 600; font-size: 13px; }
tr:hover { background: var(--surface); }
button { background: var(--accent); color: #fff; border: none; padding: 6px 14px;
  border-radius: 4px; cursor: pointer; font-size: 13px; margin-right: 4px; }
button:hover { opacity: 0.85; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }
.badge.running { background: var(--accent); }
.badge.completed { background: var(--green); }
.badge.failed { background: var(--red); color: #fff; }
.badge.paused { background: var(--yellow); }
.badge.pending { background: var(--muted); }
textarea { width: 100%; background: var(--surface); color: var(--text);
  border: 1px solid var(--border); border-radius: 4px; padding: 12px;
  font-family: monospace; font-size: 13px; }
code { background: var(--surface); padding: 2px 6px; border-radius: 3px; font-size: 12px; }
footer { text-align: center; padding: 20px; color: var(--muted); font-size: 12px; }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px; margin-bottom: 24px; }
.stat-card { background: var(--surface); border: 1px solid var(--border);
  border-radius: 8px; padding: 20px; }
.stat-card .value { font-size: 32px; font-weight: 700; }
.stat-card .label { font-size: 13px; color: var(--muted); margin-top: 4px; }
```

- [ ] **Step 3: Write JS utils**

Create `src/wflow/web/js/utils.js`:

```javascript
const API = '/api/v1';

async function apiGet(path, params = {}) {
  const qs = new URLSearchParams(params).toString();
  const url = qs ? `${API}${path}?${qs}` : `${API}${path}`;
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`GET ${path}: ${resp.status}`);
  return resp.json();
}

async function apiPost(path, body = {}) {
  const resp = await fetch(`${API}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!resp.ok) throw new Error(`POST ${path}: ${resp.status}`);
  return resp.json();
}

async function apiDelete(path) {
  const resp = await fetch(`${API}${path}`, { method: 'DELETE' });
  if (!resp.ok) throw new Error(`DELETE ${path}: ${resp.status}`);
}
```

- [ ] **Step 4: Write JS app**

Create `src/wflow/web/js/app.js`:

```javascript
function app() {
  return {
    page: 'dashboard',
    workflows: [],
    runs: [],
    cronJobs: [],
    dashboardHTML: '',
    runsHTML: '',
    showCreateWorkflow: false,

    async init() {
      await this.loadDashboard();
    },

    async loadDashboard() {
      try {
        const status = await apiGet('/status');
        this.dashboardHTML = `
          <h2>Dashboard</h2>
          <div class="stats">
            <div class="stat-card">
              <div class="value">${status.running_workflows}</div>
              <div class="label">Running Workflows</div>
            </div>
            <div class="stat-card">
              <div class="value">${status.active_sessions}</div>
              <div class="label">Active Sessions</div>
            </div>
          </div>
          <h3>Recent Runs</h3>
          <p class="muted">Switch to Runs tab for details.</p>
        `;
      } catch(e) { console.error(e); }
    },

    async loadWorkflows() {
      try {
        this.workflows = await apiGet('/workflows');
      } catch(e) { console.error(e); }
    },

    async loadRuns() {
      try {
        this.runs = await apiGet('/runs');
        let html = '<h2>Runs</h2><table><thead><tr><th>ID</th><th>Workflow</th><th>Status</th><th>Started</th><th>Actions</th></tr></thead><tbody>';
        for (const r of this.runs) {
          html += `<tr>
            <td><code>${r.id.slice(0,8)}</code></td>
            <td>${r.workflow_id.slice(0,8)}</td>
            <td><span class="badge ${r.status}">${r.status}</span></td>
            <td>${(r.started_at||'').slice(0,16)}</td>
            <td>
              <button onclick="document.querySelector('[x-data]').__x.$data.viewRun('${r.id}')">View</button>
            </td>
          </tr>`;
        }
        html += '</tbody></table>';
        this.runsHTML = html;
      } catch(e) { console.error(e); }
    },

    async loadCron() {
      try {
        this.cronJobs = await apiGet('/cron');
      } catch(e) { console.error(e); }
    },

    async createWorkflow() {
      try {
        const config = JSON.parse(this.$refs.wfConfig.value);
        const name = config.name || 'untitled';
        delete config.name;
        await apiPost('/workflows', { name, config });
        this.showCreateWorkflow = false;
        await this.loadWorkflows();
      } catch(e) { alert('Error: ' + e.message); }
    },

    async startRun(workflowId) {
      const inputs = {};
      const inp = prompt('Inputs (key=value, comma separated):');
      if (inp) {
        inp.split(',').forEach(p => { const [k,v] = p.split('='); if(k) inputs[k.trim()] = (v||'').trim(); });
      }
      try {
        const run = await apiPost('/runs', { workflow_id: workflowId, inputs });
      } catch(e) { alert('Error: ' + e.message); }
    },

    async viewRun(runId) {
      try {
        const run = await apiGet(`/runs/${runId}`);
        let html = `<h2>Run <code>${run.id}</code></h2>`;
        html += `<p>Status: <span class="badge ${run.status}">${run.status}</span></p>`;
        html += `<h3>Nodes</h3><table><thead><tr><th>Node</th><th>Type</th><th>Status</th><th>Retries</th></tr></thead><tbody>`;
        for (const n of run.nodes || []) {
          html += `<tr><td>${n.node_id}</td><td>${n.type}</td><td><span class="badge ${n.status}">${n.status}</span></td><td>${n.retry_count}</td></tr>`;
        }
        html += '</tbody></table>';
        this.runsHTML = html;
        this.page = 'runs';
      } catch(e) { alert('Error: ' + e.message); }
    },

    async toggleCron(id) {
      try {
        await apiPost(`/cron/${id}/toggle`);
        await this.loadCron();
      } catch(e) { console.error(e); }
    },

    async deleteCron(id) {
      try {
        await apiDelete(`/cron/${id}`);
        await this.loadCron();
      } catch(e) { console.error(e); }
    },

    // Watch page changes to load data
    async setPage(p) {
      this.page = p;
      if (p === 'workflows') await this.loadWorkflows();
      if (p === 'runs') await this.loadRuns();
      if (p === 'cron') await this.loadCron();
    }
  };
}
```

- [ ] **Step 5: Verify web UI is served**

Run: `echo "Web UI files created"`

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add Web UI (Alpine.js + HTMX)"
```

---

### Task 19: Notification Channels (Interface Only)

**Files:**
- Create: `src/wflow/adapters/notifiers/__init__.py`
- Create: `src/wflow/adapters/notifiers/base.py`
- Create: `src/wflow/adapters/notifiers/registry.py`
- Create: `src/wflow/adapters/notifiers/welink.py`
- Create: `tests/test_adapters/test_notifiers.py`

- [ ] **Step 1: Write base interface**

Create `src/wflow/adapters/notifiers/base.py`:

```python
"""Abstract notification channel interface."""

from abc import ABC, abstractmethod
from typing import Any


class NotificationChannel(ABC):
    """Abstract interface for notification channels.

    Each channel (WeLink, Slack, email, etc.) implements this interface.
    The channel is called by the engine after workflow completion or failure.
    """

    @abstractmethod
    async def send(self, message: str, config: dict[str, Any]) -> bool:
        """Send a notification message.

        Args:
            message: Rendered message text (template variables already resolved).
            config: Channel-specific configuration from workflow JSON.

        Returns:
            True if sent successfully, False otherwise.
            Non-critical failures should return False, not raise.
        """
        ...
```

- [ ] **Step 2: Write registry**

Create `src/wflow/adapters/notifiers/registry.py`:

```python
"""Notifier registry — manages notification channel instances."""

from typing import Any

from wflow.adapters.notifiers.base import NotificationChannel


class NotifierRegistry:
    """Registry of named notification channels.

    Usage:
        registry = NotifierRegistry()
        registry.register("welink", WeLinkNotifier())
        await registry.send("welink", "Task done!", {"webhook_url": "..."})
    """

    def __init__(self):
        self._channels: dict[str, NotificationChannel] = {}

    def register(self, name: str, channel: NotificationChannel) -> None:
        """Register a channel under a name (e.g., 'welink', 'slack')."""
        self._channels[name] = channel

    async def send(self, channel: str, message: str, config: dict[str, Any]) -> bool:
        """Send a message through the named channel.

        Returns True if sent, False if channel not found or send failed.
        """
        ch = self._channels.get(channel)
        if ch is None:
            return False
        try:
            return await ch.send(message, config)
        except Exception:
            return False

    def list_channels(self) -> list[str]:
        """Return names of all registered channels."""
        return list(self._channels.keys())
```

- [ ] **Step 3: Write WeLink stub (interface only, not implemented)**

Create `src/wflow/adapters/notifiers/welink.py`:

```python
"""WeLink notification channel — interface reserved, not yet implemented.

WeLink communicates via its own CLI tool. When implemented, this adapter
will call the `welink` CLI as a subprocess.

Config keys:
    webhook_url: str (required) — WeLink API endpoint
"""

from typing import Any

from wflow.adapters.notifiers.base import NotificationChannel


class WeLinkNotifier(NotificationChannel):
    """Sends notifications via WeLink CLI tool.

    NOT IMPLEMENTED in v1. This is an interface stub.
    """

    async def send(self, message: str, config: dict[str, Any]) -> bool:
        """Send a message to WeLink.

        Implementation sketch (for future):
            import asyncio
            url = config["webhook_url"]
            proc = await asyncio.create_subprocess_exec(
                "welink", "send", "--url", url, "--message", message,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
        """
        raise NotImplementedError(
            "WeLink notifier is not yet implemented. "
            "This is a reserved interface for future use."
        )
```

- [ ] **Step 4: Write __init__.py**

Create `src/wflow/adapters/notifiers/__init__.py`:

```python
"""Notification channel adapters.

Channels:
    welink — WeLink CLI integration (interface reserved, not yet implemented)

Future channels (Slack, DingTalk, email) should follow the same pattern:
    1. Implement NotificationChannel ABC
    2. Register with NotifierRegistry by name
"""

from wflow.adapters.notifiers.base import NotificationChannel
from wflow.adapters.notifiers.registry import NotifierRegistry
from wflow.adapters.notifiers.welink import WeLinkNotifier

__all__ = ["NotificationChannel", "NotifierRegistry", "WeLinkNotifier"]
```

- [ ] **Step 5: Write test for notifier registry**

Create `tests/test_adapters/test_notifiers.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from wflow.adapters.notifiers.base import NotificationChannel
from wflow.adapters.notifiers.registry import NotifierRegistry
from wflow.adapters.notifiers.welink import WeLinkNotifier


class MockChannel(NotificationChannel):
    def __init__(self):
        self.messages = []

    async def send(self, message: str, config: dict) -> bool:
        self.messages.append((message, config))
        return True


class FailingChannel(NotificationChannel):
    async def send(self, message: str, config: dict) -> bool:
        return False


@pytest.mark.asyncio
async def test_registry_send_to_registered_channel():
    registry = NotifierRegistry()
    mock = MockChannel()
    registry.register("test", mock)

    result = await registry.send("test", "hello", {"key": "value"})

    assert result is True
    assert mock.messages == [("hello", {"key": "value"})]


@pytest.mark.asyncio
async def test_registry_send_to_unknown_channel():
    registry = NotifierRegistry()

    result = await registry.send("nonexistent", "hello", {})

    assert result is False


@pytest.mark.asyncio
async def test_registry_list_channels():
    registry = NotifierRegistry()
    registry.register("a", MockChannel())
    registry.register("b", MockChannel())

    channels = registry.list_channels()

    assert set(channels) == {"a", "b"}


@pytest.mark.asyncio
async def test_welink_not_implemented():
    welink = WeLinkNotifier()

    with pytest.raises(NotImplementedError):
        await welink.send("test", {"webhook_url": "..."})


def test_notification_channel_is_abstract():
    with pytest.raises(TypeError):
        NotificationChannel()  # Cannot instantiate ABC
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_adapters/test_notifiers.py -v`
Expected: 5 tests PASS

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: add notification channel interfaces (WeLink stub)"
```

---

### Task 20: Example Workflows + Final Integration

**Files:**
- Create: `examples/code-review.json`
- Create: `examples/simple-script.json`

- [ ] **Step 1: Write example workflows**

Create `examples/simple-script.json`:

```json
{
  "name": "simple-script-workflow",
  "version": "1.0",
  "description": "A minimal script-only workflow",
  "config": {
    "max_retries": 1,
    "retry_delay_seconds": 5,
    "timeout_seconds": 300
  },
  "nodes": [
    {
      "id": "start",
      "name": "Start",
      "type": "script",
      "script": {
        "module": "builtin.validators",
        "function": "echo",
        "args": {
          "message": "{{ inputs.message }}"
        }
      },
      "output": {
        "type": "object",
        "properties": {
          "echo": { "type": "object" }
        },
        "required": ["echo"]
      }
    }
  ],
  "edges": [
    {
      "id": "e1",
      "from": "start"
    }
  ],
  "inputs": {
    "message": {
      "type": "string",
      "default": "Hello, World!"
    }
  }
}
```

Create `examples/code-review.json`:

```json
{
  "name": "code-review-workflow",
  "version": "1.0",
  "description": "Automated coding with review loop",
  "config": {
    "max_retries": 3,
    "retry_delay_seconds": 30,
    "timeout_seconds": 1800
  },
  "nodes": [
    {
      "id": "coding",
      "name": "Code Implementation",
      "type": "agent",
      "prompt": "Write code for: {{ inputs.requirement }}. Review feedback: {{ nodes.review.output.feedback }}",
      "tools": {
        "allowed": ["Read", "Write", "Edit", "Bash", "Grep"],
        "disallowed": ["WebFetch"],
        "auto_approve": ["Read", "Grep"]
      },
      "model": "sonnet",
      "output": {
        "type": "object",
        "properties": {
          "files_changed": { "type": "array", "items": { "type": "string" } },
          "summary": { "type": "string" }
        },
        "required": ["files_changed", "summary"]
      },
      "retry": {
        "max_retries": 2,
        "on_error": ["timeout", "parse_error"]
      }
    },
    {
      "id": "review",
      "name": "Code Review",
      "type": "agent",
      "prompt": "Review this code: {{ nodes.coding.output.summary }}. Files: {{ nodes.coding.output.files_changed }}",
      "tools": {
        "allowed": ["Read", "Grep"],
        "disallowed": ["Write", "Edit", "Bash"],
        "auto_approve": ["Read", "Grep"]
      },
      "model": "sonnet",
      "output": {
        "type": "object",
        "properties": {
          "approved": { "type": "boolean" },
          "feedback": { "type": "string" }
        },
        "required": ["approved"]
      },
      "retry": {
        "max_retries": 1,
        "on_error": ["timeout"]
      }
    }
  ],
  "edges": [
    { "id": "e1", "from": "coding", "to": "review" },
    {
      "id": "e2",
      "from": "review",
      "to": "coding",
      "condition": "{{ nodes.review.output.approved }} == false"
    },
    {
      "id": "e3",
      "from": "review",
      "to": null,
      "condition": "{{ nodes.review.output.approved }} == true"
    }
  ],
  "inputs": {
    "requirement": { "type": "string", "required": true }
  }
}
```

- [ ] **Step 2: Run full test suite**

Run: `pytest tests/ -v`
Expected: all tests PASS

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "feat: add example workflows, final integration"
```

---

## Summary

**Total tasks:** 20
**Total files created:** ~40 files across `src/`, `tests/`, `examples/`

### Task Dependency Order

```
1. Project Setup
  └─ 2. DB Models
      └─ 3. Pydantic Schemas
          └─ 4. Template Resolver
              ├─ 5. Claude CLI Adapter
              ├─ 6. Script Runner
              ├─ 7. Session Manager
              │   └─ 8. State Machine
              │       └─ 9. Node Runner
              │           └─ 10. Executor
              │               ├─ 11. Scheduler
              │               ├─ 12. Services
              │               │   ├─ 13. API (app + status)
              │               │   ├─ 14. API (workflows)
              │               │   ├─ 15. API (runs)
              │               │   └─ 16. API (cron)
              │               └─ 17. CLI
              └─ 18. Web UI
                  └─ 19. Notification Channels (interfaces)
                      └─ 20. Examples + Final
```

### Verification at Each Stage

- After each task: run the specific test file to verify
- After API tasks: run `pytest tests/test_api/ -v` to verify all endpoints
- After final task: run `pytest tests/ -v` for full test coverage

