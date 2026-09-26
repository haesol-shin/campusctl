# Assignment metadata command contract

`campusctl` reads assignment metadata from the CNU LMS using a visible browser session and caches it separately from lectures. It does not open assignment details, download attachments or media, submit work, or access the downstream task ledger. The reviewed request policy is internal to the assignment command module; this public contract does not publish its route pins.

## Commands

```text
campusctl sync --only assignments [--course COURSE_ID] [--headless] [--json]
campusctl assignments list [--course COURSE_ID] [--json]
```

`sync` without `--only` still syncs lectures. Assignment sync runs for all enrolled courses unless filtered to an enrolled course ID. It holds the single non-blocking browser session lock and uses a visible session by default. `--headless` is refused with `headless-unavailable` before browser or catalog access until separately approved compatibility verification; it never falls back to headed execution. The list reads only `<data-dir>/catalog/assignments.json`, without configuration, session, lock, or network access. An unknown list course filter yields an empty assignment array; an unknown filtered sync course returns `course-not-found`.

Assignment sync enters each course before arming the task-list response wait around the task-menu click. The first post-commit task list, its task-page Referer, and the active topbar course must agree; an opaque encrypted request body is not treated as a plaintext course ID.

Human output is the default on a terminal; `--json` opts into the versioned JSON envelope. `CAMPUSCTL_OUTPUT=human` or `json` controls redirected output unless `--json` is supplied. The [general CLI contract](cli.md) describes output precedence and the shared envelope.

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

Successful sync `result` has `{"courses":2,"assignments":2,"failed_courses":[],"catalog":{"generated_at":"2026-09-25T10:00:00Z","enrollment_state":"known"},"suppressed_count":0,"suppressed_reasons":{}}`. Counts represent successfully refreshed courses and rows, not retained stale rows. Suppression diagnostics count only named reviewed requests aborted before network access and contain safe reason labels, never raw URLs. The nested catalog/cache timestamp marks the last merge, while envelope `generated_at` marks this response.

The `assignments.sync` guard permits exact LMS POST `/api/v1/week/getStdActivityStatus` XHR from `/std/lecture` as a reviewed read-only route, rather than suppressing it; aborting it displays a blocking server-communication modal. This one allowed request does not increment suppression diagnostics. Other unsuppressed logging-token paths remain denied.


## Stale cache and errors

A successful course replaces all its cached rows, including when an empty task list is confirmed by successful course-specific data completion and an idle empty table. An unaddressable assignment ID fails the **whole course**: its previous rows remain and `failed_courses` marks it stale. Other successful courses can still commit, yielding `partial`/exit 1. An incomplete request, busy page, or unrelated empty widget never clears assignments. A partial full sync retains formerly cached courses whose removal cannot yet be confirmed (`removal-deferred`). A full enrollment discovery failure preserves rows and the previous catalog timestamp but marks `enrollment_state` as `unknown`. Filtered sync changes only its selected course. List cache metadata exposes unknown enrollment and failures even when the filtered list is empty; human output warns about both.

| Status / exit | Assignment codes and action |
| --- | --- |
| `ok` / 0 | Complete sync or readable cached list |
| `partial` / 1 | `course-sync-failed`, `item-identity-missing`; failed courses retain old rows |
| `error` / 1 | `course-discovery-failed`, `policy-blocked`, `catalog-write-failed`; a denied request aborts without publishing a new catalog |
| `user-action` / 2 | `catalog-missing` (run `campusctl sync --only assignments`), `catalog-invalid`, `catalog-schema-unsupported`, `course-not-found`, `headless-unavailable`, invalid usage; login errors such as `login-action-required` require a new user-initiated run |
| `busy` / 75 | `session-busy`; no empty sync is reported |

Errors contain safe `code`, `message`, and nullable `remediation`. An unreviewed request, media or Range header fails closed under the reviewed internal policy; login expiration does not trigger an automatic retry. No live LMS verification or headless compatibility is claimed here.

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
