# Workflow JSON 配置重构 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 agent 节点的 prompt 从模板混入上下文改为系统自动注入，prompt 字段只保留纯角色/任务描述。

**Architecture:** `NodeRunner._run_agent()` 不再对 prompt 调用 `resolve_template()`，改为调用新方法 `_build_agent_prompt()` 拼装 `## 角色任务` + `## 用户输入` 或 `## 上游节点输出`。`WorkflowExecutor` 负责计算上游节点输出并传递给 `NodeRunner.run()`。边的 `condition` 模板语法不变。

**Tech Stack:** Python 3.11+, pytest, asyncio, AsyncMock

---

### Task 1: 更新 node_runner.py — 新增 `_build_agent_prompt()` 并重构 `_run_agent()`

**Files:**
- Modify: `src/wflow/engine/node_runner.py`

- [ ] **Step 1: 添加 `_build_agent_prompt()` 静态方法**

在 `NodeRunner` 类中，`_run_agent` 方法之前，添加新静态方法：

```python
@staticmethod
def _build_agent_prompt(
    node: dict[str, Any],
    context: TemplateContext,
    upstream_output: dict[str, Any] | None,
) -> str:
    """Build the full agent prompt with auto-injected context.

    First node (upstream_output is None): injects user inputs.
    Subsequent nodes: injects upstream node output.
    """
    import json

    parts = [f"## 角色任务\n{node['prompt']}"]

    if upstream_output is not None and upstream_output:
        parts.append(
            f"\n## 上游节点输出\n{json.dumps(upstream_output, ensure_ascii=False)}"
        )
    elif context.get("inputs"):
        parts.append(
            f"\n## 用户输入\n{json.dumps(context['inputs'], ensure_ascii=False)}"
        )

    return "\n\n".join(parts)
```

- [ ] **Step 2: 修改 `_run_agent()` 使用 `_build_agent_prompt()` 替代 `resolve_template()`**

将：
```python
async def _run_agent(
    self,
    node: dict[str, Any],
    context: TemplateContext,
    session_id: str | None,
    is_resume: bool,
) -> dict[str, Any]:
    prompt = resolve_template(node["prompt"], context)
    tools = node.get("tools", {})

    prompt = self._append_schema_instruction(prompt, node["output"])
```

改为：
```python
async def _run_agent(
    self,
    node: dict[str, Any],
    context: TemplateContext,
    session_id: str | None,
    is_resume: bool,
    upstream_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prompt = self._build_agent_prompt(node, context, upstream_output)
    tools = node.get("tools", {})

    prompt = self._append_schema_instruction(prompt, node["output"])
```

- [ ] **Step 3: 修改 `run()` 方法签名，传递 `upstream_output`**

将：
```python
async def run(
    self,
    node_config: dict[str, Any],
    context: TemplateContext,
    session_id: str | None,
    is_resume: bool = False,
) -> dict[str, Any]:
    """Execute a node and return its output dict."""
    node_type = node_config["type"]

    if node_type == "agent":
        return await self._run_agent(node_config, context, session_id, is_resume)
```

改为：
```python
async def run(
    self,
    node_config: dict[str, Any],
    context: TemplateContext,
    session_id: str | None,
    is_resume: bool = False,
    upstream_output: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a node and return its output dict."""
    node_type = node_config["type"]

    if node_type == "agent":
        return await self._run_agent(node_config, context, session_id, is_resume, upstream_output)
```

- [ ] **Step 4: 移除不再需要的 import**

删除 `_run_agent` 中不再需要的 `resolve_template` 调用后，检查 `from wflow.engine.template import resolve_template, TemplateContext` 是否还被其他地方使用。`_run_script` 仍使用 `resolve_template`，所以 import 保持不变。

- [ ] **Step 5: 运行现有测试确认无破坏**

```bash
cd D:/workspace/ai-coding/workflow-orchestrator && python -m pytest tests/test_engine/test_node_runner.py -v
```

当前测试会因签名变更而失败（缺少 `upstream_output` 参数）——这是预期的，Task 3 将更新测试。

---

### Task 2: 更新 executor.py — 计算上游输出并传递

**Files:**
- Modify: `src/wflow/engine/executor.py`

- [ ] **Step 1: 新增 `_get_upstream_output()` 方法**

在 `WorkflowExecutor` 类中添加方法：

```python
def _get_upstream_output(
    self,
    spec: WorkflowSpec,
    node_id: str,
    context: TemplateContext,
) -> dict[str, Any] | None:
    """Get the output of predecessors that have edges TO node_id.

    Returns None if this is the first node (no incoming edges with completed output).
    For a single predecessor, returns that node's output dict directly.
    For multiple predecessors, returns a dict keyed by predecessor node_id.
    """
    incoming_edges = [e for e in spec.edges if e.get("to") == node_id]
    outputs: dict[str, Any] = {}
    for edge in incoming_edges:
        from_node = edge["from"]
        node_data = context.get("nodes", {}).get(from_node)
        if node_data and node_data.get("status") == "completed":
            outputs[from_node] = node_data.get("output", {})

    if not outputs:
        return None

    # Single predecessor: return its output directly
    if len(outputs) == 1:
        return next(iter(outputs.values()))

    # Multiple predecessors: return keyed by node_id
    return outputs
```

- [ ] **Step 2: 在 `execute()` 中调用 `_get_upstream_output()` 并传递给 node_runner**

在 `execute()` 方法中，找到 `output = await self._node_runner.run(...)` 调用处，修改为：

```python
upstream_output = self._get_upstream_output(spec, current_node_id, context)
output = await self._node_runner.run(
    node_config, context,
    session_id=session_id,
    is_resume=is_resume,
    upstream_output=upstream_output,
)
```

---

### Task 3: 更新测试

**Files:**
- Modify: `tests/test_engine/test_node_runner.py`
- Modify: `tests/test_engine/test_executor.py`

- [ ] **Step 1: 更新 `test_node_runner.py` — 添加 `_build_agent_prompt` 单元测试**

在文件末尾添加：

```python
def test_build_agent_prompt_first_node_with_inputs():
    node = {"id": "coding", "prompt": "你是一个工程师"}
    context = {"inputs": {"requirement": "写一个计算器"}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = NodeRunner._build_agent_prompt(node, context, upstream_output=None)

    assert "## 角色任务" in result
    assert "你是一个工程师" in result
    assert "## 用户输入" in result
    assert "写一个计算器" in result


def test_build_agent_prompt_subsequent_node_with_upstream():
    node = {"id": "review", "prompt": "审查代码"}
    context = {"inputs": {}, "nodes": {}, "run": {"id": "r1"}, "config": {}}
    upstream = {"files_changed": ["a.py"], "summary": "完成了"}

    result = NodeRunner._build_agent_prompt(node, context, upstream_output=upstream)

    assert "## 角色任务" in result
    assert "审查代码" in result
    assert "## 上游节点输出" in result
    assert "a.py" in result


def test_build_agent_prompt_no_upstream_no_inputs():
    node = {"id": "start", "prompt": "开始"}
    context = {"inputs": {}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = NodeRunner._build_agent_prompt(node, context, upstream_output=None)

    assert "## 角色任务" in result
    assert "开始" in result
    assert "## 用户输入" not in result


def test_build_agent_prompt_empty_upstream_uses_inputs():
    node = {"id": "start", "prompt": "开始"}
    context = {"inputs": {"task": "hello"}, "nodes": {}, "run": {"id": "r1"}, "config": {}}

    result = NodeRunner._build_agent_prompt(node, context, upstream_output={})

    assert "## 角色任务" in result
    assert "## 用户输入" in result
    assert "hello" in result
```

- [ ] **Step 2: 更新 `test_run_agent_node_create_mode` 传递 `upstream_output`**

```python
result = await node_runner.run(node_config, context, session_id=None, upstream_output={"task": "hello"})
```

同时验证 prompt 包含上下文注入：

```python
call_kwargs = claude_cli.run.call_args.kwargs
assert "## 角色任务" in call_kwargs["prompt"]
assert "## 上游节点输出" in call_kwargs["prompt"]
```

- [ ] **Step 3: 更新 `test_executor.py` — 添加 `_get_upstream_output` 测试**

在文件末尾添加：

```python
def test_get_upstream_output_first_node_returns_none():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[{"id": "start", "type": "agent", "prompt": "go",
                "tools": {"allowed": [], "disallowed": []},
                "output": {"type": "object", "properties": {}, "required": []}}],
        edges=[],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock())

    result = executor._get_upstream_output(spec, "start", make_context())
    assert result is None


def test_get_upstream_output_returns_predecessor_output():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "a", "type": "agent", "prompt": "a",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "agent", "prompt": "b",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "a", "to": "b"}],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock())
    ctx = make_context()
    ctx["nodes"]["a"] = {"output": {"x": 1}, "status": "completed"}

    result = executor._get_upstream_output(spec, "b", ctx)
    assert result == {"x": 1}


def test_get_upstream_output_skips_failed_predecessor():
    from wflow.models.workflow import WorkflowSpec
    spec = WorkflowSpec(
        name="test",
        nodes=[
            {"id": "a", "type": "agent", "prompt": "a",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
            {"id": "b", "type": "agent", "prompt": "b",
             "tools": {"allowed": [], "disallowed": []},
             "output": {"type": "object", "properties": {}, "required": []}},
        ],
        edges=[{"id": "e1", "from": "a", "to": "b"}],
    )
    executor = WorkflowExecutor(db=AsyncMock(), node_runner=MagicMock(), session_manager=MagicMock())
    ctx = make_context()
    ctx["nodes"]["a"] = {"output": {}, "status": "failed"}

    result = executor._get_upstream_output(spec, "b", ctx)
    assert result is None
```

- [ ] **Step 4: 运行所有测试**

```bash
cd D:/workspace/ai-coding/workflow-orchestrator && python -m pytest tests/test_engine/test_node_runner.py tests/test_engine/test_executor.py -v
```

预期全部 PASS。

---

### Task 4: 更新 examples/code-review.json

**Files:**
- Modify: `examples/code-review.json`

- [ ] **Step 1: 更新 coding 节点的 prompt**

将：
```json
"prompt": "Task: {{ inputs.requirement }}\n\nReview feedback from previous iteration (if any): {{ nodes.review.output.feedback }}\n\nUse the Write/Edit/Bash tools to implement the code. After completing all tool work, output the JSON result.",
```

改为：
```json
"prompt": "你是一个资深软件工程师。根据用户输入编写代码，将代码写入文件。如果有审查反馈，根据反馈意见修改代码。使用 Write/Edit/Bash 工具完成所有工具操作后，输出 JSON 结果。",
```

- [ ] **Step 2: 更新 review 节点的 prompt**

将：
```json
"prompt": "Review the code from the coding phase.\nSummary: {{ nodes.coding.output.summary }}\nFiles: {{ nodes.coding.output.files_changed }}\n\nRead the files, check for bugs, style issues, and correctness. Then output the JSON review result.",
```

改为：
```json
"prompt": "你是一个代码审查专家。仔细阅读上游节点产出的代码，检查 bug、风格问题和正确性，给出审查意见。使用 Read/Grep 工具审查代码后，输出 JSON 结果。",
```

- [ ] **Step 3: 验证 JSON 有效**

```bash
cd D:/workspace/ai-coding/workflow-orchestrator && python -c "import json; print(json.load(open('examples/code-review.json','r',encoding='utf-8')))"
```

---

### Task 5: 验证完整流程

**Files:** (无改动，仅验证)

- [ ] **Step 1: 运行全量测试**

```bash
cd D:/workspace/ai-coding/workflow-orchestrator && python -m pytest tests/ -v
```

- [ ] **Step 2: 确认无 `{{ }}` 残留于 prompt 字段**

```bash
cd D:/workspace/ai-coding/workflow-orchestrator && python -c "
import json
with open('examples/code-review.json','r',encoding='utf-8') as f:
    data = json.load(f)
for n in data['nodes']:
    if n['type'] == 'agent':
        assert '{{' not in n['prompt'], f\"Node {n['id']} prompt still has template: {n['prompt'][:80]}\"
        print(f\"OK: {n['id']} prompt is clean\")
"
```

- [ ] **Step 3: Commit**

```bash
git add src/wflow/engine/node_runner.py src/wflow/engine/executor.py examples/code-review.json tests/test_engine/test_node_runner.py tests/test_engine/test_executor.py
git commit -m "feat: auto-inject context into agent prompts instead of template mixin

- node_runner._build_agent_prompt() assembles ## 角色任务 + ## 用户输入 or ## 上游节点输出
- executor._get_upstream_output() computes predecessor output from DAG edges
- Prompt field in JSON is now pure role/task description
- Edge conditions retain {{ }} template syntax

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
