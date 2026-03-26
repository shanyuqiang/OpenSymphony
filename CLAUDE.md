# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Symphony-CC is an autonomous GitHub Issue → PR orchestration service. It polls GitHub for labeled issues, creates isolated git worktrees, runs Claude Code via the Agent SDK, and opens PRs. The agent handles its own PR lifecycle (land skill) inside the session — the orchestrator only manages task state.

## Common Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest
pytest tests/test_orchestrator.py -v

# Run single test file
pytest tests/test_sdk_runner.py

# Start daemon (project must have config.yaml)
symphonyctl start

# Run in foreground
symphonyctl start --foreground

# Initialize project
symphonyctl init --repo owner/repo --budget 5

# Check status
symphonyctl status

# View logs
symphonyctl logs --tail 100
symphonyctl logs --issue 42
```

## Architecture

### Core Components

- **`lib/cli.py`** — `symphonyctl` CLI entry point. `main()` routes to subcommand handlers. Project root discovery walks up from cwd to find `config.yaml`.
- **`lib/orchestrator.py`** — FSM orchestrator + `StateStore` for JSON persistence. The `_TRANSITIONS` dict defines valid state flows. Crashes are recovered via `cleanup_orphaned()` on startup.
- **`lib/claude_sdk_runner.py`** — Agent execution via Claude Agent SDK `query()`. This is the primary runner; the old CLI subprocess runner is deprecated.
- **`lib/tracker.py`** — GitHub tracker via `gh` CLI. `parse_issue_meta()` extracts YAML frontmatter from issue bodies.
- **`lib/workspace.py`** — Git worktree isolation per issue. Copies `commit`, `pull`, `push`, `land` skills from main repo to each worktree via `_copy_skills_to_worktree()`.

### FSM State Machine

```
QUEUED → PREPARING → RUNNING → SUCCEEDED → PR_CREATED → LANDING
              ↓           ↓          ↓
           FAILED     FAILED     FAILED → RETRYING → (back to PREPARING)
                                           ↓
                                       ESCALATED
```

`TaskRecord.transition()` validates transitions against `_TRANSITIONS`. Invalid transitions raise `ValueError`.

### State Persistence (StateStore)

Task state lives in JSON files:
- `state/queue.json` — pending tasks
- `state/active/issue-{N}.json` — currently running
- `state/completed/issue-{N}.json` — finished (terminal states)

### Agent Lifecycle

1. **PREPARING**: Create git worktree branch `feat/issue-{N}`
2. **RUNNING**: Render workflow template + run SDK agent
3. **SUCCEEDED**: Agent used land skill inside session → PR created
4. **LANDING**: Monitoring CI + reviews via land skill
5. **On failure**: Retry up to `max_retries`, then escalate

### Skills Copied to Worktrees

The agent uses these skills during execution (auto-copied by workspace.py):
- `commit` — Create commits
- `pull` — Pull latest main
- `push` — Push branch
- `land` — Create PR, monitor CI, handle merge

## Configuration

`config.yaml` with frozen dataclass schema in `lib/config.py`. All values overridable via `SYMPHONY_*` environment variables (e.g., `SYMPHONY_AGENT_MODEL=sonnet`).

## Development Notes

- All config dataclasses are `frozen=True` (immutable)
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"`
- `SDKAgentRunner.run()` accepts `on_progress` callback for streaming progress
- `_block_pr_merge()` in SDK runner blocks direct `gh pr merge` to enforce land skill workflow
