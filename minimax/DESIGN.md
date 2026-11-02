# MiniMax SDD foundation (2026-11-02)

This is a newly authored demonstration business baseline, not a historical bug repair. The future change is inclusive `min_priority` filtering (integer 1..5; bool invalid), with status filtering and priority-descending/id-ascending ordering preserved. The baseline explicitly raises NotImplementedError for non-None min_priority. No MiniMax result exists yet.

Use a Python CLI and parameterized SQL against the dedicated `minimax_sdd` database. Index full source files with relative paths and line numbers, selected by a task manifest. A spec import replaces that task's current documents atomically; v2 points at a distinct, existing spec file. Context reads SQL content and verifies sources still match disk, failing on stale or missing sources. No embeddings or model are needed to retrieve this small exact dependency set.

Use real PowerContext `SQLiteProfile` plus `ScopeApplication`/task bindings from the competition fork David-goldenman/powercontext at 1be152d6 to save structured progress in a Scope summary. Each CLI run opens and closes the real SQLite backend. A later process resolves the task binding and injects saved completed/todo/verification fields into the next context and resume instruction. SQLite is the PowerContext backend, not a replacement JSON file.

Alternatives considered: a full Agent framework adds unrelated complexity; keyword/vector search adds uncertain ranking for four mandatory source files. A manifest-backed SQL index makes every required source auditable while still querying real database records. This deliberately limited selection strategy is adequate for one local task.

Constraints: only this module directory and dedicated SQL schema; no credentials read, no paid API calls, no service restarts, no commits/pushes. Existing PowerContext source is read-only. Future --execute accepts environment credentials and saves public proposals only, then stops for actual parent code review. Separate --apply-reviewed executes reviewed local code with a minimal credential-free child environment and frozen tests. It is not a Python sandbox.
