# Sync performance — design

[Spec](spec.md) · [Tasks](tasks.md)

## Profiling

`src/campusctl/profiling.py` holds a span recorder that does nothing unless `--profile` is set. It keeps bounded in-memory aggregates and emits one stderr line at completion or failure. Phase names: lock, playwright, launch-connect, user-agent, auth, roster, todo, course-selection, document-commit, response-completion, guard-headers, guard-decision, guard-disposition, idle, dom-ready, extract, archive-page, modal, attachment-list, archive-restore, merge, serialize-write, teardown. Counts include `course_selections`, documents and requests. Output never contains URLs, query values, headers, bodies, credentials or course names and IDs; `course` is an ephemeral ordinal. `tools/profile_sync.py` compares two command lines over repeated runs for local fixture benchmarking.

## Combined course pass

`src/campusctl/providers/cnu/sync_all.py` owns the browser session, login, roster, guard and publication; `src/campusctl/sync.py` and the single-domain commands call it.

- **Course selection.** Click the roster row once. The selection is proven when exactly one selection POST precedes exactly one `/std/lecture` commit in the main frame, and the rendered topbar course ID equals the roster course. The topbar is polled within the seven-second course-menu timeout because it renders after head assets.
- **Sections.** Lectures first, then assignments, notices and archive, each entered by clicking its course menu link as a user would. Each collector reads its committed section page, checks the page's course, its own responses and DOM, and returns rows or a course failure. The lecture collector requires a committed `/std/course` whose topbar course matches before reading `.learningRow` rows.
- **To-do.** Read once, after the first course selection and before the first notice board. A to-do failure marks that run's notices stale without stopping other domains.
- **Guard.** One context-wide guard following the [constitution's rules](../constitution.md#request-guard) stays installed from login to cleanup; switching domains changes its operation label, not its coverage.
- **CDP.** Combined sync opens its own page and closes it at the end; existing tabs are left untouched.

## Publication

Rows are staged per domain and course and merged with the existing domain merge routines, including lecture health. A course failure keeps that domain's previous rows for that course; a successful empty section replaces them. Unknown enrollment, deferred removal and filtered syncs keep their existing behavior. Catalog files are written atomically one by one after the browser is closed and while the session lock is still held; an I/O failure after some writes reports which domains were committed.
