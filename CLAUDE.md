# 工作流编排器

工作流编排器，底层封装 Claude Code CLI / OpenCode CLI 工具完成复杂任务。

## 模块结构

### 1. CLI 用户界面 (`src/wflow/cli/`)
- 任务启动、状态查看、日志追踪
- Cron 管理
- 服务器启动管理
- 入口: `python -m wflow` (via `src/wflow/__main__.py`)

### 2. Web 用户界面 (`src/wflow/web/`)
- 侧边栏导航 (Dashboard / Workflows / Runs / Cron)
- DAG 拓扑图 (dagre 布局，支持缩放/拖拽、回路弧线、状态着色、条件边颜色区分)
- Workflow DAG 预览按钮 (单击查看工作流拓扑)
- Run 详情页：工作目录文件浏览器、节点执行详情(折叠展开)、日志查看
- 工作流输入 Schema 展示 + Run 表单弹窗
- Cron 创建：动态参数表单(选择 workflow 后自动加载输入 schema)、Cron 预设按钮

### 3. 工作流编排器 (`src/wflow/engine/`)

#### 节点类型
| 类型 | 说明 | 配置字段 |
|------|------|---------|
| `claude` | Claude Code CLI 调用 | `prompt`, `tools`, `model`, `output`, `retry` |
| `opencode` | OpenCode CLI 调用 | `prompt`, `model`, `output`, `retry` |
| `script` | 子进程脚本 (stdin JSON → stdout JSON) | `command`, `timeout_seconds`, `output` |
| `human_review` | 人工审核节点，暂停等审核后透传或驳回 | `prompt`, `output` |

> 注意: `agent` 不是合法的节点类型。过去曾作为 `claude` 的别名存在，现已移除。

#### NodeHandler 继承体系 (`src/wflow/engine/node_handler.py`)
```
NodeHandler (ABC)
├── _SessionNodeHandler → ClaudeHandler, OpenCodeHandler (共享 prompt 构建)
├── ScriptHandler
└── HumanReviewHandler
```
- 新增节点类型只需子类化 `NodeHandler` + 注册到 handlers dict，无需修改其他文件
- prompt 构建函数 (`build_agent_prompt`, `append_schema_instruction`) 作为模块级工具函数

#### human_review 节点
- 执行到该节点时，workflow 暂停 (status=`awaiting_review`)
- 通过 API `POST /runs/{id}/nodes/{node_id}/review` 提交审核
  ```json
  {"approved": true, "feedback": "通过理由"}
  ```
- `approved=true`: 上游节点的**真实产出**透传到下一个节点（审批元数据 `approved`/`feedback` 同时保留）
- `approved=false`: 通过条件边 `{{ nodes.xxx.output.approved }} == false` 反馈到上游重做
- 示例见 `examples/human-review-workflow.json`、`examples/design-review-workflow.json`

#### 回路检测 (stale node detection)
- 基于**无条件边**构建 ancestor 关系（BFS 沿无条件边向下游）
- 条件边 `A → B` 形成回路 ⟺ B 是 A 的祖先（通过无条件边 B → … → A）
- 正向条件边（如 `review_design → plan (approved == true)`）**不会**触发 staleness
- 只有回路条件边（如 `review_plan → plan (approved == false)`）在条件为 true 且 target 已完成时触发

#### 核心特性
- 条件分支判断 `{{ nodes.X.output.field }} == value`
- 状态持久化保存，支持断点续跑
- 每个 agent 节点固定 session-id (coding → review 回路复用上下文)
- 每个 run 分配隔离工作目录 `./workspace/<run_id>-<uuid>`
- 回路(Loop-back)支持 coding→review→coding
- 多起点(Multi-start)支持
- 节点汇集(Merge/Fan-in)支持

### 4. 定时任务 (`src/wflow/engine/scheduler.py`)
- 后端启动时自动恢复启用的 cron job
- 支持 Web UI 和 CLI 创建/管理
- Cron 表达式支持 5 字段(分钟级)和 6 字段(秒级)
- 可手动触发 (`POST /cron/{id}/trigger`)

### 5. API 端点
| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/workflows` | 创建工作流 |
| GET | `/api/v1/workflows` | 列出工作流 |
| GET | `/api/v1/workflows/{id}` | 工作流详情(含 config/inputs) |
| POST | `/api/v1/runs` | 启动运行 |
| GET | `/api/v1/runs/{id}` | 运行详情(含 DAG/节点/日志) |
| GET | `/api/v1/runs/{id}/files` | 浏览工作目录文件 |
| GET | `/api/v1/runs/{id}/files/content?path=` | 读取文件内容 |
| POST | `/api/v1/runs/{id}/nodes/{nid}/review` | 提交人工审核 |
| POST | `/api/v1/cron` | 创建定时任务 |
| GET | `/api/v1/cron` | 列出定时任务 |
| GET | `/api/v1/cron/{id}` | 定时任务详情(含 recent runs) |
| POST | `/api/v1/cron/{id}/toggle` | 启用/禁用定时任务 |
| DELETE | `/api/v1/cron/{id}` | 删除定时任务 |
| POST | `/api/v1/cron/{id}/trigger` | 手动触发 |

## 示例工作流
- `examples/complex-workflow.json` — 多起点+汇集+回路，含 claude/opencode/script 三种类型
- `examples/code-review.json` — 经典 coding→review 回路
- `examples/simple-opencode.json` — OpenCode 审查回路
- `examples/simple-script.json` — Script 单节点
- `examples/human-review-workflow.json` — Claude + 人工审核回路
- `examples/design-review-workflow.json` — 需求分析→设计→评审→计划→评审，双层人工审核

## 技术栈
- Python 3.11+, FastAPI, Click, SQLAlchemy 2.0 (async, aiosqlite), Pydantic v2
- Alpine.js 3.x SPA + dagre (CDN)
- APScheduler AsyncIOScheduler
- Claude Code CLI: `--output-format stream-json --input-format stream-json`
- OpenCode CLI: `opencode run --format json` (stdin pipe)
