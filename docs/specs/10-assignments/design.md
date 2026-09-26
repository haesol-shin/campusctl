# Assignments technical design (PR2)

Implements [spec.md](spec.md) under the [constitution](../constitution.md). Evidence `plan:N` refers to the owner-held sanitized LMS source plan dated 2026-09-25, line N. Repository references below are file:line ranges; legacy notifier expectations are read-only owner-held sanitized evidence. Shared catalog, UI policy, and CLI hooks are specified in [Foundation design](../00-foundation/design.md).

## Commands, results, and rendering (FR-001–003, FR-005–006, FR-010–013, FR-017)

- `campusctl sync --only assignments [--course <course-id>] [--headless] [--json]`: metadata only. PR1 retains lecture default and reserves `--headless` for registered non-playback domains. Headed is default; until independently cleared, `--headless` returns `headless-unavailable` before session/catalog access. Configured CDP does not become headless by flag. `campusctl assignments list [--course <course-id>] [--json]` reads only local cache, not config/browser/lock. Unknown list course filter returns `[]`; unknown filtered sync course returns `course-not-found`. JSON output opt-in for agents; human on TTY; preserve output-mode precedence [`src/campusctl/cli.py:89-92,300-308,437-478`; `docs/contracts/cli.md:22-24`; plan:23-29,176-177].
- Full list envelope, illustrative timestamps/version:

```json
{
  "schema_version": 1,
  "tool": "campusctl",
  "tool_version": "<released-version>",
  "status": "ok",
  "result": {
    "cache": {"generated_at": "2026-09-25T10:00:00Z", "path_present": true, "enrollment_state": "known", "failed_courses": []},
    "assignments": [
      {"entity_id": "cnu_assignment:<course-id>:TB_L_REPORT{digits}", "task_id": "TB_L_REPORT{digits}", "course": {"id": "<course-id>", "label": "<course-label>"}, "kind": "assignment", "title": "<assignment-title>", "due_date": "2026-09-28 23:59", "is_submitted": true},
      {"entity_id": "cnu_assignment:<other-course-id>:TB_L_REPORT{digits}", "task_id": "TB_L_REPORT{digits}", "course": {"id": "<other-course-id>", "label": "<other-course-label>"}, "kind": "assignment", "title": "<another-assignment-title>", "due_date": null, "is_submitted": false}
    ]
  },
  "errors": [],
  "generated_at": "2026-09-25T10:00:01Z"
}
```

- Sync's `result` is `{"courses":2,"assignments":2,"failed_courses":[],"catalog":{"generated_at":"2026-09-25T10:00:00Z","enrollment_state":"known"}}`; counts include newly committed courses/rows only. On partial sync, `failed_courses` has `{course_id,label,reason}`; list retains cached stale rows with `cache.failed_courses`. `generated_at` inside `cache`/`catalog` is merge time, envelope time is response time [plan:44-74; `src/campusctl/envelope.py:32-47`].
- Human sample (separate invocations):

```text
$ campusctl sync --only assignments
Synced 2 courses, 2 assignments.

$ campusctl assignments list
2 assignments (updated 2026-09-25 10:00)

<course-label>
  <assignment-title> — Submitted — Due: 2026-09-28 23:59
    cnu_assignment:<course-id>:TB_L_REPORT{digits}
<other-course-label>
  <another-assignment-title> — Not submitted — Due: —
    cnu_assignment:<other-course-id>:TB_L_REPORT{digits}
```

No ID truncation or splitting at narrow widths. Display due text exactly after trim (null → `—`), with no timezone conversion. Unknown enrollment warning: `Warning: assignment cache is stale; enrollment is unknown.` Failed/deferred course warning: `Warning: assignment cache is stale for <label> (<id>): <reason>.` Include warnings on empty results. Domain renderer must never use lecture-specific `_sync` or pending-only filter [`src/campusctl/presentation.py:380-447,508-523,650-688`; plan:169].

### Errors and exits (FR-007–012, FR-017)

| Status/exit | Codes and behavior |
| --- | --- |
| `ok`/0 | Complete sync or readable list. |
| `partial`/1 | `course-sync-failed` or `item-identity-missing` for any course; retain all rows of affected course, report safe course label/ID only. |
| `error`/1 | `course-discovery-failed` on failed full enrollment discovery (preserve rows/timestamp and mark enrollment unknown), `policy-blocked` for any unlisted request or case-insensitive `Range` header (abort the entire sync before catalog publication; do not downgrade to per-course partial), and existing `catalog-write-failed` for atomic-write failure. |
| `user-action`/2 | `catalog-missing` on list with remediation `Run 'campusctl sync --only assignments' to create it.`; `catalog-invalid`, `catalog-schema-unsupported`, `course-not-found` on filtered sync, invalid usage, and `headless-unavailable` before headless evidence. The last uses remediation `Use a visible local browser or a separately configured browser endpoint until headless LMS support is verified.` No silent headed fallback. The trusted pre-guard login uses existing login errors; if login expires mid-operation, fail closed with `login-action-required` or the existing login error, no automatic login/retry; user reruns. Unapproved/missing UI policy evidence must not enable the domain; direct policy construction rejects it as user-action. |
| `busy`/75 | Existing `session-busy` when browser lock held; not an empty successful sync. |

Use safe `{code,message,remediation}` entries, nullable remediation and existing envelope/status mapping [`src/campusctl/envelope.py:11-47`; `docs/contracts/cli.md:112-144`; `tests/test_sync.py:395-418`; plan:36-46].

## Row extraction and legacy mapping (FR-003–007, FR-014)

Navigate enrolled course through ordinary course-row/menu UI before `/std/task`; never synthesize direct context navigation. Observe attached task table and rows, including hidden rows. Select candidate assignments as `#table_list tr` containing `a[data-act="detail"]` **without** `[data-id]` in the selector; read `data-id` afterward and fail the **whole course** if the attribute is absent, blank after trim, duplicated, or does not match `TB_L_REPORT{digits}` (format placeholder, not a real ID). The legacy parser's `a[data-act="detail"][data-id]` selector silently drops absent-ID links; PR2 must not do so. For valid candidates: `task_id` = trimmed `data-id`; `title` = trimmed nested `strong.innerText` or link text; `due_date` = trimmed substring after **last** `" ~ "` in containing `td`, null if absent/empty; `is_submitted` = complete badge present **or** status `td` starts with `완료`, else false. Preserve null/trimmed source due text, not lecture's normalized datetime. Course ID/label come from discovered course records; no partial course commit. Owner-held sanitized 2026-09-25 task evidence shows `<table class="table mb-0" data-page-size="10" id="table_list"><tbody id="tbody"></tbody></table>`; a snapshot of this empty tbody alone does not prove the task data has loaded. A sanitized report §4.1 claimed `#todoListNoData` with `확인할 알림이 없습니다.`, but the reviewed task HTML lacks this node: DO NOT require it. `#alarmList .table_dataBlank` is an unrelated notification widget, never a task-empty signal. The exact task DOM empty marker remains a non-blocking clarification (owner-held sanitized task HTML and empty-state evidence, 2026-09-25).

For an empty course, wait for the successful course-specific `POST /api/v1/task/stdList` XHR completion, then the page to become idle, then observe zero rows in `#table_list tbody#tbody`. Prefer the completed response body's row count as the source of truth when accessible: zero plus idle zero-row DOM succeeds; nonzero or malformed response paired with zero DOM is an incomplete render/course failure, not empty. If response count is unavailable, a successful response plus idle zero-row DOM suffices. A `tbody#tbody` that starts empty or is sampled while `stdList`/another page AJAX request is pending never proves emptiness. If the response fails or successful completion and idle DOM cannot be established by the bounded wait, fail this course (`course-sync-failed`), keeping all prior rows. Track the course-specific XHR response and outstanding page requests; do not rely on a fixed sleep or unrelated alarm-widget message (owner-held sanitized LMS task/list evidence and owner decision, 2026-09-25; `src/campusctl/browser.py:19-31`).

Identity is exactly `cnu_assignment:<course_id>:<task_id>`, constructed by foundation `assignment_entity_id`; the native `task_id` comes from `a[data-act="detail"][data-id]` in `TB_L_REPORT{digits}` format and is separately exposed, never recovered by parsing the opaque ID. Course IDs come from `[data-act="moveLecture"][data-courseid]` with observed structural format `{YEAR}{TERM}{GROUP}{SUBJECT_CD}-{CLASS_NO}` (placeholders only), not from course labels. A later legacy notifier adapter passes `course_id=row["course"]["id"]`, `course_name=row["course"]["label"]`, `task_id=row["task_id"]`, `title=row["title"]`, `due_date=row["due_date"]`, `is_submitted=row["is_submitted"]` to its existing assignment upsert. That API stores `source_id=cnu_assignment`, `kind=assignment`, natural key `<course_id>:<task_id>`, entity title, payload `course_name` and due date (coalescing prior due on null), task-registration PENDING/DONE and task-sync PENDING/SKIPPED. PR2 does **not** access that DB; the adapter remains separate (owner-held sanitized legacy mapping evidence, 2026-09-25; `src/campusctl/identity.py:4-12`; plan:74,152-164).

## Catalog, merge, lock, and request boundary (FR-008–010, FR-014–018)

Separate private `<data-dir>/catalog/assignments.json` schema 1: top-level `schema_version`, UTC RFC3339 `generated_at`, `enrollment_state: known|unknown`, normalized `courses` (`course_id,label,class_no`), `failed_courses:[{course_id,label,reason}]` with `reason: course-sync-failed|item-identity-missing|removal-deferred`, and `assignments` array as shown. Use foundation `read_domain_catalog`, `write_domain_catalog`, `merge_domain_catalog`, `mark_enrollment_unknown`; no alteration to lecture catalog schema. Writes use private directory, sibling temp file, UTF-8, flush/fsync, atomic replacement and parent fsync. Network sync holds existing exclusive session lock (default `<data-dir>/session.lock`, configurable `browser.lock_path`) over browser and writes; list reads atomically replaced file without lock [`src/campusctl/catalog.py:17-18,27-55,58-115,118-145`; `src/campusctl/browser.py:286-295`; plan:16,44-46,150,178].

Full successful roster discovery sets enrollment known; each complete course replaces its records, including verified zero rows, and clears its marker. Failed/unaddressable courses keep previous rows, marked stale. A fully successful unfiltered sync removes unenrolled courses; partial full sync preserves formerly cataloged absent courses as `removal-deferred`. Filtered sync touches only target rows/marker, leaving other records/markers/enrollment unchanged. Full discovery failure sets unknown while preserving every row and old `generated_at` (or leaves catalog absent), and emits `course-discovery-failed` [plan:44-46; `../00-foundation/design.md:31-58`].

The **command module** `src/campusctl/commands/assignments.py` owns the sole policy pins in `CAPABILITY["policy"]`; PR2 depends on the PR1.1 `ui_policy.py` interceptor cutover, not an extra provider-owned allowlist. `L = https://dcs-learning.cnu.ac.kr`. Each route is a distinct `{origin,path,operation,methods}` record with `origin=L`, `operation="assignments.sync"` and the shown single method; the two properties XHR routes also carry the PR1.1 exact `query:{"_":"cachebuster"}` bound to observed `_` requests. `read_only_evidence` identifies the owner-held sanitized LMS report and owner decisions dated 2026-09-25, not raw capture content. The selected operation is list-only; no detail operation or C origin is permitted (owner-held sanitized report §§1.1, 2 and decisions §§2, 4, 2026-09-25; `src/campusctl/commands/__init__.py:11-13,27-83`; PR1.1 query contract in `../00-foundation/design.md`).

```python
L = "https://dcs-learning.cnu.ac.kr"


def pin(path, method, query=None):
    route = {"origin": L, "path": path, "operation": "assignments.sync", "methods": [method]}
    if query is not None:
        route["query"] = query
    return route


def suppress(name, path_template, method, reason):
    return {
        "name": name,
        "origin": L,
        "path_template": path_template,
        "operation": "assignments.sync",
        "methods": [method],
        "reason": reason,
    }


CAPABILITY = {
    "commands": ["list"],
    "policy": {
        "approved": True,
        "read_only_evidence": "2026-09-25 sanitized LMS report §§1.1,2,4.1,5 and owner LMS pins decision",
        "origins": [L],
        "routes": [
            pin("/std/myLecture", "GET"),
            pin("/std/lecture", "GET"),
            pin("/api/v1/course/addSessionCourseInfo", "POST"),
            pin("/std/task", "GET"),
            pin("/api/v1/task/stdList", "POST"),
            pin("/api/v1/user/getUserInfo", "POST"),
            pin("/api/v1/user/getMenuList", "POST"),
            pin("/api/v1/alarm/getAlarmListByDate", "POST"),
            pin("/api/v1/course/getCeShortcuts", "POST"),
            pin("/api/v1/course/get", "POST"),
            pin("/api/v1/common/checkEnableUrl", "POST"),
            pin("/api/v1/boardM/getBoardItemList", "POST"),
            pin("/api/v1/term/getYearTermList", "POST"),
            pin("/api/v1/course/getStdMyCourseList", "POST"),
            pin("/api/v1/board/courseNotice/list", "POST"),
            pin("/api/v1/week/getStdWeekList", "POST"),
            pin("/api/v1/week/getStdEtcList", "POST"),
            {
                **pin("/api/v1/week/getStdActivityStatus", "POST"),
                "logging_token_reviewed": True,
                "resource_type": "xhr",
            },
            pin("/api/v1/survey/getApplyPopList", "POST"),
            pin("/api/v1/board/popup/noticeList", "POST"),
            pin("/properties/messages.properties", "GET", {"_": "cachebuster"}),
            pin("/properties/messages_ko.properties", "GET", {"_": "cachebuster"}),
        ],
        "suppress": [
            suppress("panopto-script", "/js/common/panopto-{hash}.js", "GET", "media-integration"),
            suppress("panopto-disconnection-log", "/api/v1/panopto/addInternetDisconnectionLog", "POST", "logging"),
            suppress("panopto-connectivity-check", "/api/v1/panopto/checkInternetConnection", "GET", "logging"),
        ],
        "static_asset_origins": [L],
        "static_resource_types": ["script", "stylesheet", "font", "image"],
        "selected_file_routes": [],
        "allowed_media": [],
        "max_bytes": None,
    },
}
```
`pin` and `suppress` above are presentation-only shorthand; the runtime module materializes the route and suppression dictionaries. The observed two messages properties XHRs carry exactly one `_` cachebuster; `query:{"_":"cachebuster"}` constrains it to 1–20 digits and denies duplicates/other names. All other assignments data routes deny query/fragment. Static assets require same-origin GET and matching resource type, never media/video extension; XHR/fetch/document data always match a listed path/method and any explicit query constraint. Owner decision 3 permits only exact LMS POST `/api/v1/week/getStdActivityStatus` XHR from `/std/lecture` as a read-only `assignments.sync` route with `logging_token_reviewed:true`: aborting it triggers a blocking server-communication modal. Other unsuppressed logging-token paths are denied. The suppressed script template `{hash}` is one nonempty safe `[A-Za-z0-9_-]+` path segment. Suppressions abort matching requests before network and increment `UiRequestDiagnostics.suppressed_count`/`suppressed_reasons`; approved routes return `"allow"` and unexpected requests raise `UiRequestDenied` without partial-course publication. Allowed file media remains empty and `max_bytes` null because this operation retrieves metadata only. Suppression behavior is documented in owner-held sanitized LMS evidence dated 2026-09-26.

Call existing `ensure_logged_in` **once before installing the interceptor** as the trusted authentication path outside operation policy (no auth endpoints in the per-operation pins). PR1.1 makes `enter_course_section(page,config,course_id,section="task")` navigation-only on that already authenticated page, removing its current internal login call (`src/campusctl/providers/cnu/course_context.py:21-50`); PR2 never edits that shared file. Construct `UiRequestPolicy.from_reviewed_config(CAPABILITY["policy"])`, then `install_ui_request_interceptor(page_or_context, policy, operation="assignments.sync", diagnostics=diagnostics)` before first operation navigation, including reused browser contexts. Guard GET `/std/myLecture` for roster/course re-entry and every other operation request. Its handler passes complete request headers, resource type and `redirected_from`; at the end call `interceptor.raise_if_denied()` before publication and `await interceptor.close()` in cleanup. A mid-operation expired session's redirect/login navigation is denied as `redirect`/`route`; fail closed with `login-action-required` or existing login error, without automatic re-login/retry. All other unpinned requests fail `policy-blocked`; no catalog publication. Enforce case-insensitive `Range`, media/stream, redirect and origin checks even on otherwise pinned routes/static assets. The `{}` versus `{"rAnGe":"bytes=0-1023"}` request-header fixture pair uses the same approved GET; the second must be denied without catalog publication. PR4's selected-file/response guard is not a PR2 prerequisite (PR1.1 contract in `../00-foundation/design.md`; `src/campusctl/providers/cnu/course_context.py:21-50`, `src/campusctl/providers/cnu/ui_policy.py:124-163`; owner-held sanitized authentication decision, 2026-09-25).

## Module layout and FILE OWNERSHIP (FR-001–002, FR-015–018)

- **PR1/PR1.1-owned, consumed unchanged:** `src/campusctl/domain_catalog.py` (`Domain`, `domain_catalog_path(domain,root=None)`, `read_domain_catalog(domain,path=None)`, `write_domain_catalog(domain,value,path=None)`, `merge_domain_catalog(domain,previous,roster,rows,*,successful_course_ids,failed_courses,selected_course_id=None)`, `mark_enrollment_unknown(domain,root)`); `src/campusctl/identity.py` (`assignment_entity_id(course_id,task_id)`); `src/campusctl/providers/cnu/course_context.py` (`enter_course_section(page,config,course_id,section="task")`); PR1.1 `src/campusctl/providers/cnu/ui_policy.py` (`UiRequestPolicy.from_reviewed_config`, `guard_ui_request(..., selected_file=None) -> Literal["allow","suppress"]`, `install_ui_request_interceptor`, `UiRequestDiagnostics`, `UiRequestInterceptor.raise_if_denied`/`close`); `src/campusctl/commands/__init__.py` (`discover_domain_modules()`); generic CLI/parser and renderer dispatch. Existing source anchors: `src/campusctl/domain_catalog.py:15-28,65-80,228-238`, `src/campusctl/identity.py:4-12`, `src/campusctl/providers/cnu/course_context.py:12-50`, `src/campusctl/commands/__init__.py:86-110`; PR1.1 interface: [Foundation design](../00-foundation/design.md).
- **PR2 new files, disjoint ownership:** `src/campusctl/providers/cnu/assignments.py`: `parse_assignment_rows`, DOM extraction, `sync_assignments(config,root,course_id=None,*,headless=False,reviewed_policy)`; calls trusted `ensure_logged_in` once before policy installation, shared navigation-only course context/identity/catalog and the PR1.1 UI interceptor with operation pinned in domain policy and existing `open_session`. `src/campusctl/commands/assignments.py`: sole `CAPABILITY` with approved list-only policy shown above, `register(subparsers)`, `async sync(config,root,course_id,*,headless=False)` passing `CAPABILITY["policy"]` to provider, `dispatch(args)` for local list, and `render(command,result,width)`. No external legacy notifier imports.
- **Zero shared-file edits in PR2:** Foundation `discover_domain_modules()` uses sorted `pkgutil.iter_modules` under `campusctl.commands` and imports modules; a helper with none of `register`, `dispatch`, `render`, `sync`, `CAPABILITY` is ignored, an explicitly unapproved policy module is skipped, and partial hooks or malformed capability raise at discovery rather than silently hiding a broken implementation. With all four hooks and a validated approved `CAPABILITY`, foundation `cli.py` automatically registers `assignments` parsing/dispatch, routes sync to `asyncio.run(module.sync(config,root,course_id,headless=args.headless))`, and composes public capability metadata. Foundation `presentation.py` automatically routes internal `sync.assignments`/`assignments.list` to `assignments.render`; PR2 touches neither shared file. `register` adds nested `EnvelopeArgumentParser`, `dest="assignments_command"`, and domain/leaf `--json` with `default=argparse.SUPPRESS`. PR2 publishes only `docs/contracts/assignments.md` with commands, schema, stale semantics, errors, policy and human output, together with an approved discovered module. No edit to `docs/contracts/cli.md`; PR1 owns its index pointer. PR5 adds `fetch` only after its separate gate [plan:168-170; `../00-foundation/design.md:111-140`].
- **PR2 test ownership:** `tests/test_assignments_provider.py`, `tests/test_assignments_cli.py`, `tests/test_assignments_compat.py`, `tests/test_assignments_integration.py`, plus `tests/fixtures/assignments/rows.html`, each owned by the lane in [tasks.md](tasks.md). No edits to the external legacy notifier. Shared-file integration is a sequential boundary after provider and command files are ready.

## Fixture acceptance strategy (FR-003–018; SC-001–005)

Reuse read-only behavioral expectations from owner-held sanitized legacy notifier test evidence dated 2026-09-25 (line ranges below refer to the held test snapshot, not a public checkout):

| Legacy behavior in held test snapshot | Lines | PR2 use |
| --- | --- | --- |
| Parse assignment rows with missing task IDs | 284–309 | ID/title/due/status normalization; replace legacy silent ID drop with whole-course failure for both empty and **absent** `data-id`. |
| Extract task-table rows and completion status | 391–426 | Task-table identity and submitted states. |
| Wait for assignment rows and handle empty tables | 428–464 | Bounded waits; strengthen empty success to successful per-course `stdList` XHR plus idle zero-row tbody, not an unrelated DOM marker. |
| Map classroom rows to assignment ledger entries | 474–508 | Assignment data only; do not reuse lecture scrape. |
| Include collapsed-week rows | 511–523 | Hidden-row technique; add hidden **assignment** row tested with `state="attached"`. |
| Handle missing task link/table | 537–571 | Missing menu/table now fails course and preserves cache. |
| Bound stalled row extraction | 618–628 | Bounded DOM evaluation. |
| Persist pending/completed assignment actions and coalesce absent due dates | 133–174, 204–217 | Pin PR2's native-field/identity golden expectations; later adapter PR pre-seeds isolated SQLite and verifies real action states before cutover. Never mutate production DB. |

Ledger isolation for the **later adapter gate** is established by owner-held sanitized legacy test evidence (2026-09-25, fixture setup lines 37–43); PR2's permanent tests compare catalog rows to golden upsert arguments without importing the external notifier. Campusctl fake-session/catalog patterns: `tests/test_sync.py:64-158,161-192,221-307,310-418`; output patterns: `tests/test_list_commands.py`, `tests/test_cli_output.py`, `tests/test_presentation.py`; parser patterns: `tests/test_cnu_parsers.py:7-19,21-87` [plan:180-192].

Add sanitized scenario fixtures for null due, badge/text submitted forms, unknown status, hidden rows, duplicate/blank/malformed `TB_L_REPORT{digits}` native IDs, a candidate `a[data-act="detail"]` **without** any `data-id` attribute beside a valid row, and empty `#table_list > #tbody` both before/after a successful course-specific `stdList` response. Test pending AJAX, unsuccessful response, page-idle zero-row DOM, missing table and unrelated `#alarmList .table_dataBlank`; **do not** fabricate a required `#todoListNoData` task marker. Also cover timeouts, roster failure preserving timestamp, filtered/full/partial/deferred merges, removal only after complete pass, domain isolation, busy lock/75, and headless refusal before browser/catalog access. Absent-ID or malformed-ID fixtures MUST fail the whole course and preserve cached valid rows; only a successful zero-item `stdList` response plus idle zero-row DOM replaces old rows. Verify JSON/human narrow-width full IDs, stale warnings and assignment-only counts. Exercise **all** pinned bootstrap/task/background routes (both messages properties routes with `_` cachebuster, malformed/duplicate/unlisted query rejected), GET same-origin static assets, named Panopto suppression (`UiRequestDiagnostics.suppressed_count`/`suppressed_reasons`, zero sent requests), `panoptoSaml-...` not suppressed, an unpinned background request denying the *whole* sync, Range-case variant and redirect/origin/media denials. `interceptor.raise_if_denied()` before catalog write must catch asynchronously denied requests; `await interceptor.close()` tears down route. Assert observable network/catalog records and errors, not mock forwarding (owner-held sanitized LMS report, task/list evidence and owner decisions, 2026-09-25).

## Evidence and release gate (FR-014–018)

The owner-approved evidence supplies native task ID attributes, exact reviewed `assignments.sync` routes/methods, benign background requests, and named Panopto suppressions. Owner-held sanitized task HTML dated 2026-09-25 proves the initially empty `tbody#tbody` but **not** a task-specific empty marker; successful course-specific `stdList` response plus idle zero-row tbody is sufficient (marker identity remains non-blocking unknown). Implement and publish list-only policy after PR1.1; no live LMS use in PR2 implementation or CI. Headless LMS SSO remains unverified and `--headless` refuses before browser/catalog access until its separately approved post-PR2–4 compatibility check. No detail operation is enabled here (owner-held sanitized report, 2026-09-25, §§1.1, 2.2, 3.2, 4.1, 5; sanitized task/list evidence and owner decisions, 2026-09-25).
