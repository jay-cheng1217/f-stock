# Contributing Guide

## Branch Strategy
- Keep `main` stable and releasable.
- Create one branch per task from `main`.
- Branch naming:
  - `feature/<short-topic>`
  - `fix/<short-topic>`
  - `chore/<short-topic>`

## Commit Convention
Use clear, small commits with this format:

```
<type>(<scope>): <summary>
```

Recommended `type` values:
- `feat`: new feature
- `fix`: bug fix
- `chore`: maintenance or tooling
- `refactor`: code change without behavior change
- `docs`: documentation only
- `test`: tests only

Examples:
- `feat(stock): add monthly revenue parser`
- `fix(batch): handle empty csv input`
- `docs(workflow): document branch policy`

## Pull Request / Merge Checklist
- Rebase or merge from latest `main` before opening PR.
- Keep PR focused on one topic.
- Include test notes (what was run, what was verified).
- Ensure no secrets or machine-specific files are committed.

## Collaboration Rule
- Do not push directly to `main` unless both collaborators agree.
- Always include enough context in commit messages so another teammate can continue your work without extra explanation.
