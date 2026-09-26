# Notice metadata contract

## Source and completeness

`campusctl sync --only notices [--course COURSE_ID] [--json]` visits each selected enrolled course through `/std/myLecture`, its session-course POST, `/std/lecture`, and `/std/notice`. Completed `/api/v1/board/notice/list/top` and `/api/v1/board/notice/list` JSON responses are the primary source; the first board table supplies a rendered row count, never detail requests. Every nondeleted board response item is included even when absent from global `/std/todo`. Matching to-do data preserves historical identity and supplies read state; unmatched board items have `is_unread: null`. An incomplete to-do grid leaves selected courses stale rather than silently changing old keys. A numeric page beyond 1, enabled Next, ordinary-list total above 10 or list length below total fails the entire course as `notice-board-paginated`. The rendered count must equal the deduplicated top+ordinary item count. Verified-empty requires success code 200, ordinary-list total 0 and empty list, empty top list, and zero rendered rows.

The session POST carries an opaque encrypted `e` value, so the course is verified from its successful response `body.data.course_id`, the active topbar course, and every board item's `course_id`; no plaintext request course ID is required. Its response body is captured before the next page navigation.

The request policy pins exact observed LMS methods and paths. It aborts optional Panopto scripts, the exact telemetry POST, observed college thumbnail PNG and LMS favicon icon; passive outside-origin GET assets are also suppressed. It continues only the exact reviewed LMS POST `/api/v1/week/getStdActivityStatus` XHR from `/std/lecture` as a read-only `notices.sync` route, avoiding a blocking server-communication modal. Other unsuppressed logging-token paths and unapproved requests remain fatal. The JSON envelope shape was reviewed from an owner-controlled live capture; implementation and fixture tests do not access live LMS. Headless sync is not approved.

## Commands

```text
campusctl sync --only notices [--course COURSE_ID] [--json]
campusctl notices list [--course COURSE_ID] [--json]
```

`list` reads `<data-dir>/catalog/notices.json` without network, configuration, browser or lock. Sync holds the shared exclusive browser-session lock. Filtering an unknown enrolled course at sync returns `course-not-found`; filtering an unknown course on list returns an empty list. `--json` selects a single versioned JSON envelope; otherwise `CAMPUSCTL_OUTPUT` selects human/JSON, and auto mode uses human on a TTY, JSON when redirected. A sync result reports freshly committed `courses`, `notices`, `failed_courses`, and `catalog` timestamp/enrollment state. Failed courses retain old rows; full discovery failure marks enrollment unknown while preserving old timestamp and rows; filtered discovery failure leaves the catalog untouched.

## Row fields and identity

Each `result.notices[]` row has `entity_id`, `legacy_key`, `course:{id,label}`, `kind:"notice"`, `title`, `date`, `status`, `is_unread`, `posted_date`, `author_role`, `author`, `view_count`, and `has_attachments`. The board response's `writeruser_name` becomes author, never an inferred author role. `boarditem_viewcnt` becomes an integer or `null`; reviewed numeric `file_yn` 0/1 becomes false/true (other or absent indicators remain `null`). `insert_dt` is a displayed day, while `insert_dt_addtime` supplies a date and time. Posting date and author role remain `null` unless separately observed. Unread is true/false only when a matching to-do row says `읽지않음`/`읽음`; otherwise `null`. Personal/contact response fields `writeruser_phone`, `writeruserno`, and `ref_user_no` never enter the catalog or output.

The legacy parser uses the trimmed displayed course label, date and ordinal number: `<label>_<date>_<number>`, preserving internal date whitespace. A native-ID match or a uniquely matching course/title/date-day row without a native ID supplies the original to-do timestamp and ordinal for byte-compatible `legacy_key` and `entity_id`. Ambiguous matches fail that course as `notice-identity-ambiguous`, retaining its old rows. The board DOM displays `insert_dt` (`YYYY-MM-DD`), but its observed `insert_dt_addtime` is `YYYY-MM-DD HH:MM`, which supplies the board-only identity date when valid; otherwise the displayed day is used. A board-only notice has no pre-existing to-do key to preserve. The native `TB_L_BOARDITEM{digits}` is a join key, not a substitute public identity. Treat the opaque entity ID as indivisible.

## Human and error output

The human list shows the course, title, displayed date, known read state only, author, view count, attachment presence and full untruncated ID on its own line. Unknown metadata is labeled `unknown`; unknown unread is not mislabeled read. Unknown enrollment and failed courses print stale warnings, including on an empty filtered list. JSON exposes `cache.generated_at`, `cache.enrollment_state`, `cache.failed_courses`, and all rows within the standard `{schema_version,tool,tool_version,status,result,errors,generated_at}` envelope. Errors use safe `{code,message,remediation}` without raw LMS bodies or URLs. `notice-board-paginated`, `notice-identity-ambiguous`, `item-identity-missing` and `course-sync-failed` are partial sync errors; `policy-blocked` is fatal, and `headless-unavailable` and `course-not-found` require user action.
