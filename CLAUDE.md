# 工作流编排器

工作流编排器，底层封装 Claude Code CLI / OpenCode CLI 工具完成复杂任务。

## 模块结构

### 1. CLI 用户界面 (`src/wflow/cli/`)
- 任务启动、状态查看、日志追踪
- Cron 管理
- 服务器启动管理
- **工作流生成**: 通过自然语言描述自动生成工作流 JSON (`wflow generate`)
- 入口: `wflow` (console_scripts entry point)

#### 工作流生成 (`wflow generate`)
通过自然语言描述，调用 Claude Code CLI 或 OpenCode CLI 自动生成完整的工作流 JSON 文件。

```bash
# 基础用法
wflow generate "代码审查工作流：先写代码，再审查，不通过则修改"

# 指定后端和模型
wflow generate "多源研究合并" --backend opencode -m deepseek-v4-pro

# 指定输出路径
wflow generate "需求分析 → 设计 → 人工审核" -o my-workflow.json

# 预览不写入文件
wflow generate "自动化测试流水线" --dry-run

# 强制覆盖已有文件
wflow generate "CI/CD 流水线" -f
```

| 选项 | 说明 |
|------|------|
| `-b, --backend` | 后端选择：`claude`（默认）或 `opencode` |
| `-m, --model` | 模型名称，如 `sonnet`、`deepseek-v4-pro` |
| `-o, --output` | 输出文件路径（默认：`<workflow-name>.json`） |
| `-t, --timeout` | 超时秒数（默认 600） |
| `--dry-run` | 仅验证并打印 JSON，不写入文件 |
| `-f, --force` | 覆盖已存在的输出文件 |

生成流程：用户描述 → 构建 meta-prompt（含工作流 JSON 格式参考） → 调用 LLM → 提取 JSON → Pydantic + 语义校验 → 写入文件。

实现位于 `src/wflow/cli/generate.py`，直接调用适配器（非通过服务器），可离线使用。

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

## 适配器 (`src/wflow/adapters/`)

### ClaudeCLI (`claude_cli.py`)
- 流式解析 stream-json 输出，结果在 streaming 过程中内联提取（单次遍历）
- 手动缓冲读取 (`proc.stdout.read(65536)`) 避免 `StreamReader.readline()` 的 64KB 限制
- stderr 并发读取 (`asyncio.create_task`) 防止管道缓冲区死锁
- 完善异常处理：`TimeoutError`、通用 `Exception` 均会 kill 子进程 + finally 清理

### OpenCodeCLI (`opencode_cli.py`)
- 策略：`opencode run` 流式 stdout 仅用于日志和 session ID 提取；结果始终通过 `opencode export <sid>` 获取
- stderr 并发读取（同上）
- 完善异常处理（同上）
- `_export_and_extract` 对 `json.loads` 返回非 dict 有 isinstance 守卫

### ScriptRunner (`script_runner.py`)
- 子进程执行：context 通过 stdin JSON 传入，结果从 stdout JSON 解析
- 支持超时 kill + `ScriptError` 异常

### JSON 解析 (`common/json_parser.py`)
- `extract_json(text)` 三种策略：直接解析 → fenced code block → 平衡括号
- 所有策略均保证返回 `dict[str, Any]`（有 isinstance 守卫）

## 重试机制
- 节点执行失败时，executor 在同一条 `NodeExecution` 记录上进行重试
- 重试成功后会清除 `ne.error` 字段（防止 UI 展示陈旧的重试错误）
- 重试过程的错误通过 `RunLog` (warn 级别) 记录，可在日志查看

## 测试

### 测试组织

| 目录 | 类型 | 运行方式 |
|------|------|---------|
| `tests/test_adapters/` | 适配器单元测试 | `pytest` |
| `tests/test_engine/` | 引擎单元测试 | `pytest` |
| `tests/test_api/` | API 集成测试 (FastAPI TestClient) | `pytest` |
| `tests/test_models/` | ORM 模型测试 (SQLite 内存库) | `pytest` |
| `tests/test_services/` | 服务层单元测试 | `pytest` |
| `tests/test_cli/` | CLI 命令测试 (Click CliRunner) | `pytest` |
| `tests/e2e/` | E2E 测试 (Playwright + FastAPI) | `pytest -m e2e` |

### 运行测试

```bash
# 默认：运行全部单元/集成/API 测试（排除 E2E）
pytest                                    # 117 passed

# 单独运行 E2E 测试
pytest -m e2e                             # 11 passed

# 两者互斥运行 —— pytest-playwright 和 pytest-asyncio 管理事件循环的方式
# 不兼容，混跑会导致 async 测试全部 RuntimeError。tests/conftest.py 中的
# pytest_collection_modifyitems hook 确保它们不会同时执行。
```

### E2E 测试说明
- 需要 Playwright 浏览器 (`playwright install chromium`)
- `tests/e2e/conftest.py` 自动启动 FastAPI 测试服务器 (port 18100)
- 测试标记为 `@pytest.mark.e2e`，通过 `pytestmark = pytest.mark.e2e` 模块级应用
- 工作流名使用 UUID 后缀避免跨测试冲突
