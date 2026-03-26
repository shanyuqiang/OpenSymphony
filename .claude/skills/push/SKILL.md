---
name: push
description:
  Push current branch changes to origin and create or update the corresponding
  pull request; use when asked to push, publish updates, or create pull request.
---

# Push

## Prerequisites

- `gh` CLI is installed and available in `PATH`.
- `gh auth status` succeeds for GitHub operations in this repo.

## Goals

- Push current branch changes to `origin` safely.
- Create a PR if none exists for the branch, otherwise update the existing PR.
- Keep branch history clean when remote has moved.

## Related Skills

- `pull`: use this when push is rejected or sync is not clean (non-fast-forward,
  merge conflict risk, or stale branch).
- `commit`: use this before push if there are uncommitted changes.

## Steps

1. Check git status - ensure there are changes to push.
2. Identify current branch.
3. Push branch to `origin` with upstream tracking:
   - First try: `git push -u origin HEAD`
   - If rejected due to non-fast-forward, use `pull` skill, then retry
   - If auth/permission error, stop and report the error
4. Ensure a PR exists for the branch:
   - If no PR exists, create one.
   - If a PR exists and is open, update it.
   - If branch is tied to a closed/merged PR, create a new branch + PR.
5. Write a clear PR title that describes the change.
6. Write a PR body that includes:
   - What the change does
   - Why it was needed
   - Any relevant issue references (e.g., "Closes #123")
7. Reply with the PR URL from `gh pr view`.

## Commands

```sh
# Check status and identify branch
branch=$(git branch --show-current)
echo "Current branch: $branch"

# Verify there are changes to push
if git diff --quiet && [ -z "$(git status --porcelain)" ]; then
  echo "No changes to push"
  exit 0
fi

# Push with upstream tracking
git push -u origin HEAD

# If push failed due to non-fast-forward, use pull skill first
# git push --force-with-lease origin HEAD  # only if history was rewritten

# Check if PR exists
pr_state=$(gh pr view --json state -q .state 2>/dev/null || echo "NONE")
pr_number=$(gh pr view --json number -q .number 2>/dev/null || echo "")

# Create or update PR
if [ "$pr_state" = "NONE" ]; then
  gh pr create --title "$pr_title" --body "$pr_body"
else
  gh pr edit --title "$pr_title" --body "$pr_body"
fi

# Show PR URL
gh pr view --json url -q .url
```

## Notes

- Do not use `--force`; only use `--force-with-lease` as the last resort.
- Distinguish sync problems from remote auth/permission problems:
  - Use the `pull` skill for non-fast-forward or stale-branch issues.
  - Surface auth, permissions, or workflow restrictions directly instead of
    changing remotes or switching protocols.
