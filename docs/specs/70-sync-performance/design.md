# Sync performance — design

[Spec](spec.md) · [Tasks](tasks.md)

## Profiling

`src/campusctl/profiling.py` holds a span recorder that does nothing unless `--profile` is set. It keeps bounded in-memory aggregates and emits one stderr line at completion or failure. Phase names: lock, playwright, launch-connect, user-agent, auth, roster, todo, sso-settle, course-selection, document-commit, response-completion, idle, dom-ready, extract, archive-page, modal, attachment-list, archive-restore, merge, serialize-write, teardown. Counts include `course_selections`, documents and SSO settlements; there are no guard-decision phases or per-route disposition counts. Output never contains URLs, query values, headers, bodies, credentials or course names and IDs; `course` is an ephemeral ordinal. `tools/profile_sync.py` compares two command lines over repeated runs for local fixture benchmarking.

Profile v2 includes a top-level `diagnostics` array for failed readiness, identity, to-do grid, archive-state and modal-binding checks. Passing checks add no record. Each record contains exactly:

- `check`: `page-readiness`, `course-identity`, `todo-grid`, `archive-state` or `modal-binding`; `outcome`: always `failed`.
- `page_kind` and `domain`: existing profile enums or null; `course`: a positive run-local ordinal or null.
- `counts`: known nonnegative integers only: `rendered_rows`, `response_items`, `tot_cnt`, `expected_rows`, `modal_controls`, `page_size`, `current_page`, `expected_page`, `expected_total`, `selection_requests`, `entry_documents`, `entry_commits`.
- `states`: Boolean `ids_match`, `route_match`, `same_document`, `modal_clear`, `completed`, or `next_state` (`enabled`, `disabled`, `absent`).
- `elapsed_ns` and `bound_ns`: nonnegative nanoseconds for elapsed check time and its timeout bound.

Unknown measurements are omitted from their maps, never filled from page text. Diagnostics and spans share the existing `max_events` budget (4096 by default); overflow increments `dropped_events`. The profile never stores identifiers, titles, URL values or page text.

## Combined course pass

`src/campusctl/providers/cnu/sync_all.py` owns the browser session, login, roster and publication; `src/campusctl/sync.py` and the single-domain commands call it.

- **Course selection.** Click the roster row once. The selection is proven when exactly one selection POST precedes exactly one `/std/lecture` commit in the main frame, and the rendered topbar course ID equals the roster course. The topbar is polled within the seven-second course-menu timeout because it renders after head assets.
- **Sections.** Lectures first, then assignments, notices and archive, each entered by clicking its course menu link as a user would. Each collector reads its committed section page, checks the page's course, its own responses and DOM, and returns rows or a course failure. The lecture collector requires a committed `/std/course` whose topbar course matches before reading `.learningRow` rows.
- **To-do.** Read once after roster discovery and before the first course selection, then return to the roster. A to-do failure marks that run's notices stale without stopping other domains.
- **CDP.** Combined sync opens its own page and closes it at the end; existing tabs are left untouched.

The LMS controls its page requests during sync and fetch. No operation-scoped request interceptor, route table or suppression is installed. Selected attachment transfers remain bound to the clicked official file and checked for response type, signature and size.

## Publication

Rows are staged per domain and course and merged with the existing domain merge routines, including lecture health. A course failure keeps that domain's previous rows for that course; a successful empty section replaces them. Unknown enrollment, deferred removal and filtered syncs keep their existing behavior. Catalog files are written atomically one by one after the browser is closed and while the session lock is still held; an I/O failure after some writes reports which domains were committed.
