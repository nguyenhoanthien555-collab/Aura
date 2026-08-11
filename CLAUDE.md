# Aura — Persistent Development Instructions

## FIRST ACTION ON EVERY SESSION

Before doing development work:

1. Read this file.
2. Read .claude/project-state.md.
3. Read .claude/current-task.md.
4. Read .claude/progress.md.
5. Read .claude/decisions.md only when architectural decisions are relevant.

Do NOT reconstruct the previous conversation unless necessary.

## CONTEXT COMPACTION RULE

When context is compacted:

- Do NOT reread the entire conversation.
- Do NOT redo completed work.
- Do NOT assume previous chat messages are the source of truth.
- Read the persistent state files first.
- Inspect the current repository state.
- Continue from current-task.md.
- Verify existing implementation before changing it.

The repository and .claude/*.md state files are the persistent source of truth.

## STATE MANAGEMENT

After every meaningful milestone:

1. Update .claude/progress.md.
2. Update .claude/current-task.md.
3. Update .claude/project-state.md if architecture/status changed.
4. Record important architectural decisions in .claude/decisions.md.

Do not wait until the end of a long task to update state.

## DEVELOPMENT RULES

- Inspect before modifying.
- Make targeted changes.
- Preserve existing architecture unless explicitly instructed otherwise.
- Do not rewrite working systems without a concrete reason.
- Search the repository before creating new abstractions.
- Reuse existing utilities and systems.
- Keep changes consistent with the existing project structure.
- Run relevant tests/checks after changes.
- If something fails, diagnose the actual cause before changing unrelated code.

## AVOID CONTEXT WASTE

Do not:

- reread unrelated files,
- dump huge files into context,
- repeat completed analysis,
- repeatedly explain the same architecture,
- inspect the entire repository when only a small subsystem is relevant.

Prefer:

- targeted searches,
- relevant files,
- git diff,
- git status,
- tests,
- persistent state files.

## TASK COMPLETION

Before declaring a task complete:

1. Verify the implementation.
2. Check git diff.
3. Run appropriate tests/checks.
4. Update persistent state.
5. Clearly record remaining issues.

## OVERNIGHT MODE

When executing a long task autonomously:

- Continue through the task queue.
- Do not stop merely because one subtask is complete.
- After each completed milestone, persist state.
- If blocked, record the blocker and move to the next independent task when safe.
- Never invent requirements.
- Never perform destructive architectural changes without explicit justification.

## SMALL-MODEL CODING DISCIPLINE

You are working with local Qwen3-Coder 30B via Ollama as an external coding/debugging agent for Aura.
Prioritize correctness, verification, and repository evidence over guessing.

### Before coding
1. Read `.claude/project-state.md`.
2. Read `.claude/current-task.md`.
3. Read `.claude/progress.md`.
4. Inspect only files relevant to the current task.
5. Search the repository before inventing or creating anything.
6. Identify existing implementations that can be reused.
7. Make a short implementation plan before editing.

### While coding
- Make small, coherent changes.
- Do not rewrite working systems unnecessarily.
- Do not invent files, functions, classes, APIs, dependencies, configuration keys, or behavior.
- Prefer existing utilities and architecture.
- Avoid unrelated refactors.
- If uncertain, inspect the repository instead of guessing.

### After coding
1. Run relevant tests/checks using `.venv\Scripts\python.exe -m pytest -q`.
2. Inspect `git diff`.
3. Look for regressions, duplicated systems, broken imports, bad assumptions, and unrelated changes.
4. Fix discovered problems.
5. Update `.claude/progress.md`.
6. Update `.claude/current-task.md`.
7. Update `.claude/project-state.md` when architecture or project status changes.

### SELF-REVIEW
Before declaring any task complete, verify:

- Does the implementation actually satisfy the task?
- Did I duplicate an existing system?
- Did I break an existing API?
- Did I introduce unnecessary dependencies?
- Are edge cases handled?
- Are tests needed?
- Did I modify unrelated files?
- Is there an existing utility I should reuse?
- Does the code match the existing architecture?

### CONTEXT COMPACTION
When context is compacted:

- DO NOT reconstruct the entire previous conversation.
- DO NOT reread unrelated files.
- Read the persistent `.claude/*.md` state files first.
- Inspect the current repository state.
- Continue from `current-task.md`.
- Do not redo completed work.
- Verify existing work instead of assuming it is correct.

### OVERNIGHT MODE
When working autonomously:

- Work through `task-queue.md` from top to bottom.
- Complete one coherent task at a time.
- Verify every completed task.
- Persist state after every meaningful milestone.
- Continue to the next independent task automatically.
- If blocked, record the blocker and continue with another safe independent task.
- Never make destructive architectural changes based on assumptions.
