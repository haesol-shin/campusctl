# Live run protocol

Every change that affects LMS traffic is planned from exactly one recorded live run. The run covers the whole path the change touches and logs enough to answer every later question offline. A second run for the same change needs owner approval and a stated reason that the first log cannot answer.

## Before the run

- The owner authorizes the run and its scope.
- Nothing else uses the same LMS account or browser during the run; other automation is paused and resumed afterwards even if the run fails.
- The browser session lock is free and the host has enough free memory for one browser.
- The run script is complete and reviewed: it performs every step of the scope in one session, so a failure in one step is logged and the remaining steps still run.

## Scope of a full-path run

A full-path run exercises every network command on every enrolled course:

1. Full sync of all four domains through the real CLI.
2. One notice fetch and one assignment fetch, with read, view and submission state recorded before and after.
3. One official material download.
4. Timing for each step with profiling enabled.

## What is logged

| Record | Fields |
| --- | --- |
| Request | sequence, time, step, domain, course ordinal, method, origin class, placeholder path, query keys, resource type, frame (main, child, popup), Referer path, status, content type, guard disposition and reason |
| Step | command, exit status, envelope status, error codes, elapsed time, profiling spans, course selection count |
| State | catalog record sets before and after, notice read state and view count, assignment submission state |
| Environment | campusctl revision, browser mode, browser version, host memory |

Response bodies are recorded only as JSON key paths with value types and equality flags against the selected IDs, never as values.

## Where it lives

- **Raw log:** owner-held private evidence, directory mode 0700, one directory per run.
- **Sanitized replay fixture:** `tests/fixtures/lms_traffic/<date>-<scope>.json`, with synthetic IDs, placeholder hashes and no query values.
- **Replay test:** `tests/test_traffic_replay.py` runs every recorded request through the current guard and asserts its disposition; it is the regression gate for later guard changes.
- **Run summary:** the spec that planned the run cites it as `owner-held sanitized evidence (YYYY-MM-DD)` with counts, dispositions and timings.
