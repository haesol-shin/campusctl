# Assignment metadata command contract

`campusctl` reads assignment metadata from the CNU LMS using a browser session and caches it separately from lectures. Page requests are not filtered; campusctl verifies the selected course and assignment identities before publishing records. It does not submit work or access the downstream task ledger.

## Commands

```text
campusctl [--headless|--headed] sync --only assignments [--course COURSE_ID] [--json]
campusctl assignments list [--course COURSE_ID] [--json]
campusctl [--headless|--headed] assignments fetch ENTITY_ID... [--out DIR] [--json]
```

Bare `sync` refreshes all four metadata domains in one combined pass; `--only assignments` limits it to assignments. The pass selects each course once, enters its task section through the rendered menu, and lets its page requests proceed normally. Assignment sync holds the single non-blocking browser lock, uses headed mode by default and supports global `--headless` with local Chromium; CDP headless returns `headless-unavailable`. `list` reads only `<data-dir>/catalog/assignments.json` without browser/network/lock; `--refresh` runs assignment sync first. Human `--course` accepts a full ID, last-printed course number or unique name fragment; JSON requires a full ID. Unknown filters return `course-not-found`.

Assignment sync enters each course before arming the task-list response wait around the task-menu click. The first post-commit task list, its task-page Referer, and the active topbar course must agree; an opaque encrypted request body is not treated as a plaintext course ID. Readiness waits for the committed task route, matching topbar and task table, then for the completed list response and the rendered rows to match its count. A missing response or count mismatch times out rather than publishing an empty course.

Human output is the default on a terminal; `--json` opts into the versioned JSON envelope. `CAMPUSCTL_OUTPUT=human` or `json` controls redirected output unless `--json` is supplied. The [general CLI contract](cli.md) describes output precedence and the shared envelope.

## Selected detail fetch

`fetch` accepts one or more explicit full entity IDs from the cached `assignments list`, deduplicated in first-seen order. List numbers, globs, a whole course and `--course` are not selections. A missing catalog or any unknown ID fails before browser startup. One distinct ID returns `result.source_package`. More than one returns `result.items` with `completed`, `partial`, `failed` or `not-started` outcomes; only the first two carry `source_package`, and `partial` and `failed` carry `reason_code`. Fetch opens each selected course and assignment through the ordinary UI, verifies their identities, and packages readable text; it does not submit work or change submission state. One session authenticates once and continues after an item failure. A session failure stops the queue. There is no retry. Local headed and headless Chromium support assignment fetch; CDP headless returns `headless-unavailable` before startup. It holds the non-blocking session lock. `--out DIR` chooses a **nonexistent package directory** for one ID, not a parent folder; more than one distinct ID with `--out` is a `usage-error`, and an existing destination fails with `output-path-conflict`.

For each selected ID, including later IDs in one session, fetch navigates back to the roster before selecting its course. It waits for the roster link, matching course entry and task table, then for both task-detail responses and a visible detail title. The task-detail response must match the selected course and task. The submission-record response must also match, except that an unsubmitted assignment may have an explicitly null `report_no` and null or empty `course_id`; both responses must still report success. A shell or unrelated asset response cannot establish the selected task identity; any nonempty mismatch aborts before packaging.

Without `--out`, packages live under `<data-dir>/sources/assignment/<sha256-of-entity-id>/<package-digest>/`. Each package contains `package.json` and `content.md`, plus included `images/` and `attachments/` as applicable. A verified identical package may be reused; an explicit `--out` never overwrites. `package.json` records schema version, selected ID, kind, course, query-free source reference, retrieval time, content path, completeness and included/omitted resources. Included resource records carry deterministic `resource_id`, kind, relative path, normalized source reference, original name, media type, size and SHA-256; omitted references carry ID, source reference, original name, media type and reason. Unsupported/external/media references are omitted, not fetched. Failed transfer or identity verification publishes no package.

One distinct ID returns `result.source_package` with `entity_id`, `completeness`, absolute `path`, `manifest_path`, `content_path`, included `resources` (with absolute paths), `omitted_resources`, and `provenance` (`provider`, `course_id`, `source_ref`, `retrieved_at`) in the [standard envelope](cli.md). Human output for that ID prints `Assignment source`, `Package`, `Content`, `Completeness`, and one `Omitted` line per skipped reference. A complete single package is `ok`/0; any omissions are `partial`/1 with `resource-omitted` and a usable package. A multi-ID request is `ok`/0 only when every item is `completed`; any published package with a partial, failed or not-started item is `partial`/1; otherwise the envelope uses the first failure's status and keeps every item and ordered error. Unknown ID, missing/invalid catalog, output conflict, CDP headless or login action is `user-action`/2; operational capture/transfer failure is `error`/1; locked browser session is `busy`/75. Errors identify the failing fetch step without leaking request details.


## JSON results

A successful `campusctl assignments list --json` has this shape (synthetic IDs and illustrative timestamps):

```json
{
  "schema_version": 1,
  "tool": "campusctl",
  "tool_version": "<released-version>",
  "status": "ok",
  "result": {
    "cache": {
      "generated_at": "2026-09-25T10:00:00Z",
      "path_present": true,
      "enrollment_state": "known",
      "failed_courses": []
    },
    "assignments": [
      {
        "entity_id": "cnu_assignment:course-a:TB_L_REPORT101",
        "task_id": "TB_L_REPORT101",
        "course": {"id": "course-a", "label": "Synthetic Course A"},
        "kind": "assignment",
        "title": "Draft report",
        "due_date": "2026-10-02 23:59",
        "is_submitted": true
      },
      {
        "entity_id": "cnu_assignment:course-b:TB_L_REPORT202",
        "task_id": "TB_L_REPORT202",
        "course": {"id": "course-b", "label": "Synthetic Course B"},
        "kind": "assignment",
        "title": "Open task",
        "due_date": null,
        "is_submitted": false
      }
    ]
  },
  "errors": [],
  "generated_at": "2026-09-25T10:00:01Z"
}
```

Both submitted and unsubmitted rows appear. `entity_id` is a full, selectable opaque identity; use the separate `course.id` and native `task_id` fields when integrating with a downstream adapter, not substrings of the entity ID. `due_date` preserves displayed LMS text after trimming, without timezone conversion; absent due text is `null`. `is_submitted` is Boolean.

Successful sync `result` has `{"courses":2,"assignments":2,"failed_courses":[],"catalog":{"generated_at":"2026-09-25T10:00:00Z","enrollment_state":"known"}}`. Counts represent successfully refreshed courses and rows, not retained stale rows. The nested catalog/cache timestamp marks the last merge, while envelope `generated_at` marks the report.

## Stale cache and errors

A successful course replaces all its rows, including a confirmed empty task list. Missing native identity fails the whole course: previous rows stay stale and `failed_courses` identifies the course. An incomplete response, mismatched rendered rows or unrelated empty widget never clears assignments. Other courses and domains continue after a domain-specific failure; partial full sync defers removal of previously cached courses. Discovery failure retains rows and previous timestamp while marking enrollment unknown. Errors name the domain and failing step, without exposing request data.

| Status / exit | Assignment codes and action |
| --- | --- |
| `ok` / 0 | Complete sync or readable cached list |
| `partial` / 1 | `course-sync-failed`, `item-identity-missing`; failed courses retain old rows |
| `error` / 1 | `course-discovery-failed`, `catalog-write-failed`; unverified course identities do not publish new rows |
| `user-action` / 2 | `catalog-missing` (run `campusctl sync --only assignments`), `catalog-invalid`, `catalog-schema-unsupported`, `course-not-found`, `headless-unavailable`, invalid usage; login errors such as `login-action-required` require a new user-initiated run |
| `busy` / 75 | `session-busy`; no empty sync is reported |

Errors contain safe `code`, `message`, and nullable `remediation`; operational messages name the assignment step that failed without including raw request URLs. Login expiration does not trigger an automatic retry.

## Human sample

```text
$ campusctl sync --only assignments
Synced 2 courses, 2 assignments.

$ campusctl assignments list
2 assignments (updated 2026-09-25 10:00)

Synthetic Course A
  Draft report — Submitted — Due: 2026-10-02 23:59
    cnu_assignment:course-a:TB_L_REPORT101

Synthetic Course B
  Open task — Not submitted — Due: —
    cnu_assignment:course-b:TB_L_REPORT202
```

At narrow terminal widths, complete IDs remain on their own lines rather than being truncated or split. Stale output adds `Warning: assignment cache is stale; enrollment is unknown.` or `Warning: assignment cache is stale for <label> (<id>): <reason>.`, including for empty filtered results.
