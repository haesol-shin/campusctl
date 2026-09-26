# Live run protocol

A live run exercises campusctl on the real LMS and records enough to plan the next change offline. Each change that touches LMS behavior gets one full-path run; a second run for the same change needs owner approval and a reason the first record cannot answer. One run cannot show every case, so each run also feeds the owner's LMS behavior notes, which accumulate what every run has seen.

## Before the run

- The owner authorizes the run and its scope.
- Nothing else uses the same LMS account or browser during the run; other automation is paused and resumed afterwards even if the run fails.
- The browser session lock is free and the host has enough free memory for one browser.
- The run script performs every step of the scope in one go, so a failed step is recorded and the remaining steps still run.

## Scope of a full-path run

1. Full sync of all four domains through the real CLI.
2. One notice fetch and one assignment fetch, with read, view and submission state recorded before and after.
3. One official material download.
4. Wall time for every step, with `--profile` spans for sync.

## What is recorded

| Record | Fields |
| --- | --- |
| Request | sequence, time, step, domain, course ordinal, method, origin, path, query keys, resource type, frame (main, child, popup), Referer path, status, content type |
| Page | how it was entered, when it committed, when key elements rendered, dialogs and redirects |
| Step | command, exit status, envelope status, error codes, elapsed time, profiling spans, course selection count |
| State | catalog record sets before and after, notice read state and view count, assignment submission state |
| Environment | campusctl revision, browser mode, browser version, host memory |

Response bodies are recorded as JSON key paths with value types and equality flags against the selected IDs, never as values.

## After the run

- **Raw record:** owner-held private evidence, directory mode 0700, one directory per run.
- **LMS behavior notes:** the owner's local notes are updated with every new or changed LMS behavior the run showed. They are never committed.
- **Fixtures:** a test that needs a new LMS behavior gets a sanitized synthetic fixture with synthetic IDs and `.invalid` hosts.
- **Citation:** specs cite the run as `owner-held sanitized evidence (YYYY-MM-DD)` with counts and timings.
