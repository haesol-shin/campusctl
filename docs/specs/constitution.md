# campusctl constitution

These rules govern every campusctl change and take precedence over domain specs. campusctl syncs and reads the owner's own coursework through the normal LMS web UI; it owns local catalogs and selected source packages, not scheduling or delivery.

## Principles

1. **Every feature works.** A command that fails on the live LMS is the most serious defect. Nothing campusctl adds may stop a command that a person could complete in the same browser.
2. **Then optimize.** Speed and resource work comes second and never trades away principle 1. Optimizations are measured on a live run, not estimated.
3. **Act like a person.** campusctl opens pages and clicks menus the way a user does and lets each page behave as it does for that user. It does not filter or rewrite the page's own requests.
4. **UX first.** Human-readable output is the default on a TTY; `--json` gives agents a stable versioned envelope. Lists print complete selectable IDs and stale-cache warnings. Errors name the step that failed.
5. **Verified identity before publication.** Data is published only under the course the committed page and its responses prove. A wrong or unprovable course fails that course and keeps its previous records; other courses and domains continue.
6. **One session per account.** Network commands share one non-blocking browser session lock. Catalogs and packages are private, atomic and domain-separated. `busy`/75 never counts as an empty successful sync.
7. **Learn from the live LMS.** Changes that touch LMS behavior start from a recorded [live run](live-run.md) and the owner's local LMS behavior notes, which every run updates.
8. **Small reviewed PRs.** Stage only intended paths with `git add <path>`. Commit and PR titles use `type(scope): summary` in English, then exactly one blank line and at most four adjacent imperative `- ` bullets without trailing periods; no trailers. Verify with `git log -1 --format=%B | cat -A`. Release integration serializes CHANGELOG and version changes.

## Limits

- **No coursework actions.** campusctl never clicks submission, attendance, grading, enrollment or mark-read controls. Opening a notice detail may add one view and flip its read state; that is ordinary reading.
- **No saved media.** Lecture video and audio are never saved to disk; playback happens only in the official player. Material downloads save only the selected official attachment, checked for type, signature and size.
- **No private data in the public repository.** Real course names, IDs, board or file IDs, personal names, raw traffic and the LMS behavior notes stay with the owner; fixtures use synthetic IDs and `.invalid` hosts.

## Owner decisions

- **Attachments** (2026-09-25): allowed extensions are pdf, ppt/pptx, doc/docx, xls/xlsx, hwp/hwpx, txt/md, png/jpg, zip and ipynb/py/c/cpp/java/js/sql, up to 200,000,000 bytes. Observed MIME/signature pairs: PDF `application/x-pdf` + `%PDF-`; PPTX Office MIME + `PK\x03\x04`; ZIP `application/zip` + `PK\x03\x04`. Other extensions need a matching known signature, never MIME alone. ZIP files are stored, never extracted. File names come from the official control or `getAttachFileList` display text with a trailing `바로보기` removed, then sanitized. The download binds its transfer to the selected file.
- **Notice reading** (2026-09-25): one view per opened notice detail is accepted; a read-state flip is accepted and recorded.
- **Notice attachments** (2026-09-27): until a notice with an attachment is observed, notice fetch records attachments as omissions and transfers nothing.
- **No request guard for sync and fetch** (2026-09-27): pages run their own requests unfiltered, as they do for a person; the per-course selection-epoch layer is removed.
