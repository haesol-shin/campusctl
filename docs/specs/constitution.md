# campusctl constitution

These rules govern every campusctl change and take precedence over domain specs. campusctl syncs and reads the owner's own coursework through the normal LMS web UI; it owns local catalogs and selected source packages, not scheduling or delivery.

## Principles

1. **Working features first.** A core command that fails is a defect, and so is a safeguard that stops it without preventing one of the harms listed under [hard limits](#hard-limits). Narrow such a safeguard at its source instead of adding exceptions around it.
2. **UX first.** Human-readable output is the default on a TTY; `--json` gives agents a stable versioned envelope. Lists print complete selectable IDs and stale-cache warnings; nobody parses a truncated ID.
3. **Suppress, don't stop.** The request guard aborts harmful requests and lets the run continue (see [request guard](#request-guard)). A run stops only for a failed login, a busy session, or data that cannot be tied to its course.
4. **Verified identity before publication.** Data is published only under the course the committed page and its responses prove. A wrong or unprovable course fails that course and keeps its previous records; other courses and domains continue.
5. **One session per account.** Network commands share one non-blocking browser session lock. Catalogs and packages are private, atomic and domain-separated. `busy`/75 never counts as an empty successful sync.
6. **Traffic from evidence.** Changes that affect LMS traffic are derived from a recorded [live run](live-run.md), not guessed from fixtures. Tests replay recorded traffic offline.
7. **Small reviewed PRs.** Stage only intended paths with `git add <path>`. Commit and PR titles use `type(scope): summary` in English, then exactly one blank line and at most four adjacent imperative `- ` bullets without trailing periods; no trailers. Verify with `git log -1 --format=%B | cat -A`. Release integration serializes CHANGELOG and version changes.

## Hard limits

These are the only lines campusctl never crosses:

- **No media transfer.** No video or audio download, stream interception, capture, range fetching or HLS/DASH retrieval. Playback happens only in the official player, one lecture at a time.
- **No coursework writes.** No submission, attendance, grading, enrollment or mark-read action. Opening a notice detail may add one view and flip the notice's read state; that is ordinary reading.
- **No logging or telemetry calls.** Known LMS logging and telemetry requests are aborted before they leave the browser.
- **No private data in the public repository.** Real course names, IDs, board or file IDs, personal names and raw traffic stay in owner-held evidence; fixtures use synthetic IDs and `.invalid` hosts.

## Request guard

The guard is installed context-wide right after login, including popups, and applies one rule set to every request:

| Request | Disposition |
| --- | --- |
| Known logging or telemetry endpoint, Panopto logging or connectivity check | Suppress |
| Audio, video, media or stream resource type or extension | Suppress |
| Non-GET request whose path names logging, attendance, progress or a write action | Suppress |
| Cross-origin request other than a passive static asset | Suppress |
| Passive static asset (script, stylesheet, font, image) from any origin | Allow |
| Same-origin page request not matched above | Allow |
| Selected official attachment transfer | Allow only when bound to the selected file, typed and size-bounded |

Suppression never fails a run. Every suppressed request and every allowed request outside the reviewed route list is recorded in the run diagnostics as method, origin class, placeholder path, resource type and reason, so the next change starts from evidence.

## Boundary decisions

| Tier | Actions |
| --- | --- |
| **Always** | Full IDs, private atomic writes, one session lock, verified course identity, sanitized diagnostics, selected-file binding, one recorded live run before traffic-affecting work. |
| **Ask first** | A second live run for the same change, any exception to a hard limit, new attachment types, production cutover of other automation. |
| **Never** | The [hard limits](#hard-limits); publishing a capability that has not passed its live run. |

## Owner decisions

- **Attachments** (2026-09-25): allowed extensions are pdf, ppt/pptx, doc/docx, xls/xlsx, hwp/hwpx, txt/md, png/jpg, zip and ipynb/py/c/cpp/java/js/sql, up to 200,000,000 bytes. Observed MIME/signature pairs: PDF `application/x-pdf` + `%PDF-`; PPTX Office MIME + `PK\x03\x04`; ZIP `application/zip` + `PK\x03\x04`. Other extensions need a matching known signature, never MIME alone. ZIP files are stored, never extracted. File names come from the official control or `getAttachFileList` display text with a trailing `바로보기` removed, then sanitized.
- **Notice reading** (2026-09-25): one view per opened notice detail is accepted; a read-state flip is accepted and recorded.
- **Notice attachments** (2026-09-27): until a notice with an attachment is observed, notice fetch records attachments as omissions and transfers nothing.
- **Guard model** (2026-09-27): suppress-and-continue replaces stop-on-unknown; the per-course selection-epoch layer is removed.
