---
tracker:
  kind: github
  repo: shanyuqiang/OpenSymphony
  trigger_label: symphony:ready
  active_labels: [symphony:in-progress]
  merging_label: symphony:merging
  terminal_labels: [symphony:done, symphony:failed]
polling:
  interval_s: 30
workspace:
  root: ~/symphony-workspaces
agent:
  max_concurrent: 2
  max_retries: 3
  retry_delay_s: 60
  max_budget_usd: 5
  model: opus
  allowed_tools: "Skill(*),Bash(*),Read(*),Write(*),Edit(*),Glob(*),Grep(*)"
hooks:
  after_create: |
    git checkout -b feat/issue-{{issue.number}} origin/main
  before_run: |
    git pull origin main --rebase
  after_run: |
    echo "Issue #{{issue.number}} completed at $(date)"
---

You are working on GitHub issue #{{issue.number}}.

Title: {{issue.title}}
Body: {{issue.body}}

{% if attempt %}
Continuation: retry attempt #{{attempt}}. Resume from current state.
{% endif %}

## Label Flow

| Label | Meaning |
|-------|---------|
| `symphony:ready` | Issue is queued, waiting to be picked up |
| `symphony:in-progress` | Agent is actively working on the issue |
| `symphony:merging` | PR created, waiting for CI and land to complete |
| `symphony:done` | PR successfully merged |
| `symphony:failed` | Failed after max retries |

## Skills Reference

Skills are pre-loaded from `.claude/skills/`. They are automatically available and will be used by the Agent when needed through the `Skill` tool.

## Step 1: Implementation (symphony:in-progress)

1. Understand the issue requirements from title and body
2. Run tests and verify current behavior before making changes
3. Implement the requested changes following project conventions
4. Update tests if needed

## Step 2: When you need to commit

INVOKE the commit skill using the Skill tool with name "commit".

## Step 3: When you need to sync with main

INVOKE the pull skill using the Skill tool with name "pull".

## Step 4: When you need to push/create PR

INVOKE the push skill using the Skill tool with name "push". The label will be automatically updated to `symphony:merging`

## Step 5: When PR is created (symphony:merging) - CRITICAL

**This is the most important step. NEVER merge directly using `gh pr merge`.**

1. **INVOKE the land skill** using the Skill tool with name "land" and await the results.
2. The land skill will:
   - Monitor CI checks via `land_watch.py`
   - Wait for human review approval
   - Return when ready to merge
3. **Only after land skill completes successfully**, merge the PR.

**DO NOT** call `gh pr merge` directly without invoking the land skill first.

## Guardrails

- **NEVER** call `gh pr merge` directly - always INVOKE the land skill first
- **ALWAYS** use the Skill tool to invoke skills (commit, push, pull, land)
- **NEVER** bypass skills by running commands directly
- If blocked by missing tools/auth, report failure and stop
