# Notice metadata contract

## Source and completeness

Bare `sync` collects all four domains in one combined pass; `--only notices` narrows it. The pass selects each course once and visits its board through the rendered notice menu. Completed `/api/v1/board/notice/list/top` and `/api/v1/board/notice/list` JSON responses are primary; the first board table supplies a rendered row count. Every nondeleted board item appears even if absent from global `/std/todo`. Matching to-do rows preserve historical identity and supply read state; unmatched items have `is_unread: null`. An incomplete to-do grid leaves affected courses stale rather than silently changing keys. A numeric page beyond 1, enabled Next, ordinary total above 10, or list length below total fails the course as `notice-board-paginated` and retains its previous rows.

The session-course POST carries an opaque encrypted value and can navigate immediately. Course identity comes from the committed page, topbar and board items, not the POST body. The notice board opens via the rendered menu; a direct `/std/notice` navigation can redirect to the home page. Page requests are unfiltered. Notice fetch is not registered; until an attachment-bearing notice is observed, its design records attachments as omissions without transfer. Global `--headless` supports notice sync with local Chromium; CDP headless returns `headless-unavailable`.

## Commands

```text
campusctl sync --only notices [--course COURSE_ID] [--json]
campusctl notices list [--course COURSE_ID] [--refresh] [--json]
```

`list` reads `<data-dir>/catalog/notices.json` without network, configuration, browser or lock; `--refresh` first syncs this domain. Human `--course` accepts exact ID, last-printed course number or unique name fragment; JSON requires exact ID. Unknown filters return `course-not-found`. A sync result reports freshly committed `courses`, `notices`, `failed_courses`, catalog timestamp and enrollment state. Failed courses retain old rows while other courses and domains continue. Full discovery failure preserves prior rows/timestamp and marks enrollment unknown; filtered failure leaves unrelated rows untouched. Errors name the failed domain and step.

## Row fields and identity

Each `result.notices[]` row has `entity_id`, `legacy_key`, `course:{id,label}`, `kind:"notice"`, `title`, `date`, `status`, `is_unread`, `posted_date`, `author_role`, `author`, `view_count`, and `has_attachments`. The board response's `writeruser_name` becomes author, never an inferred author role. `boarditem_viewcnt` becomes an integer or `null`; reviewed numeric `file_yn` 0/1 becomes false/true (other or absent indicators remain `null`). `insert_dt` is a displayed day, while `insert_dt_addtime` supplies a date and time. Posting date and author role remain `null` unless separately observed. Unread is true/false only when a matching to-do row says `읽지않음`/`읽음`; otherwise `null`. Personal/contact response fields `writeruser_phone`, `writeruserno`, and `ref_user_no` never enter the catalog or output.

The legacy parser uses the trimmed displayed course label, date and ordinal number: `<label>_<date>_<number>`, preserving internal date whitespace. A native-ID match or a uniquely matching course/title/date-day row without a native ID supplies the original to-do timestamp and ordinal for byte-compatible `legacy_key` and `entity_id`. Ambiguous matches fail that course as `notice-identity-ambiguous`, retaining its old rows. The board DOM displays `insert_dt` (`YYYY-MM-DD`), but its observed `insert_dt_addtime` is `YYYY-MM-DD HH:MM`, which supplies the board-only identity date when valid; otherwise the displayed day is used. A board-only notice has no pre-existing to-do key to preserve. The native `TB_L_BOARDITEM{digits}` is a join key, not a substitute public identity. Treat the opaque entity ID as indivisible.

## Human and error output

The human list shows the course, title, displayed date, known read state only, author, view count, attachment presence and full untruncated ID on its own line. Unknown metadata is labeled `unknown`; unknown unread is not mislabeled read. Unknown enrollment and failed courses print stale warnings, including on an empty filtered list. JSON exposes `cache.generated_at`, `cache.enrollment_state`, `cache.failed_courses`, and all rows within the standard `{schema_version,tool,tool_version,status,result,errors,generated_at}` envelope. Errors use safe `{code,message,remediation}` with the failing domain and step, without raw LMS bodies or URLs. `notice-board-paginated`, `notice-identity-ambiguous`, `item-identity-missing` and `course-sync-failed` are partial sync errors; `headless-unavailable` and `course-not-found` require user action.
