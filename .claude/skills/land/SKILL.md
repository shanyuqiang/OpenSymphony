---
name: land
description:
  DEPRECATED - Land is now handled by orchestrator. The orchestrator automatically
  monitors CI, waits for review, and squash-merges PRs after push. Do NOT invoke
  this skill manually.
---

# Land (Deprecated)

**This skill is deprecated. Orchestrator handles land automatically.**

The orchestrator will:
1. Monitor CI checks and wait for green
2. Wait for human review approval
3. Squash-merge when ready
4. Update issue labels (symphony:done or symphony:failed)

## Goals

- Ensure the PR is conflict-free with main.
- Keep CI green and fix failures when they occur.
- Wait for human review approval (APPROVED state).
- Squash-merge the PR once CI is green and review is approved.
- Do not yield until the PR is merged unless blocked.
- Update issue labels: `symphony:merging` when starting, `symphony:done` when merged.

## Preconditions

- `gh` CLI is authenticated.
- You are on the PR branch with a clean working tree.
- PR has been created with `push` skill.

## Steps

1. Extract issue number from branch name (e.g., `feat/issue-123` → `123`).
2. Add `symphony:merging` label to the issue (replace `symphony:in-progress`).
3. Locate the PR for the current branch.
4. If the working tree has uncommitted changes, commit with the `commit` skill
   and push with the `push` skill before proceeding.
5. Check mergeability and conflicts against main.
6. If conflicts exist, use the `pull` skill to fetch/merge `origin/main` and
   resolve conflicts, then use the `push` skill to publish the updated branch.
7. Monitor CI checks and human review status using the watch helper.
8. If CI fails, fix the issue and push updates.
9. If human review requests changes, address them and push updates.
10. When CI is green and review is APPROVED, squash-merge.
11. Report final status.

## Watch Helper

Use the asyncio watcher to monitor CI checks and review status in parallel:

```
python3 .claude/skills/land/land_watch.py
```

Exit codes:

| Code | Meaning |
|------|---------|
| 0 | PR successfully merged |
| 2 | Human review feedback detected (blocking) |
| 3 | CI checks failed |
| 4 | PR head updated (force-push detected) |
| 5 | Merge conflicts detected |

## Commands

```sh
# Get PR info
branch=$(git branch --show-current)
issue_number=$(echo "$branch" | sed 's/.*issue-//')

# Add symphony:merging label to issue (replace symphony:in-progress)
gh issue edit "$issue_number" --add-label "symphony:merging" --remove-label "symphony:in-progress" 2>/dev/null || true

pr_number=$(gh pr view --json number -q .number)
pr_title=$(gh pr view --json title -q .title)
pr_body=$(gh pr view --json body -q .body)

# Check mergeability
mergeable=$(gh pr view --json mergeable -q .mergeable)

if [ "$mergeable" = "CONFLICTING" ]; then
  # Use pull skill to resolve conflicts
fi

# Watch CI and review
python3 .claude/skills/land/land_watch.py
exit_code=$?

case $exit_code in
  0) echo "PR merged successfully" ;;
  2) echo "Human review feedback - address and retry" ;;
  3) echo "CI failed - fix and retry" ;;
  4) echo "PR head updated - recheck" ;;
  5) echo "Conflicts - resolve and retry" ;;
esac

# Squash-merge when ready
gh pr merge --squash --subject "$pr_title" --body "$pr_body"

# Extract issue number from branch name (feat/issue-{number}) and update labels
branch=$(git branch --show-current)
issue_number=$(echo "$branch" | sed 's/.*issue-//')
gh issue edit "$issue_number" --add-label "symphony:done" --remove-label "symphony:ready,symphony:merging" 2>/dev/null || true
```

## Failure Handling

- **CI failure**: Pull logs with `gh pr checks` and `gh run view --log`, fix the issue,
  commit and push updates, re-run the watch.
- **Human review changes**: Address the feedback, push updates, re-run the watch.
- **Merge conflicts**: Use `pull` skill to merge `origin/main`, resolve conflicts,
  push and re-run the watch.
- **Auto-fix commits**: If CI pushes an auto-fix commit, pull it, merge `origin/main`
  if needed, and force-push to retrigger CI.
- **Flaky failures**: Use judgment to determine if a failure is a flake. If it is,
  you may proceed without fixing it.

## Notes

- Do not merge while CI is pending or human review has changes requested.
- If review state is CHANGES_REQUESTED, you must address feedback before merging.
- If review state is COMMENTED, use judgment to proceed or wait.
- Do not enable GitHub auto-merge; always use squash-merge manually.
