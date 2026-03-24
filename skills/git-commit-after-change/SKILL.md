---
name: git-commit-after-change
description: Enforce a strict Git workflow where every completed file-modification task ends with a commit. Use when changing code, data, or documents in a Git repository and the user expects a clear commit history with no finished work left uncommitted.
---

# Git Commit After Change

## Trigger Conditions
- Apply this skill whenever the task includes modifying files in a Git repository.
- Keep this rule active for the whole task: do not finish with completed edits left uncommitted.

## Mandatory Execution Flow
1. Check repository state before editing:
   - `git rev-parse --is-inside-work-tree`
   - `git status --short --branch`
2. Implement the requested file changes.
3. Run relevant checks (tests/lint/syntax) for the changed scope when feasible.
4. Stage only task-related files (prefer explicit paths, avoid broad staging by default).
5. Commit changes for the completed task.
6. Verify final state:
   - `git status --short --branch` must be clean for this task's files.
   - `git log --oneline -n 1` must show the new commit.

## Commit Message Standard
- Use this format:
  - `<type>(<scope>): <summary>`
- Allowed `type`:
  - `feat`, `fix`, `chore`, `refactor`, `docs`, `test`
- Write summary in imperative style and keep it specific.
- Use one logical task per commit unless the user explicitly requests split commits.

## Exception Rules
- Do not commit only if at least one of these is true:
  - User explicitly requests no commit.
  - Task is read-only (analysis/review only, no file edits).
  - No file content changed after implementation.
- If commit fails (identity, hooks, lock, conflicts), try to resolve immediately.
- If unresolved, report exact blocker and ask user for the minimal required input.

## Safety Guardrails
- Never include unrelated local changes in the task commit.
- Stage by explicit file paths when the worktree contains unrelated edits.
- Never amend/rewrite history unless user explicitly requests it.
- Never use destructive Git commands (`reset --hard`, forced checkout) without explicit user approval.

## Required Final Report
- Always include:
  - Commit hash
  - Commit message
  - Whether working tree is clean after commit
