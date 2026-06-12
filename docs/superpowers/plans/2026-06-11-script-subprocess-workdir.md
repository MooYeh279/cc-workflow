# Script Subprocess + Working Directory Isolation 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** Script 节点改为 subprocess 调用（stdin/stdout），每个 run 创建隔离的工作目录。

**Architecture:** `ScriptRunner` 从进程内函数注册表改为 `asyncio.create_subprocess_exec` 执行外部命令，stdin 传入上下文 JSON，stdout 捕获解析。`WorkflowExecutor` 在启动时创建 `<workflow_name>-<run_id[:8]>` 工作目录，Claude CLI 和 Script subprocess 均在其下运行。

**Tech Stack:** Python 3.11+, asyncio, pytest

---

### Task 1: 重写 ScriptRunner 为 subprocess 模式

**Files:**
- Modify: `src/wflow/adapters/script_runner.py`

- [ ] **Step 1: 完全重写 `script_runner.py`**

将整个文件替换为：

```python
"""Script node runner — executes external commands via subprocess with stdin/stdout."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any


class ScriptError(Exception):
    """Raised when a script node fails."""


class ScriptRunner:
    """Executes external scripts/commands as subprocess.

    Context (inputs + upstream output) is serialized as JSON and piped
    to stdin. The script MUST write a JSON object to stdout as its result.
    """

    async def run(
        self,
        command: str,
        context: dict[str, Any],
        timeout_seconds: int = 300,
        cwd: str | None = None,
        logger: logging.Logger | None = None,
    ) -> dict[str, Any]:
        """Execute a command, pass context as stdin JSON, parse stdout JSON.

        Args:
            command: The command to execute (e.g. "python ./scripts/validate.py")
            context: Dict with "inputs" and "upstream" keys to pass via stdin
            timeout_seconds: Max execution time
            cwd: Working directory for the subprocess
            logger: Optional logger

        Returns:
            Parsed JSON output from stdout

        Raises:
            ScriptError: On non-zero exit, timeout, or invalid JSON output
        """
        stdin_data = json.dumps(context, ensure_ascii=False)

        if logger:
            logger.info(f"[script] CMD: {command}")
            logger.info(f"[script] INPUT ({len(stdin_data)} chars): {stdin_data[:200]}")

        try:
            proc = await asyncio.create_subprocess_exec(
                *command.split(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data.encode("utf-8")),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise ScriptError(f"Script timed out after {timeout_seconds}s: {command}")
        except FileNotFoundError:
            raise ScriptError(f"Command not found: {command.split()[0]}")
        except Exception as e:
            raise ScriptError(f"Script execution failed: {e}")

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if logger:
            logger.info(f"[script] STDOUT ({len(stdout)} chars): {stdout[:300]}")
            if stderr:
                logger.info(f"[script] STDERR: {stderr[:300]}")

        if proc.returncode != 0:
            raise ScriptError(
                f"Script exited with code {proc.returncode}: {command}\n"
                f"stderr: {stderr[:500]}"
            )

        try:
            result = json.loads(stdout)
        except json.JSONDecodeError:
            raise ScriptError(
                f"Script stdout is not valid JSON: {stdout[:500]}"
            )

        if not isinstance(result, dict):
            raise ScriptError(
                f"Script must output a JSON object, got {type(result).__name__}"
            )

        return result
```

- [ ] **Step 2: 验证语法**

```bash
python -c "import ast; ast.parse(open('src/wflow/adapters/script_runner.py').read()); print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/wflow/adapters/script_runner.py
git commit -m "feat(script_runner): rewrite as subprocess executor with stdin/stdout"
```

---

### Task 2: Claude CLI adapter — 添加 cwd 参数

**Files:**
- Modify: `src/wflow/adapters/claude_cli.py`

- [ ] **Step 1: `run()` 添加 `cwd` 参数**

在 `run()` 方法签名中添加 `cwd: str | None = None`：

```python
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
    cwd: str | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
```

- [ ] **Step 2: 将 `cwd` 传递给 `create_subprocess_exec`**

在 `create_subprocess_exec` 调用处添加 `cwd=cwd`：

```python
proc = await asyncio.create_subprocess_exec(
    *cmd,
    stdin=asyncio.subprocess.PIPE,
    stdout=asyncio.subprocess.PIPE,
    stderr=asyncio.subprocess.PIPE,
    env=merged_env,
    cwd=cwd,
)
```

- [ ] **Step 3: 验证语法并 Commit**

```bash
git add src/wflow/adapters/claude_cli.py
git commit -m "feat(claude_cli): add cwd parameter for working directory isolation"
```

---

### Task 3: 更新 node_runner.py — 传递 cwd 并适配新 ScriptRunner

**Files:**
- Modify: `src/wflow/engine/node_runner.py`

- [ ] **Step 1: `run()` 添加 `cwd` 参数**

```python
async def run(
    self,
    node_config: dict[str, Any],
    context: TemplateContext,
    session_id: str | None,
    is_resume: bool = False,
    upstream_output: dict[str, Any] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Execute a node and return its output dict."""
    node_type = node_config["type"]

    if node_type == "agent":
        return await self._run_agent(node_config, context, session_id, is_resume, upstream_output, cwd)
    elif node_type == "script":
        return await self._run_script(node_config, context, upstream_output, cwd)
    else:
        raise ValueError(f"Unknown node type: {node_type}")
```

- [ ] **Step 2: `_run_agent()` 添加 `cwd` 参数并传透**

```python
async def _run_agent(
    self,
    node: dict[str, Any],
    context: TemplateContext,
    session_id: str | None,
    is_resume: bool,
    upstream_output: dict[str, Any] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    prompt = self._build_agent_prompt(node, context, upstream_output)
    tools = node.get("tools", {})

    prompt = self._append_schema_instruction(prompt, node["output"])

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
        timeout_seconds=node.get("retry", {}).get("timeout_seconds", 1800),
        cwd=cwd,
        logger=self._logger,
    )
```

- [ ] **Step 3: 重写 `_run_script()` 为 subprocess 模式**

```python
async def _run_script(
    self,
    node: dict[str, Any],
    context: TemplateContext,
    upstream_output: dict[str, Any] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    command = node["command"]
    timeout = node.get("timeout_seconds", 300)

    # Build context for the script
    script_context = {
        "inputs": context.get("inputs", {}),
        "upstream": upstream_output or {},
        "nodes": {
            k: {"output": v.get("output", {}), "status": v.get("status")}
            for k, v in context.get("nodes", {}).items()
        },
        "run": context.get("run", {}),
        "config": context.get("config", {}),
    }

    return await self._script.run(
        command=command,
        context=script_context,
        timeout_seconds=timeout,
        cwd=cwd,
        logger=self._logger,
    )
```

- [ ] **Step 4: 移除不再需要的 `resolve_template` import**

`_run_script` 不再使用模板，但 `executor.py` 的 `_evaluate_condition` 和 `_find_next_node` 仍使用 `resolve_template`。检查 node_runner.py 中是否还有对 `resolve_template` 的引用。如果没有（现在只被 executor.py 用），可以移除这个 import。

- [ ] **Step 5: Commit**

```bash
git add src/wflow/engine/node_runner.py
git commit -m "feat(node_runner): pass cwd through to agents and scripts; use new subprocess script runner"
```

---

### Task 4: 更新 executor.py — 创建并管理工作目录

**Files:**
- Modify: `src/wflow/engine/executor.py`

- [ ] **Step 1: 添加 `os` import 和工作目录配置**

```python
import os

_RUNS_DIR = os.environ.get("WFLOW_RUNS_DIR", "./data/runs")
```

- [ ] **Step 2: `execute()` 添加 `workflow_name` 参数**

在 `execute()` 签名中添加：

```python
async def execute(
    self,
    spec: WorkflowSpec,
    run_id: str,
    context: TemplateContext,
    workflow_name: str = "",
) -> bool:
```

- [ ] **Step 3: 在方法开始处创建/读取工作目录**

在 `execute()` 方法体顶部（在 `current_node_id` 之前）：

```python
# --- Working directory: create or reuse for resume ---
work_dir = context.get("run", {}).get("work_dir")
if not work_dir:
    safe_name = workflow_name.replace(" ", "-").replace("/", "-")
    work_dir = os.path.join(_RUNS_DIR, f"{safe_name}-{run_id[:8]}")
    os.makedirs(work_dir, exist_ok=True)
    if "run" not in context:
        context["run"] = {}
    context["run"]["work_dir"] = work_dir
    self._log(run_id, None, "info", f"Created work directory: {work_dir}")
else:
    self._log(run_id, None, "info", f"Using existing work directory: {work_dir}")
```

- [ ] **Step 4: 将 `cwd=work_dir` 传递给 node_runner**

在 `node_runner.run()` 调用处添加：

```python
output = await self._node_runner.run(
    node_config, context,
    session_id=session_id,
    is_resume=is_resume,
    upstream_output=upstream_output,
    cwd=work_dir,
)
```

- [ ] **Step 5: Commit**

```bash
git add src/wflow/engine/executor.py
git commit -m "feat(executor): create isolated work directory per run, pass cwd to nodes"
```

---

### Task 5: 更新 workflow 模型和 JSON 示例

**Files:**
- Modify: `src/wflow/models/workflow.py`
- Modify: `examples/simple-script.json`
- Modify: `examples/code-review.json` (if needed)

- [ ] **Step 1: 更新 `ScriptNode` 模型**

将：
```python
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
```

改为：
```python
class ScriptNode(BaseModel):
    id: str
    name: str
    type: str = "script"
    command: str
    timeout_seconds: int = 300
    output_schema: dict[str, Any] = Field(alias="output")
    retry: RetryConfig = Field(default_factory=RetryConfig)

    class Config:
        populate_by_name = True
        extra = "allow"
```

- [ ] **Step 2: 更新 `examples/simple-script.json`**

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
      "command": "python -c \"import json,sys; data=json.load(sys.stdin); print(json.dumps({'echo': data}))\"",
      "timeout_seconds": 30,
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
    { "id": "e1", "from": "start" }
  ],
  "inputs": {
    "message": {
      "type": "string",
      "default": "Hello, World!"
    }
  }
}
```

- [ ] **Step 3: 验证 JSON 有效**

```bash
python -c "import json; json.load(open('examples/simple-script.json','r',encoding='utf-8'))"
```

- [ ] **Step 4: Commit**

```bash
git add src/wflow/models/workflow.py examples/simple-script.json
git commit -m "feat(models): update ScriptNode to use command field; update examples"
```

---

### Task 6: 更新 API — 传递 workflow_name

**Files:**
- Modify: `src/wflow/api/runs.py`

- [ ] **Step 1: 在 `start_run` 中传递 `workflow_name`**

在 `start_run` 端点中，找到 `executor.execute(spec, run.id, context)` 调用，改为：

```python
success = await executor.execute(spec, run.id, context, workflow_name=workflow.name)
```

同样更新 `rerun_run` 中的调用。

- [ ] **Step 2: 将 `work_dir` 持久化到 run context**

在 executor 中 `context["run"]["work_dir"]` 已经设置。确保在 run 完成/失败后，将 context 写回 `WorkflowRun.context`：

在 `start_run` 的 `run_workflow()` 内部函数中，executor 返回后：

```python
success = await executor.execute(spec, run.id, context, workflow_name=workflow.name)
# executor 已经通过 context["run"]["work_dir"] 设置了 work_dir
# 更新 run 记录中的 context
bg_run.context = json.dumps(context, ensure_ascii=False)
```

- [ ] **Step 3: 同样更新 `rerun_run` 中的调用**

- [ ] **Step 4: Commit**

```bash
git add src/wflow/api/runs.py
git commit -m "feat(api): pass workflow_name to executor; persist work_dir in run context"
```

---

### Task 7: 更新测试

**Files:**
- Modify: `tests/test_adapters/test_claude_cli.py`
- Modify: `tests/test_engine/test_node_runner.py`
- Modify: `tests/test_engine/test_executor.py`
- Modify: `tests/test_models/test_workflow_schema.py`

- [ ] **Step 1: 重写 `test_claude_cli.py`** — 添加 cwd 测试；保持现有测试兼容

- [ ] **Step 2: 更新 `test_node_runner.py`** — 更新 `test_run_script_node`，改为使用 `command` 字段并验证 subprocess 调用

- [ ] **Step 3: 更新 `test_executor.py`** — 传递 `workflow_name` 参数；验证工作目录创建

- [ ] **Step 4: 更新 `test_workflow_schema.py`** — 适配 ScriptNode 模型变更

- [ ] **Step 5: 运行测试**

```bash
cd D:/workspace/ai-coding/workflow-orchestrator && python -m pytest tests/test_adapters/test_claude_cli.py tests/test_engine/test_node_runner.py tests/test_engine/test_executor.py tests/test_models/test_workflow_schema.py -v
```

- [ ] **Step 6: Commit**

```bash
git add tests/
git commit -m "test: update tests for script subprocess and work directory isolation"
```

---

### Task 8: 验证完整流程

- [ ] **Step 1: 运行全量测试**

```bash
cd D:/workspace/ai-coding/workflow-orchestrator && python -m pytest tests/ -v
```

- [ ] **Step 2: 验证工作目录创建**

```bash
python -c "
import os, tempfile
os.environ['WFLOW_RUNS_DIR'] = tempfile.mkdtemp()
print('Runs dir:', os.environ['WFLOW_RUNS_DIR'])
"
```

- [ ] **Step 3: Final commit if needed**
