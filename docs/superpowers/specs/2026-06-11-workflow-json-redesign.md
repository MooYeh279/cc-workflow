# Workflow JSON 配置重构设计

**日期:** 2026-06-11  
**状态:** 设计中

## 1. 问题

当前 JSON 配置中，agent 节点的 `prompt` 字段混合了三种内容：
1. 角色/任务描述（"Use the Write/Edit/Bash tools..."）
2. 上游节点输出引用（`{{ nodes.review.output.feedback }}`）
3. 用户输入引用（`{{ inputs.requirement }}`）

这导致 prompt 臃肿、节点间耦合紧密，且新增节点时用户必须手动写出所有模板引用。

## 2. 目标

- `prompt` 字段只包含纯角色/任务描述，不再使用 `{{ }}` 模板
- 系统自动将上游节点输出、用户输入等上下文注入到 agent 调用中
- 边的 `condition` 保留 `{{ }}` 模板语法
- 每个 run 有唯一 run ID，每个 agent 节点有唯一 session ID（已有实现，维持不变）

## 3. 设计

### 3.1 prompt 语义变更

**之前：**
```json
"prompt": "Task: {{ inputs.requirement }}\n\nReview feedback: {{ nodes.review.output.feedback }}\n\nUse Write/Edit/Bash..."
```

**之后：**
```json
"prompt": "你是一个资深软件工程师。根据用户输入编写代码并写入文件。如果有审查反馈，请根据反馈意见修改代码。"
```

### 3.2 系统自动注入上下文

`NodeRunner._run_agent()` 不再对 prompt 调用 `resolve_template()`，改为：
1. 从 `context` 中提取上游节点输出和用户输入
2. 按固定格式拼接上下文
3. 传给 Claude CLI 的 prompt = context_header + schema_instruction + prompt

**注入格式（首节点，无上游输出）：**
```
## 角色任务
<prompt原文>

## 用户输入
{"requirement": "编写一个快排算法"}

---
CRITICAL OUTPUT FORMAT...
```

**注入格式（后续节点，有上游输出）：**
```
## 角色任务
<prompt原文>

## 上游节点输出
{"files_changed": ["src/sort.py"], "summary": "实现了快排"}

---
CRITICAL OUTPUT FORMAT...
```

**回路重入 / resume：** 格式与"后续节点"相同。上游节点（review）的输出自然包含反馈内容（`{"approved": false, "feedback": "修改第42行"}`），无需特殊处理。

### 3.3 模板语法保留范围

- ✅ **边的 `condition`**：`"{{ nodes.review.output.approved }} == false"` — 保留
- ✅ **其他需要动态引用的字段**：保留扩展能力
- ❌ **`prompt` 字段**：不再使用，系统自动注入上下文

### 3.4 JSON 示例

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
      "prompt": "你是一个资深软件工程师。根据用户输入编写代码，将代码写入文件。如果有审查反馈，根据反馈意见修改代码。使用 Write/Edit/Bash 工具完成所有工具操作后，输出 JSON 结果。",
      "tools": {
        "allowed": ["Read", "Write", "Edit", "Bash", "Grep"],
        "disallowed": ["WebFetch"]
      },
      "model": "deepseek-v4-pro",
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
      "prompt": "你是一个代码审查专家。仔细阅读上游节点产出的代码，检查 bug、风格问题和正确性，给出审查意见。使用 Read/Grep 工具审查代码后，输出 JSON 结果。",
      "tools": {
        "allowed": ["Read", "Grep"],
        "disallowed": ["Write", "Edit", "Bash"]
      },
      "model": "deepseek-v4-pro",
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
    { "id": "e2", "from": "review", "to": "coding", "condition": "{{ nodes.review.output.approved }} == false" },
    { "id": "e3", "from": "review", "to": null, "condition": "{{ nodes.review.output.approved }} == true" }
  ],
  "inputs": {
    "requirement": { "type": "string", "required": true }
  }
}
```

## 4. 代码改动

### 4.1 `src/wflow/engine/node_runner.py`

- `_run_agent()`: 移除 `resolve_template(prompt, context)` 调用
- 新增 `_build_agent_prompt()`: 根据节点是否是首节点，拼接 `## 角色任务` + `## 用户输入` 或 `## 上游节点输出`
- `_append_schema_instruction()`: 保持不变，继续附在末尾

```python
async def _run_agent(self, node, context, session_id, is_resume):
    prompt = self._build_agent_prompt(node, context)
    prompt = self._append_schema_instruction(prompt, node["output"])
    # ... rest unchanged

@staticmethod
def _build_agent_prompt(node, context):
    parts = []
    parts.append(f"## 角色任务\n{node['prompt']}")
    
    # 获取上一个已完成节点的输出
    upstream_output = _get_upstream_output(context, node["id"])
    
    if upstream_output:
        parts.append(f"\n## 上游节点输出\n{json.dumps(upstream_output, ensure_ascii=False)}")
    elif context.get("inputs"):
        parts.append(f"\n## 用户输入\n{json.dumps(context['inputs'], ensure_ascii=False)}")
    
    return "\n\n".join(parts)
```

### 4.2 `src/wflow/engine/executor.py`

- `execute()` 中 context 传递不变（`context["nodes"]` 已包含之前节点的输出）
- 可选：新增 `_get_upstream_output()` 辅助函数，从 context 中找到当前节点所依赖的前驱节点输出

## 5. 边界情况

| 场景 | 行为 |
|------|------|
| 首节点，无上游，有用户输入 | 注入 `## 角色任务` + `## 用户输入` |
| 中间节点，首次执行 | 注入 `## 角色任务` + `## 上游节点输出` |
| 回路重入（如 coding 收到 review 反馈） | 同"中间节点"，上游输出即为 review 的输出 |
| 节点有多个前驱 | 注入所有前驱的输出，分别标注 |
| resume 已有 session | 注入 `## 上游节点输出`（有提示词补充说明） |

## 6. 向后兼容

- 旧的 `prompt` 中 `{{ }}` 模板不再生效（如仍使用旧 JSON，prompt 中模板文字不会被解析，会原样发送给 Claude）
- 边的 `condition` 保持不变
- 数据库 schema 不变
- API 接口不变

## 7. 不做的

- 不增加 `role`/`on_first`/`on_feedback` 等多字段设计（保持方案 A 的简洁性）
- 不改变 `tools`、`output`、`retry` 等字段的语义
- 不改变 session 管理和 run ID 分配逻辑（已有实现正确）
