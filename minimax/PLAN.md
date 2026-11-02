# MiniMax SDD Foundation Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans inline; preserve the user's no-commit constraint.

**Goal:** Prepare reproducible real seekdb/PowerContext infrastructure and a business baseline awaiting MiniMax.

**Architecture:** Python CLI imports a task manifest into dedicated seekdb tables, queries source documents, and incorporates real persisted PowerContext Scope state. The future model command uses that output to propose a restricted business-code edit.

**Tech Stack:** Python 3.12, PyMySQL, PowerContext source 1be152d6, SQLiteProfile.

**Spec:** DESIGN.md and the official MiniMax full task provided by the parent.

## Global Constraints
- Only minimax/ is writable; minimax_sdd is the only writable SQL database.
- No actual MiniMax request until user credentials and usable quota are available.
- No target implementation, commits, purchases, service modifications, or private evaluation labels.

## Review Focus
- Reject SQL database names outside minimax_sdd; use parameters for task identifiers.
- Missing or stale source files must fail before creating misleading context.
- Reimport replaces old documents so removed/v1 sources cannot survive.
- Missing PowerContext state means an explicit empty state; a second process must use saved state.
- Target test failures must be feature failures, with baseline regression passing.

## Tasks
- [x] Write baseline and infrastructure tests first, capture initial failing context run.
- [x] Author baseline status/sorting behavior; target min_priority remains explicitly unsupported.
- [x] Implement manifest import/query with source validation and spec replacement; verify real SQL.
- [x] Implement real Scope persistence and a separate-process resume test.
- [x] Prepare future MiniMax adapter, reproducible runner, README and one-page technical note.
- [x] Run verification, inspect diff, retain logs and report pending model acceptance.

Ruling: execute the supplied/approved scoped design inline without another user approval loop; parent specifically authorized the module and excluded actual target implementation. Cost if wrong: reviewable local files only.

Final review: fresh independent reviewer found two Important issues in the future adapter (unreviewed code execution with inherited credentials, and missing durable receipts on failure). Addressed with proposal-only execution, a separate parent-reviewed apply mode, credential-free test child environment, frozen test-source checks, actual test-count validation, and phase receipts. Four added offline boundary tests were observed RED before implementation and GREEN afterward. Parent additionally required accurate fork attribution; remote SHA was fetched into this module and verified.

Final verification: independent minimax/.venv plus a newly cloned pinned fork completed `scripts/verify_foundation.py` with exit 0; evidence `logs/foundation-20261102T074727445829Z`. Actual counts: regression 5 GREEN, real context integration 7 GREEN, offline adapter 8 GREEN; target 5 intentionally RED; full suite 25 tests, exit 1 with exactly five feature failures and zero environment errors. New-process PowerContext write/read reused the same Scope and saved todo. No model API was invoked, no target implementation was added, and no commit or push was performed.
