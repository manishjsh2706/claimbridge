# ClaimBridge — Working Rules

These rules apply to every session working in this repository.

## 1. Read the spec before doing anything

Before answering, designing, or writing code, read:

- `problem-statement.md` — requirements, constraints, success criteria
- `iteration-backlog.md` — iteration scope, definition of done, demo scripts
- `resources/` — tenant catalog, tenant policies, CARC/RARC reference,
  sample claims, member-summary rubric, provider spec, onboarding checklist,
  golden eval cases

Do all mandatory checks first (existing code, DB state, versions), then act.
Earlier work drifted from the spec because this was skipped.

## 2. Always ask before deleting anything

Ask the owner for explicit permission before deleting or dropping anything:
files, folders, database tables or rows, Weaviate collections, or any data.
Every time, no exceptions — even when the item looks unused.

## 3. Current state

See `PROJECT_STATUS.md`.
