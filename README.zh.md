# OpenSymphony

> Symphony – 将你的 Issue Tracker 变成自主编码 Agent 编排器。

OpenSymphony 是 [OpenAI Symphony](https://github.com/openai/symphony) 规范的 Python 实现。它将 **Gitea** Issues 与 **Claude Code CLI** 对接，为每个 Issue 自动创建隔离工作区并运行 Claude Code，直到生成 Pull Request。

---

## 工作原理

```
Gitea Issues  →  编排器  →  工作区  →  Claude Code CLI  →  Pull Request
```

1. **轮询** – Symphony 轮询 Gitea，找到带有 `symphony-doing` 标签的 Issues。
2. **工作区** – 为每个 Issue 独立 Clone 一个 Git 工作区。
3. **Agent** – 使用 Jinja2 渲染 Prompt，调用 Claude Code CLI 子进程。
4. **循环** – 若 Agent 未完成，以续写 Prompt 重试，最多 `max_turns` 次。
5. **完成** – Claude 添加 `symphony-done` 标签后，编排器关闭此次运行。

---

## 功能特性

- **Gitea Tracker** – REST API 集成，标签驱动生命周期（`symphony-doing` / `symphony-done`）
- **Claude Code CLI Agent** – 子进程模式，支持配置工具列表和 dangerous 模式
- **隔离工作区** – 每个 Issue 拥有独立的 Clone 目录
- **并发运行** – 可配置 `max_concurrent_agents` 并发数
- **指数退避重试** – 最大等待上限为 `max_retry_backoff_ms`
- **HTTP 控制台** – 可选 Starlette 服务，实时监控运行状态
- **WORKFLOW.md 配置** – 单文件，YAML front matter 配置 + Jinja2 Prompt 模板

---

## 环境要求

- Python 3.11+
- 已安装并登录的 [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- 可访问 API 的 Gitea 实例

---

## 安装

```bash
git clone https://github.com/shanyuqiang/OpenSymphony.git
cd OpenSymphony

# 推荐使用 uv
uv sync

# 或使用 pip
pip install -e .
```

---

## 快速开始

### 1. 复制工作流模板

```bash
cp WORKFLOW.md.example WORKFLOW.md
```

### 2. 编辑 `WORKFLOW.md`

文件分为两部分：YAML front matter 配置块 + Jinja2 Prompt 模板。

```markdown
---
tracker:
  kind: gitea
  endpoint: http://localhost:3000/api/v1
  api_key: $GITEA_TOKEN          # 支持环境变量引用
  owner: myuser
  repo: myproject

polling:
  interval_ms: 30000             # 30 秒轮询一次

workspace:
  root: ~/symphony_workspaces

agent:
  max_concurrent_agents: 3
  max_turns: 10
  max_retry_backoff_ms: 300000

claude:
  command: claude
  allowed_tools: ["Edit", "Bash", "Read", "Write"]
  dangerous_mode: true
  turn_timeout_ms: 3600000

server:
  port: 8080                     # 0 表示不启动控制台
---

# 任务说明

你正在处理 Gitea Issue: {{ issue.identifier }}

**标题**: {{ issue.title }}

**描述**:
{{ issue.description }}

## 完成协议

完成后，请添加 `symphony-done` 标签：
...
```

### 3. 设置环境变量

```bash
export GITEA_TOKEN=your_gitea_api_token
```

### 4. 启动

```bash
# 仅验证配置，不实际运行
symphony ./WORKFLOW.md --dry-run

# 启动编排器
symphony ./WORKFLOW.md
```

---

## Issue 生命周期

| 标签 | 含义 |
|------|------|
| `symphony-doing` | Issue 已被认领，正在处理中 |
| `symphony-done` | Agent 完成，等待人工审核 |

给 Issue 添加 `symphony-doing` 标签即可将其交给 Symphony 处理。审核完 PR 后，关闭或移除对应 Issue 即可。

---

## 项目结构

```
OpenSymphony/
├── src/symphony/
│   ├── cli.py            # typer CLI 入口
│   ├── orchestrator.py   # asyncio 编排主循环
│   ├── workflow.py       # WORKFLOW.md 解析（YAML + Jinja2）
│   ├── workspace.py      # Git 工作区管理
│   ├── models.py         # 共享数据模型
│   ├── config.py         # pydantic-settings 配置
│   ├── labels.py         # 标签生命周期工具
│   ├── tracker/
│   │   ├── base.py       # 抽象 Tracker 接口
│   │   └── gitea.py      # Gitea REST API 实现
│   ├── agent/
│   │   ├── runner.py     # Agent 运行循环
│   │   └── claude_cli.py # Claude Code CLI 子进程封装
│   └── server/
│       └── dashboard.py  # 可选 HTTP 控制台
├── tests/                # pytest 测试套件
├── WORKFLOW.md.example   # 工作流配置模板
└── pyproject.toml
```

---

## 开发指南

```bash
# 安装开发依赖
uv sync --extra dev

# 运行测试
pytest -v

# 查看覆盖率
pytest --cov=symphony --cov-report=term-missing

# 代码检查与类型检查
ruff check src tests
mypy src
```

---

## 配置参考

| 配置节 | 参数 | 默认值 | 说明 |
|--------|------|--------|------|
| `tracker` | `kind` | `gitea` | Tracker 类型 |
| `tracker` | `endpoint` | – | Gitea API 基础 URL |
| `tracker` | `api_key` | – | API Token（支持环境变量） |
| `polling` | `interval_ms` | `30000` | 轮询间隔（毫秒） |
| `agent` | `max_concurrent_agents` | `3` | 最大并发 Agent 数 |
| `agent` | `max_turns` | `10` | 每个 Issue 最大 Turn 数 |
| `agent` | `max_retry_backoff_ms` | `300000` | 退避重试上限（毫秒） |
| `claude` | `turn_timeout_ms` | `3600000` | 每次 Turn 超时（毫秒） |
| `server` | `port` | `8080` | 控制台端口（0 表示禁用） |

---

## 相关项目

| 项目 | 说明 |
|------|------|
| [openai/symphony](https://github.com/openai/symphony) | Elixir 原版规范 |
| [OasAIStudio/symphony-ts](https://github.com/OasAIStudio/symphony-ts) | TypeScript 移植版（Linear + Codex） |
| [openSymphony (Rust)](https://github.com/shanyuqiang/openSymphony) | Rust 移植版（GitHub + Claude Code） |

---

## 许可证

Apache-2.0
