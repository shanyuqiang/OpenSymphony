# OpenSymphony

> Symphony – Turn your issue tracker into an autonomous coding agent orchestrator.

OpenSymphony is a Python implementation of the [OpenAI Symphony](https://github.com/openai/symphony) specification. It connects **Gitea** issues to **Claude Code CLI**, automatically spinning up isolated workspaces and running Claude Code on each issue until it creates a Pull Request.

---

## How It Works

```
Gitea Issues  →  Orchestrator  →  Workspace  →  Claude Code CLI  →  Pull Request
```

1. **Poll** – Symphony polls Gitea for open issues labelled `symphony-doing`.
2. **Workspace** – An isolated git workspace is cloned for each issue.
3. **Agent** – Claude Code CLI is invoked with a Jinja2-rendered prompt.
4. **Loop** – If the agent doesn't finish, Symphony retries with a continuation prompt (up to `max_turns`).
5. **Done** – When Claude adds the `symphony-done` label, the orchestrator closes the run.

---

## Features

- **Gitea tracker** – REST API integration with label-based lifecycle (`symphony-doing` / `symphony-done`)
- **Claude Code CLI agent** – subprocess-based, with configurable tools and dangerous mode
- **Isolated workspaces** – each issue gets its own cloned repo directory
- **Concurrent runs** – configurable `max_concurrent_agents`
- **Retry with backoff** – exponential back-off up to `max_retry_backoff_ms`
- **HTTP dashboard** – optional Starlette server for monitoring active runs
- **WORKFLOW.md config** – single file with YAML front matter + Jinja2 prompt template

---

## Requirements

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed and authenticated
- A running Gitea instance with API access

---

## Installation

```bash
git clone https://github.com/shanyuqiang/OpenSymphony.git
cd OpenSymphony

# with uv (recommended)
uv sync

# or with pip
pip install -e .
```

---

## Quick Start

### 1. Copy the workflow template

```bash
cp WORKFLOW.md.example WORKFLOW.md
```

### 2. Edit `WORKFLOW.md`

The file has two parts: a YAML front matter block and a Jinja2 prompt template.

```markdown
---
tracker:
  kind: gitea
  endpoint: http://localhost:3000/api/v1
  api_key: $GITEA_TOKEN          # env var reference
  owner: myuser
  repo: myproject

polling:
  interval_ms: 30000             # 30 s

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
  port: 8080                     # optional dashboard
---

# Task

You are working on Gitea Issue: {{ issue.identifier }}

**Title**: {{ issue.title }}

**Description**:
{{ issue.description }}

## Completion Protocol

When done, add the `symphony-done` label:
...
```

### 3. Set environment variables

```bash
export GITEA_TOKEN=your_gitea_api_token
```

### 4. Run

```bash
# dry run – validate config only
symphony ./WORKFLOW.md --dry-run

# start orchestrator
symphony ./WORKFLOW.md
```

---

## Issue Lifecycle

| Label | Meaning |
|-------|---------|
| `symphony-doing` | Issue is picked up and being processed |
| `symphony-done` | Agent has finished; awaiting human review |

Add `symphony-doing` to an issue to hand it off to Symphony. Remove or close the issue after reviewing the resulting PR.

---

## Project Structure

```
OpenSymphony/
├── src/symphony/
│   ├── cli.py            # typer CLI entry point
│   ├── orchestrator.py   # asyncio orchestration loop
│   ├── workflow.py       # WORKFLOW.md parser (YAML + Jinja2)
│   ├── workspace.py      # git workspace management
│   ├── models.py         # shared data models
│   ├── config.py         # pydantic-settings config
│   ├── labels.py         # label lifecycle helpers
│   ├── tracker/
│   │   ├── base.py       # abstract tracker interface
│   │   └── gitea.py      # Gitea REST API tracker
│   ├── agent/
│   │   ├── runner.py     # agent run loop
│   │   └── claude_cli.py # Claude Code CLI subprocess wrapper
│   └── server/
│       └── dashboard.py  # optional HTTP dashboard
├── tests/                # pytest test suite
├── WORKFLOW.md.example   # starter workflow config
└── pyproject.toml
```

---

## Development

```bash
# install dev dependencies
uv sync --extra dev

# run tests
pytest -v

# with coverage
pytest --cov=symphony --cov-report=term-missing

# lint & type check
ruff check src tests
mypy src
```

---

## Configuration Reference

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `tracker` | `kind` | `gitea` | Tracker backend |
| `tracker` | `endpoint` | – | Gitea API base URL |
| `tracker` | `api_key` | – | API token (env var OK) |
| `polling` | `interval_ms` | `30000` | Poll interval |
| `agent` | `max_concurrent_agents` | `3` | Parallel agent limit |
| `agent` | `max_turns` | `10` | Max turns per issue |
| `agent` | `max_retry_backoff_ms` | `300000` | Retry back-off cap |
| `claude` | `turn_timeout_ms` | `3600000` | Per-turn timeout |
| `server` | `port` | `8080` | Dashboard port (0 = disabled) |

---

## Related Projects

| Project | Notes |
|---------|-------|
| [openai/symphony](https://github.com/openai/symphony) | Original Elixir spec |
| [OasAIStudio/symphony-ts](https://github.com/OasAIStudio/symphony-ts) | TypeScript port (Linear + Codex) |
| [openSymphony (Rust)](https://github.com/shanyuqiang/openSymphony) | Rust port (GitHub + Claude Code) |

---

## License

Apache-2.0
