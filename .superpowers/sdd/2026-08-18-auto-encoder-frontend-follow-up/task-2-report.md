# Task 2 report

Implemented picker filtering and route request/response contracts.

- Added one shared walk-pruning predicate for directory and file pickers.
- Excludes dot-prefixed trees, `.trickplay`, and the configured top-level music folder; excluded roots return no files.
- Added `PresetImport.include_names` selection support without changing the import response envelope.
- Added preview, reprocess, and bulk-run contract models (with descriptive aliases) and applied the reprocess request model to its route.
- Added focused route tests for directory/file exclusions and named preset selection.

Verification: `56 passed, 1 skipped` for `tests/test_encoder_routes.py` using the repository's available backend virtualenv and a workspace-local pytest basetemp (rerun after the final dot-file exclusion change). One existing FastAPI/httpx deprecation warning remains.

Concern: the default pytest temp root is inaccessible in this environment, so the suite required `--basetemp` under the worktree. The worktree also contains unrelated pytest temp directories (including the pre-existing `backend/.pytest-tmp-review/`).
