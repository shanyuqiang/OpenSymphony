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

Use the **commit** skill to create commits. The skill is already loaded and available.

## Step 3: When you need to sync with main

Use the **pull** skill to merge origin/main and resolve conflicts.

## Step 4: When you need to push/create PR

Use the **push** skill to push branch and create PR. The label will be automatically updated to `symphony:merging`

## Step 5: When PR is created (symphony:merging) - CRITICAL

**This is the most important step. NEVER merge directly using `gh pr merge`.**

1. Use the **land** skill to monitor CI and review:
   - Run `python3 .claude/skills/land/land_watch.py`
   - Wait for it to complete (it monitors CI and review)
2. **Check exit code**:
   - If exit code is `0`: Run `gh pr merge --squash --subject "$pr_title" --body "$pr_body"`
   - If exit code is `2`: Blocking review detected, address feedback and retry from step 1
   - If exit code is `3`: CI failed, fix issues and retry from step 1
   - If exit code is `4`: PR head updated, pull changes and retry
   - If exit code is `5`: Merge conflicts, resolve and retry
3. **After successful merge**: Labels will be automatically updated to `symphony:done`

## Guardrails

- **NEVER** call `gh pr merge` directly without running `land_watch.py` first
- **ALWAYS** check `land_watch.py` exit code before merging
- If blocked by missing tools/auth, report failure and stop
