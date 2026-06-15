"""Workflow JSON generation from natural language descriptions.

Uses Claude Code CLI or OpenCode CLI as the backend to generate a complete
workflow JSON file from a user's text description.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import click

from wflow.adapters.claude_cli import ClaudeCLI, ClaudeCLIError, ClaudeCLITimeout
from wflow.adapters.opencode_cli import OpenCodeCLI, OpenCodeError, OpenCodeTimeout
from wflow.models.workflow import WorkflowSpec

# ══════════════════════════════════════════════════════════════════════════════
# Prompt template — teaches the LLM how to author a valid workflow JSON
# ══════════════════════════════════════════════════════════════════════════════

_WORKFLOW_FORMAT_REFERENCE = """
## 工作流 JSON 格式参考

一个完整的工作流 JSON 文件包含以下顶层字段：

```json
{
  "name": "kebab-case 工作流名称",
  "version": "1.0",
  "description": "工作流的一句话描述",
  "config": {
    "max_retries": 3,
    "retry_delay_seconds": 30,
    "timeout_seconds": 1800
  },
  "nodes": [ ... ],
  "edges": [ ... ],
  "inputs": { ... }
}
```

### 节点 (nodes)

每个节点是一个 JSON 对象，支持以下 type：

| type | 说明 | 必填字段 | 可选字段 |
|------|------|---------|---------|
| `claude` | Claude Code CLI 调用 | id, name, type, prompt, output | tools, model, retry |
| `opencode` | OpenCode CLI 调用 | id, name, type, prompt, output | model, retry |
| `script` | 子进程脚本 | id, name, type, command, output | timeout_seconds, retry |
| `human_review` | 人工审核节点 | id, name, type, prompt | output |

节点字段说明：
- **id**: 唯一标识符，snake_case，如 "coding"、"review_design"
- **name**: 人类可读的名称，如 "代码实现"、"方案审查"
- **type**: 节点类型，claude / opencode / script / human_review
- **prompt**: 节点的角色任务描述（仅 claude / opencode / human_review）
- **command**: 要执行的 shell 命令（仅 script 类型）
- **tools**: 工具配置，含 allowed 和 disallowed 两个数组（仅 claude 类型）
  ```json
  "tools": {
    "allowed": ["Read", "Write", "Edit", "Bash", "Grep"],
    "disallowed": ["WebFetch"]
  }
  ```
- **model**: 模型名称（可选，claude 和 opencode 类型），如 "sonnet"、"deepseek-v4-pro"
- **output**: JSON Schema 定义节点的输出格式，每个节点必须有 output 字段
  ```json
  "output": {
    "type": "object",
    "properties": {
      "result": { "type": "string" },
      "score": { "type": "number" }
    },
    "required": ["result"]
  }
  ```
- **retry**: 重试配置
  ```json
  "retry": {
    "max_retries": 2,
    "retry_delay_seconds": 30,
    "timeout_seconds": 300,
    "on_error": ["timeout", "parse_error"]
  }
  ```
- **timeout_seconds**: 超时秒数（script 类型）

### 边 (edges)

边定义节点之间的数据流和控制流：

```json
{ "id": "e1", "from": "node_a", "to": "node_b" }
{ "id": "e2", "from": "node_b", "to": "node_a", "condition": "{{ nodes.node_b.output.approved }} == false" }
{ "id": "e3", "from": "node_b", "to": null, "condition": "{{ nodes.node_b.output.approved }} == true" }
```

- **id**: 边的唯一标识符
- **from**: 源节点 ID
- **to**: 目标节点 ID（null 表示流程结束）
- **condition**: 条件表达式（可选），使用 `{{ nodes.<node_id>.output.<field> }}` 引用上游节点输出

条件表达式支持的操作符：==、!=、>、<、>=、<=

### 输入 (inputs)

定义工作流的用户输入参数：

```json
"inputs": {
  "requirement": {
    "type": "string",
    "required": true,
    "description": "需求描述"
  },
  "language": {
    "type": "string",
    "required": false,
    "default": "python",
    "description": "编程语言"
  }
}
```

### 回路 (Loop-back) 模式

通过条件边实现反馈回路，例如 coding → review → coding：

1. review 节点输出 `{"approved": false, "feedback": "需要修改..."}`
2. 条件边 `review → coding (approved == false)` 触发重做
3. coding 节点用 `feedback` 修改代码
4. review 重新审查
5. 条件边 `review → null (approved == true)` 结束流程

### 多起点 (Multi-start) 模式

工作流可以有多个起始节点（没有入边的节点），它们会并行执行：

```
gen_name ──┐
           ├── merge_info → coding → review
gen_features┘
```

### 人工审核 (human_review) 模式

```json
{ "id": "review", "name": "人工审核", "type": "human_review",
  "prompt": "请审核上游节点的输出是否符合要求" }
```

human_review 节点会暂停工作流，等待用户通过 API 提交审核决定。

## 设计原则

1. 节点 ID 用 snake_case，简洁有意义
2. 每个节点的 output schema 必须反映该节点的实际产出
3. 回路模式用于需要迭代改进的场景（code→review→code）
4. 多起点用于可以并行处理的独立任务
5. 条件边必须成对出现：true 分支和 false 分支
6. 选择合适类型的节点完成任务
7. 避免过度设计——节点数量控制在 10 个以内
8. human_review 节点放在关键决策点，不要滥用
"""

_BUILD_PROMPT_TEMPLATE = """你是一个工作流编排专家。请根据用户的描述，设计一个完整的工作流 JSON。

{format_reference}

## 重要约束

1. 你必须根据用户描述选择最合适的工作流模式（简单链式 / 回路 / 多起点 / 人工审核）
2. 每个节点必须使用正确的 type（claude / opencode / script / human_review）
3. 每个节点的 output 必须是一个有效的 JSON Schema，定义该节点的输出结构
4. 边必须形成合理的 DAG（回路依赖通过条件边实现）
5. 如果用户提到"审查"、"review"、"检查"，考虑使用回路模式
6. 如果用户提到"审核"、"批准"、"人工确认"，考虑使用 human_review 节点
7. 如果多个任务可以并行，使用多起点模式
8. 节点 ID 必须唯一，使用 snake_case
9. 条件边必须成对出现（true 路径和 false 路径），to=null 表示流程结束
10. 根据任务复杂度合理设置 timeout_seconds
11. 为 coding/implementation 类节点配置合适的 tools allowed 列表
12. 为纯分析/审查类节点限制 tools 为只读（Read/Grep）

## 输出要求

只输出完整的工作流 JSON 对象，不要有任何解释、注释、markdown 标记。输出必须以 `{{` 开头，以 `}}` 结尾。

## 用户需求

{description}

## 请输出工作流 JSON"""


def build_generation_prompt(description: str) -> str:
    """Build the meta-prompt that instructs the LLM to generate a workflow JSON."""
    return _BUILD_PROMPT_TEMPLATE.format(
        format_reference=_WORKFLOW_FORMAT_REFERENCE,
        description=description,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════════════


def validate_workflow_json(data: dict[str, Any]) -> list[str]:
    """Validate a generated workflow JSON against the schema.

    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []

    # Use Pydantic for structural validation
    try:
        WorkflowSpec(**data)
    except Exception as e:
        errors.append(f"Schema validation failed: {e}")

    # Extra semantic checks
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])

    # Node IDs must be unique
    node_ids = [n.get("id", "") for n in nodes]
    if len(node_ids) != len(set(node_ids)):
        errors.append("Duplicate node IDs found")

    # Edges must reference valid nodes
    valid_ids = set(node_ids)
    for edge in edges:
        frm = edge.get("from", "")
        to = edge.get("to")
        if frm not in valid_ids:
            errors.append(f"Edge '{edge.get('id', '?')}' references unknown 'from' node: {frm}")
        if to is not None and to not in valid_ids:
            errors.append(f"Edge '{edge.get('id', '?')}' references unknown 'to' node: {to}")

    # Node types must be valid
    valid_types = {"claude", "opencode", "script", "human_review"}
    for node in nodes:
        ntype = node.get("type", "")
        if ntype not in valid_types:
            errors.append(f"Node '{node.get('id', '?')}' has invalid type: {ntype}")

    # Each node must have an output schema
    for node in nodes:
        if "output" not in node:
            errors.append(f"Node '{node.get('id', '?')}' missing 'output' field")

    return errors


# ══════════════════════════════════════════════════════════════════════════════
# Console logger — prints adapter streaming output to stderr
# ══════════════════════════════════════════════════════════════════════════════

_CONSOLE_LOGGER: logging.Logger | None = None


def _get_console_logger() -> logging.Logger:
    """Create (or reuse) a logger that writes adapter stream events to stderr.

    Format is minimal — the adapters already prefix messages with
    ``[node_id]``, so we only emit the raw message.
    """
    global _CONSOLE_LOGGER
    if _CONSOLE_LOGGER is not None:
        return _CONSOLE_LOGGER

    logger = logging.getLogger("wflow.generate")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)

    _CONSOLE_LOGGER = logger
    return logger


# ══════════════════════════════════════════════════════════════════════════════
# Generation
# ══════════════════════════════════════════════════════════════════════════════


async def _generate_with_claude(
    prompt: str, model: str | None, timeout: int, cwd: str, logger: logging.Logger,
) -> dict[str, Any]:
    """Generate workflow JSON using Claude Code CLI."""
    adapter = ClaudeCLI()
    return await adapter.run(
        prompt=prompt,
        node_id="generate",
        session_id=None,
        is_resume=False,
        tools_allowed=[],          # no tools needed — just generate JSON
        tools_disallowed=None,
        model=model,
        timeout_seconds=timeout,
        cwd=cwd,
        logger=logger,
    )


async def _generate_with_opencode(
    prompt: str, model: str | None, timeout: int, cwd: str, logger: logging.Logger,
) -> dict[str, Any]:
    """Generate workflow JSON using OpenCode CLI."""
    adapter = OpenCodeCLI()
    return await adapter.run(
        prompt=prompt,
        node_id="generate",
        session_id=None,
        is_resume=False,
        model=model,
        timeout_seconds=timeout,
        cwd=cwd,
        logger=logger,
    )


async def generate_workflow_json(
    description: str,
    backend: str,
    model: str | None,
    timeout: int,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Generate a workflow JSON from a natural language description.

    Args:
        description: User's workflow description.
        backend: ``"claude"`` or ``"opencode"``.
        model: Optional model override.
        timeout: Timeout in seconds.
        cwd: Working directory (defaults to current directory).

    Returns:
        The generated workflow dict (without ``_session_id``).

    Raises:
        click.ClickException: On generation failure.
    """
    cwd = cwd or os.getcwd()
    prompt = build_generation_prompt(description)
    logger = _get_console_logger()

    try:
        if backend == "claude":
            result = await _generate_with_claude(prompt, model, timeout, cwd, logger)
        else:
            result = await _generate_with_opencode(prompt, model, timeout, cwd, logger)
    except (ClaudeCLITimeout, OpenCodeTimeout):
        raise click.ClickException(
            f"Generation timed out after {timeout}s. "
            f"Try a simpler description or increase --timeout."
        )
    except (ClaudeCLIError, OpenCodeError) as e:
        raise click.ClickException(f"Generation failed: {e}")

    # Strip adapter-injected metadata
    result.pop("_session_id", None)
    return result


# ══════════════════════════════════════════════════════════════════════════════
# CLI command
# ══════════════════════════════════════════════════════════════════════════════


@click.command("generate")
@click.argument("description")
@click.option(
    "--backend", "-b",
    type=click.Choice(["claude", "opencode"]),
    default="claude",
    help="Backend AI to use for generation (default: claude)",
)
@click.option(
    "--model", "-m",
    default=None,
    help="Model name override (e.g. sonnet, deepseek-v4-pro)",
)
@click.option(
    "--output", "-o",
    default=None,
    help="Output file path (default: <workflow-name>.json in current directory)",
)
@click.option(
    "--timeout", "-t",
    default=600,
    type=int,
    help="Timeout in seconds (default: 600)",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Validate and print JSON without writing to file",
)
@click.option(
    "--force", "-f", is_flag=True,
    help="Overwrite output file if it already exists",
)
def generate(
    description: str,
    backend: str,
    model: str | None,
    output: str | None,
    timeout: int,
    dry_run: bool,
    force: bool,
) -> None:
    """Generate a workflow JSON from a natural language description.

    Examples:
      wflow generate "代码审查工作流：先写代码，再审查，不通过则修改"
      wflow generate "多源研究合并" --backend opencode -m deepseek-v4-pro
      wflow generate "需求分析 → 设计 → 人工审核" -o my-workflow.json
      wflow generate "自动化测试流水线" --dry-run
    """
    cwd = os.getcwd()

    click.echo(f"🤖 Generating workflow with {backend} backend...")
    click.echo(f"   Description: {description[:80]}{'...' if len(description) > 80 else ''}")

    try:
        result = asyncio.run(
            generate_workflow_json(description, backend, model, timeout, cwd)
        )
    except click.ClickException:
        raise
    except Exception as e:
        raise click.ClickException(f"Unexpected error: {e}")

    # Validate
    errors = validate_workflow_json(result)
    if errors:
        click.echo("\n⚠️  Validation warnings:", err=True)
        for err in errors:
            click.echo(f"   - {err}", err=True)
        if not force:
            click.echo("\nUse --force to save despite validation errors.", err=True)
            raise SystemExit(1)

    # Determine output path
    if output:
        output_path = Path(output)
    else:
        name = result.get("name", "generated-workflow")
        output_path = Path(cwd) / f"{name}.json"

    if not output_path.is_absolute():
        output_path = Path(cwd) / output_path

    # Check for overwrite
    if output_path.exists() and not force and not dry_run:
        if not click.confirm(f"File '{output_path}' already exists. Overwrite?"):
            click.echo("Aborted.")
            raise SystemExit(0)

    if dry_run:
        click.echo("\n📋 Generated workflow JSON (dry-run):")
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
        return

    # Write file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    click.echo(f"\n✅ Workflow JSON written to: {output_path}")
    click.echo(f"   Name: {result.get('name', '?')}")
    click.echo(f"   Nodes: {len(result.get('nodes', []))}")
    click.echo(f"   Edges: {len(result.get('edges', []))}")
    if result.get("inputs"):
        click.echo(f"   Inputs: {', '.join(result['inputs'].keys())}")
    click.echo(f"\n   Deploy with: wflow workflow create {output_path}")
