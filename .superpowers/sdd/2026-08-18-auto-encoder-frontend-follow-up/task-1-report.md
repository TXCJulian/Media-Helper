# Task 1 report

Implemented immediate path re-evaluation primitives.

- Added `EncoderStore.active_job_for_source`, selecting the newest non-terminal job.
- Added `EncodeQueue.reprocess_path`, including seen-row invalidation, replanning, idempotent active-job responses, and SQLite unique-source race handling.
- Added focused store and queue tests.

Tests: `60 passed, 1 warning` (`tests/test_encoder_store.py tests/test_encoder_queue.py`), using the target backend virtualenv and a worktree-local pytest basetemp.

Concerns: pytest emitted one existing Starlette/httpx deprecation warning; no functional failures.
