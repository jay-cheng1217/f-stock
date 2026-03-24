# Repository Agent Rules

## Required Startup Step
- Before any implementation task, read `skills/git-commit-after-change/SKILL.md`.

## Git Commit Policy
- For any task that modifies files, finish the task with a Git commit.
- Do not leave completed edits uncommitted unless the user explicitly says no commit.
- Follow commit message format:
  - `<type>(<scope>): <summary>`
- Allowed `type` values:
  - `feat`, `fix`, `chore`, `refactor`, `docs`, `test`

## Final Report Requirement
- After completing a modification task, report:
  - commit hash
  - commit message
  - whether `git status --short --branch` is clean
