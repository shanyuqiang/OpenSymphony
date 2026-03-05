---
tracker:
  kind: github
  repo: qjc-office/qjc-webapp
  trigger_label: symphony:ready
  active_labels: [symphony:in-progress]
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
  allowed_tools: "Bash(*),Read(*),Write(*),Edit(*),Glob(*),Grep(*)"
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

Instructions:
1. Implement the requested changes following project conventions.
2. Run tests and verify before committing.
3. Create a PR with "Closes #{{issue.number}}" in the body.
