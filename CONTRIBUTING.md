# Contributing to Symphony-CC

Thank you for your interest in contributing! This guide will help you get started.

## Development Setup

### Prerequisites

- Python 3.12+
- GitHub CLI (`gh`) -- installed and authenticated
- Claude Code CLI -- Max Plan subscription
- Git 2.15+ (worktree support)

### Environment

```bash
git clone https://github.com/qjc-office/symphony-cc.git
cd symphony-cc
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=lib --cov-report=term-missing

# Run a specific test file
pytest tests/test_orchestrator.py -v
```

## Code Style

- **Python 3.12+** -- Use modern syntax (type unions `X | Y`, match statements, etc.)
- **asyncio** -- All I/O operations must be async
- **Type hints** -- Required on all function signatures
- **Docstrings** -- Required on all public classes and functions
- **Comments** -- Korean (`한국어`) is fine for inline comments

### File Size Limits

- Max **800 lines** per file
- Max **50 lines** per function
- Max **4 levels** of nesting

## Project Structure

```
lib/
  cli.py          -- symphonyctl CLI entry point
  config.py       -- YAML config + env var overrides
  orchestrator.py -- FSM state machine + dispatch
  runner.py       -- claude --print agent execution
  workspace.py    -- Git worktree isolation
  tracker.py      -- GitHub Issues polling
  notifier.py     -- GitHub comments + Slack
  dashboard.py    -- Rich TUI dashboard
  init.py         -- Project initialization
  workflow.py     -- Template rendering
  logger.py       -- Structured JSON logging
tests/
  test_*.py       -- pytest + pytest-asyncio
templates/
  WORKFLOW.md     -- Default workflow template
```

## Pull Request Process

1. **Fork** the repository and create a feature branch:
   ```bash
   git checkout -b feat/your-feature
   ```

2. **Write tests first** (TDD):
   - RED: Write a failing test
   - GREEN: Implement the minimum to pass
   - IMPROVE: Refactor

3. **Ensure all tests pass**:
   ```bash
   pytest tests/ -v
   ```

4. **Create a PR** with:
   - Clear title (conventional commits: `feat:`, `fix:`, `refactor:`, etc.)
   - Description of what changed and why
   - Test plan

## Issue Reporting

### Bug Reports

Use the [Bug Report template](.github/ISSUE_TEMPLATE/bug-report.yml) and include:

- Steps to reproduce
- Expected vs actual behavior
- Python version, OS, Symphony-CC version

### Feature Requests

Open a regular issue with:

- Use case description
- Proposed solution (if any)
- Alternatives considered

## Commit Messages

```
<type>: <description>

<optional body>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`

## License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).
