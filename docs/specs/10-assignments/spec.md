# Assignments: metadata sync and list (PR2)

Governed by the [project constitution](../constitution.md). This PR replaces the assignment-listing responsibility of the legacy classroom scraper without moving scheduled task creation or its action ledger. Assignment details/packages are a later, separately gated capability.

## Planned v0.4.0 delta

[50 UX](../50-v040-ux/spec.md) supersedes lecture-only bare sync, exact-ID-only course filters and cache-only list only when `--refresh` is explicitly supplied; unknown filters now error, ordinary lists remain offline and show age. [60](../60-headless-replay/tasks.md) owns headless approval; global browser flags replace command-local flags. [70](../70-sync-performance/design.md) may share session/course selection but preserves this domain's complete-section and stale-merge checks. Status consumes this catalog, not todo. Assignment fetch remains solely [40-fetch](../40-fetch/spec.md).

## User stories

### US1 — Refresh assignments safely (P1)

As a student, I can refresh assignment metadata for all enrolled courses or one selected course without changing coursework state or retrieving details/media.

Independent test: Given fixture-only LMS pages and a preexisting catalog, run assignment sync and inspect the response and catalog; no live LMS is contacted.

- **WHEN** a complete roster and addressable assignment rows are observed, **THEN** only those courses' rows are replaced and the result reports freshly completed course and assignment counts.
- **WHEN** one course fails or an item has no stable identity, **THEN** the whole course keeps its old rows, is marked stale, and sync returns partial success while independent successful courses commit.
- **WHEN** a complete course's `/api/v1/task/stdList` XHR succeeds with zero assignment rows and, once the page is idle, `tbody#tbody` has zero rows, **THEN** its previous assignments are replaced by an empty set.
- **WHEN** full enrollment discovery fails, **THEN** previous rows and catalog timestamp remain unchanged, enrollment becomes unknown, and sync reports a discovery error.
- **WHEN** a full roster is successful but any course fails, **THEN** previously cached courses absent from that roster remain stale until a fully successful full sync can remove them.

### US2 — Inspect selectable assignments (P1)

As a student, I can read cached assignments in a human-friendly terminal view, including the complete ID needed for a later selected operation; automation can request a stable JSON envelope.

Independent test: Seed only a local catalog, run the list command in JSON and human modes at narrow width, and check all fields and stale warnings without a browser or account configuration.

- **WHEN** a list is requested, **THEN** every submitted and unsubmitted row is returned with course, title, native task ID, due text or null, status, and a full opaque entity ID.
- **WHEN** a course filter is supplied, **THEN** only that course's assignments are shown; an unknown filter produces an empty list.
- **WHEN** enrollment is unknown or a course is failed/deferred, **THEN** cache metadata reports staleness and the human view warns even if no rows are displayed.
- **WHEN** no assignment catalog exists, **THEN** the command explains which assignment sync to run rather than silently returning zero rows.

### US3 — Preserve legacy assignment identity (P1)

As the later scheduled adapter, I can map a listed assignment to the existing task-action ledger without deriving native fields from an opaque ID, changing dedup identity, or creating a second ledger.

Independent test: Compare the JSON row's native fields and full entity ID against a golden legacy-upsert argument fixture without importing the external notifier. The later adapter separately exercises the real upsert API against a temporary preseeded ledger before cutover.

- **WHEN** the same course and native task ID are seen, **THEN** the existing assignment identity remains stable across syncs.
- **WHEN** the assignment is submitted, **THEN** the status field stays true and the later adapter preserves the ledger's completed/skipped task-action semantics.
- **WHEN** a previously known due date is absent in a new row, **THEN** JSON represents the source absence as null; the later adapter may use the existing ledger's coalescing behavior without campusctl writing to that ledger.

## Functional requirements

- **FR-001** The system MUST support metadata-only assignment sync for all enrolled courses and an optional selected course; normal sync MUST continue to default to lectures.
- **FR-002** The system MUST provide a cache-only assignment list with optional course filter, human terminal output by default on a TTY, and explicit JSON output for agents.
- **FR-003** Each listed assignment MUST expose a stable opaque entity ID plus separate native task ID from `a[data-act="detail"][data-id]` (format `TB_L_REPORT{digits}`), course ID and label, assignment kind, title, due text or null, and Boolean submitted status; submitted rows MUST NOT be filtered out.
- **FR-004** Assignment IDs MUST retain the existing course/native-task identity mapping; candidate assignment rows MUST be selected independently of whether their native-ID attribute exists, and a missing, blank, or duplicate native identity MUST make the entire course unaddressable rather than silently dropping a row.
- **FR-005** Due text MUST retain the LMS-displayed value after trimming, without invented timezone conversion; an absent or empty due value MUST be null.
- **FR-006** Submitted status MUST follow the observed complete badge or completed status text; missing/unrecognized status maps to false for legacy compatibility.
- **FR-007** Assignment extraction MUST include attached but visually hidden rows; it MUST NOT interpret a missing menu/table, stalled render, or an empty `#table_list > #tbody` before asynchronous row loading completes as an observed empty course. An empty course requires a successful course-specific `/api/v1/task/stdList` XHR, idle page and zero rows in `tbody#tbody`; prefer the response body's row count as authority when accessible. `#alarmList .table_dataBlank` belongs to a different widget and MUST NOT prove task emptiness. [NEEDS CLARIFICATION: exact `/std/task` DOM empty marker; non-blocking because the completed data response and idle zero-row tbody establish emptiness.]
- **FR-008** Successful courses MUST replace their own cached rows, including verified empty courses; failed courses MUST preserve all old rows and expose failure markers.
- **FR-009** A selected-course sync MUST leave other courses, their failure markers, and enrollment state unchanged; a full sync MUST remove unenrolled rows only after complete roster and course success.
- **FR-010** Full roster discovery failure MUST preserve prior rows and catalog timestamp, mark enrollment unknown, and report an error; lists MUST expose enrollment and failed-course staleness.
- **FR-011** Sync results MUST count freshly completed courses and assignment rows, not lecture rows or retained stale rows.
- **FR-012** The system MUST use the existing versioned response envelope and safe structured errors with established exit-code categories.
- **FR-013** Human list output MUST show course, title, submitted status, due date, and each full selectable entity ID without truncation, plus warnings for unknown enrollment or failed/deferred courses.
- **FR-014** The system MUST NOT open assignment detail views, download lecture video, intercept/capture streams, retrieve files, or mutate read/submission/attendance/grading/enrollment state. The PR1.1 named Panopto script and connection-logging suppressions MUST abort their matching requests before the network while allowing sync to continue; all other unpinned page-originated requests MUST fail closed.
- **FR-015** The `assignments` domain module's `CAPABILITY["policy"]` MUST own the approved `assignments.sync` exact-origin/path/method pins for guarded roster/course-entry bootstrap, task list, and operation-specific read-only background requests, plus named suppressions and same-origin static resource types. Exact LMS POST `/api/v1/week/getStdActivityStatus` XHR from `/std/lecture` MUST continue only as a reviewed read-only route with `logging_token_reviewed:true`: aborting it triggers a blocking server-communication modal. One trusted `ensure_logged_in` runs before policy installation; subsequent navigation, including `/std/myLecture` re-entry, runs guarded. Unreviewed origins, paths, methods, redirects, data subresources, other logging/media/stream requests, and any request with a `Range` header (case-insensitive) MUST be denied before sending and abort sync without catalog publication. No host inferred from page content or user config is permitted. Mid-operation login expiration MUST fail closed without automatic re-login/retry.
- **FR-016** The system MUST retain one browser-session lock for network sync and perform lock-free local list reads; it MUST NOT read or write the later adapter's private ledger.
- **FR-017** An opt-in headless sync MUST refuse execution before browser or catalog access while compatibility is unverified and MUST NOT silently fall back to headed; ordinary sync remains headed by default. [NEEDS CLARIFICATION: CNU headless compatibility evidence.]
- **FR-018** The assignment list capability and public contract MUST be published together only when the metadata path has the approved `assignments.sync` policy pins; no assignment detail operation is part of PR2.

## Edge cases

- Two task IDs may be identical in different courses; they remain distinct, while duplicates in one course fail that course. Observed `TB_L_REPORT{digits}` task IDs have no entity-ID delimiter; foundation `assignment_entity_id` escapes delimiter-bearing components if the course ID ever requires it (`src/campusctl/identity.py:4-12`).
- A task row with a blank title retains an empty title as observed; a blank or **absent** native-ID attribute is not addressable and fails the entire course.
- A previously cached course absent from a partially successful full roster remains visible but stale; a successful full roster removes it.
- On null due, JSON reports null even if a downstream ledger retains an earlier due date.
- Stale warnings must survive an empty filtered result; busy sync must not be represented as a successful empty refresh.

## Success criteria

- **SC-001** Every fixture row with a valid unique `TB_L_REPORT{digits}` native ID, including visually hidden rows, appears exactly once with matching title/due/submitted fields; zero malformed-ID courses commit partial row sets. An empty tbody before successful `/api/v1/task/stdList` completion or while the page is busy never clears old rows; a successful zero-item data response with an idle, zero-row tbody does, even without a task-specific DOM empty marker or with an unrelated `#alarmList .table_dataBlank`.
- **SC-002** For every fixture merge transition, untouched or failed course rows remain byte-equivalent in content and fully successful courses replace all old rows; full-discovery failure preserves the prior timestamp.
- **SC-003** At narrow terminal width, 100% of displayed entity IDs appear as complete, independently selectable strings, and all stale-cache fixtures show a warning.
- **SC-004** Fixture interception records zero *sent* LMS Panopto script/connection-logging requests, media, stream, Range-header, or unpinned-origin requests. The three named Panopto requests are aborted with suppression diagnostics and sync continues; another unpinned page request denies the entire sync. An otherwise allowed GET with any case-variant `Range` header is denied while the same GET without it is permitted. Busy lock or unverified headless flag causes zero LMS requests.
- **SC-005** For every compatibility fixture, the JSON fields map one-to-one to the existing assignment upsert arguments and retain the same course/native-task entity ID; real SQLite action-state compatibility is a separate adapter cutover gate, not a claim of PR2.

## Out of scope

Assignment detail views, body/images/attachments, source packages, lecture scraping/playback, material download, scheduled notifications, external task-service operations, database migration, and edits to the external legacy notifier. PR2 verification uses fixture-only requests; headless behavior and provider effects remain separately gated.
