# Official LMS materials — PR4 specification

This specification is governed by the [campusctl constitution](../constitution.md). The goal is to let people discover official course-archive attachments and save **one explicitly selected, approved non-video file** safely. The human terminal view is primary; agents opt into machine-readable JSON. Discovery and listing are metadata-only. The scheduled consumer retains responsibility for choosing unseen files and its own ledger.

## Planned v0.4.0 delta

[50 UX](../50-v040-ux/design.md) adds human row numbers bound to the last printed list/catalog generation, one-file picker, common course selectors and explicit list refresh. Numbers resolve to full IDs before existing download validation; stale generations fail, JSON requires full IDs, and bulk download remains prohibited. [60](../60-headless-replay/tasks.md) gates download headless separately from sync; flags become global. [70](../70-sync-performance/design.md) permits skipping a modal restore reload only with exact course/document/page/post proof and guarded fallback. File identity/type/size/receipt/publication safeguards are unchanged.

## User stories

### US1 (P1) — Discover and inspect archive attachments

As a student, I can refresh course-archive metadata and list every official file target with its full selectable ID, course, archive title, filename and downloadability, without transferring any attachment bytes.

Independent test: Given local fixtures for modal and inline archive controls and a fake browser, sync then list; assert every official target appears exactly once, unsupported targets remain visible but not downloadable, no file-body request occurs, and human/JSON outputs describe the same rows.

- **WHEN** an archive post offers two official file targets, **THEN** the resulting list has two independently selectable rows; a post without files has none.
- **WHEN** the shared file modal does not open, **THEN** discovery checks inline controls; if neither resolves the post, the whole course fails and its previous rows remain stale. The post-detail `/std/archiveView` fallback is removed because it has no reviewed route pin.
- **WHEN** visible text ends in `바로보기`, **THEN** `display_name` retains that raw text while `filename` has only the trailing label and optional adjacent whitespace removed before type classification or safe naming.
- **WHEN** a row is video, unknown type, or on an unapproved origin, **THEN** it remains metadata-only with `downloadable: false`.
- **WHEN** the course cannot be completely enumerated or a native file ID is missing/duplicated, **THEN** its old rows remain visible as stale and no partial new course set is committed.
- **WHEN** a complete full sync discovers a course with zero attachments, **THEN** its prior attachments are removed; an incomplete or absent roster does not falsely remove them.

### US2 (P2) — Download one selected official attachment

As a user, I can pass the full ID from the list and optionally choose a destination directory. I get the absolute path, verified digest, byte count and provenance; no other file is fetched.

Independent test: With a pinned fixture policy and fake selected-file response, invoke one download ID and assert only the matching official file control is used, the body is classified and bounded before final publication, and a complete file with matching bytes and digest appears atomically.

- **WHEN** the ID is missing, unknown or not downloadable, **THEN** the command rejects it before any browser/file-body request.
- **WHEN** the selected control has moved or no longer matches the catalogued file and board IDs, **THEN** no file is requested or published.
- **WHEN** an origin, route, method, redirect, MIME/extension pair, response header, content signature or byte limit fails policy, **THEN** transfer stops and no partial file becomes visible.
- **WHEN** a file name contains traversal separators or reserved components, **THEN** the saved component is safe and remains under the output directory.
- **WHEN** transfer succeeds, **THEN** human output prints its full absolute path; JSON gives the raw label, label-free name, path, size, digest and selected-file provenance.

### US3 (P3) — Repeat safely without duplicates or partial residue

As a user rerunning a selected download, I can skip an already verified same-ID/same-destination file without network transfer. An interrupted transfer is safely restarted, not resumed through HTTP Range.

Independent test: Download fixture bytes once; invoke the same ID and destination again and assert no LMS request, identical result path/hash, and no new file. Corrupt the local file and assert the next call no longer skips it. Interrupt a transfer and assert no published partial file or leftover temp remains.

- **WHEN** the original safe basename exists with identical bytes, **THEN** reuse it rather than create a duplicate.
- **WHEN** it has different bytes, **THEN** use exactly one full-content-digest suffix before the extension; a conflicting digest-suffixed name or symlink is an error, never an overwrite or numeric loop.
- **WHEN** a verified receipt points to a changed, missing, symlinked or out-of-directory file, **THEN** it is not trusted as a skip.
- **WHEN** transfer fails or is cancelled, **THEN** an exclusive same-directory temp is removed; the next invocation starts from byte zero, without a Range request.

## Functional requirements

- **FR-001** The system MUST expose metadata sync, local list, and one-ID selected download only after each operation's read-only and policy evidence is approved; it MUST NOT advertise a disabled operation. The approved materials pins are in the owner-held sanitized policy decision (2026-09-25, lines 23-60); observations are in the owner-held sanitized observation report (2026-09-25, lines 38-59,79-99,189-238).
- **FR-002** Sync and list MUST NOT download attachment bodies; each official archive file target MUST appear as its own catalog row, including non-downloadable targets.
- **FR-003** Each material MUST have a stable full ID bound to course ID and native file ID; duplicate or missing native IDs MUST reject the entire affected course rather than silently merge/omit a row.
- **FR-004** The system MUST preserve exact raw LMS display text separately and remove only a trailing `바로보기` label with optional surrounding whitespace before allowlist classification or safe filename normalization.
- **FR-005** `downloadable` MUST be true only for an official selected target with approved origin and one of pdf, ppt/pptx, doc/docx, xls/xlsx, hwp/hwpx, txt/md, png/jpg, zip, ipynb/py/c/cpp/java/js/sql; unknown, external, video/audio or stream targets MUST remain non-downloadable. ZIP MUST be stored, never extracted.
- **FR-006** Full and course-filtered sync MUST preserve prior rows for failed courses, expose stale markers and unknown enrollment, defer removal after partial discovery, and remove old rows only after a fully successful full roster pass.
- **FR-007** List MUST read the cache without browser access and display full, copyable IDs, course, archive title, filename, downloadability and a warning for stale cache; sync MUST report material rather than lecture counts.
- **FR-008** Download MUST accept one full listed ID, verify the exact selected official file and board identity again, and MUST NOT request any bytes for an unknown or non-downloadable ID.
- **FR-009** Network access MUST fail closed for unapproved origins, routes, methods, data subrequests and redirects, unreviewed logging or video/audio/stream/range requests. The named Panopto script and two Panopto connection calls MUST instead be aborted at the network layer without failing the operation; exact LMS POST `/api/v1/week/getStdActivityStatus` XHR MUST continue only in `materials.sync` and `materials.download` with the explicit `logging_token_reviewed:true` route flag. Every other unexpected page request MUST fail it. Same-origin script/stylesheet/font/image static resources MAY be allowed by resource type from approved origins, excluding suppressed and media/video paths; data XHR/fetch/document requests MUST match exact paths.
- **FR-010** A selected download MUST bind its actual URL to the official selected file ID and one of the two approved segment-bounded URL templates, with no redirects, unexpected query, decoded slash, traversal or double encoding. It MUST require compatible extension, the strict observed MIME/signature pair for pdf/pptx/zip, or extension-specific signature/text validation for other owner-approved types; reject `video/*` and `audio/*` regardless of extension. It MUST reject non-success, empty, mismatched or interrupted responses without publication.
- **FR-011** Download MUST enforce `max_bytes: 200000000` against declared and streamed byte counts. The observed pairs are pdf `application/x-pdf`/`%PDF-`, pptx OOXML presentation MIME/`PK\x03\x04`, zip `application/zip`/`PK\x03\x04`; each is strict. Other approved extensions MUST match the known extension-specific signature or valid UTF-8 text; OOXML/hwpx additionally MUST have the required ZIP central-directory entry, checked without extraction. For these unobserved types, an unmapped server MIME MUST NOT prevent a signature-validated save, and MIME alone MUST NOT admit one. `[NEEDS CLARIFICATION: live MIME values for hwp, docx, and other unobserved types are not yet verified; record observed MIME for later pinning.]`
- **FR-012** Output names MUST be traversal-safe, normalized, device-safe, collision-keyed after truncation, and at most 200 UTF-8 bytes in the final component including suffix and extension; no path below the output root may follow a symlink. The default destination MUST be `<Downloads>/campusctl/<safe course label>/`, and on Windows the complete absolute path MUST remain at most 250 characters (shorten the basename including its collision suffix or fail with `output-path-too-long` and suggest `--out`).
- **FR-013** Different-content name collisions MUST use one deterministic full SHA-256 suffix; identical bytes MAY reuse an existing regular file only after verification; a remaining conflict MUST fail without overwrite.
- **FR-014** Downloads MUST use exclusive same-directory temporary files and atomic no-clobber publication; every failure MUST remove temporary output and MUST NOT replace user files.
- **FR-015** A private, atomic success receipt MAY permit no-network skip only after checking the same full ID and absolute output directory against a regular, non-symlink file whose bytes match the stored digest/size; interruption MUST restart without Range.
- **FR-016** Browser operations MUST share one exclusive account-session lock; busy MUST be reported distinctly; local list MUST NOT acquire it.
- **FR-017** Terminal output MUST be human-readable by default on a terminal and print the saved absolute path; `--json` MUST provide the versioned envelope and safe error codes for agents.
- **FR-018** Implementation and CI MUST use fixtures, not a live LMS; selected-file release MUST await separately approved verification that the official UI action does not mutate coursework state.
- **FR-019** Headless network operation MAY be enabled only after independent provider compatibility evidence; before that the opt-in MUST fail explicitly rather than silently falling back to headed mode. `[NEEDS CLARIFICATION: actual CNU headless behavior is unverified.]`
- **FR-020** When `Content-Disposition` is absent, the official selected control's display text or `getAttachFileList` filename MUST supply the saved name: remove only a trailing `바로보기` and then sanitize. Neither a URL basename nor a server suggestion may replace this provenance without an independently verified official filename.

## Edge cases

- An absent file icon could mean an empty archive or a failed/still-loading page. Treat it as a failed course unless bounded UI observation confirms a completed empty result; `[NEEDS CLARIFICATION: provider's explicit archive empty-state selector and timing evidence are not established; the owner-held sanitized observation report (2026-09-25, lines 137-155) covers task, not archive.]`
- The two approved storage origins and selected URL shapes are pinned in the owner-held sanitized policy decision (2026-09-25, lines 37-60); do not synthesize a URL from page text. `[NEEDS CLARIFICATION: representative sanitized archive empty-state and board-title mapping fixtures are not established.]`
- There was no `Content-Disposition` in the three observed responses (owner-held sanitized observation report, 2026-09-25, lines 195-238). Prefer official control or `getAttachFileList` text, strip the trailing label, then sanitize. A response-suggested name cannot upgrade an unknown row or overwrite the official name. `[NEEDS CLARIFICATION: if neither official name is available, the selected row is not downloadable; an approved fallback fixture has not been supplied.]`
- Case-only and Unicode normalization collisions, both slash forms, `.`/`..`, Windows device names, trailing spaces/dots, overlong extensions and a collision that remains after a full digest suffix MUST be covered.
- Zero-byte bodies, misleading `Content-Length`, failed redirects, signed URLs and interrupted temp files MUST never appear as successful saves.

## Success criteria

- **SC-001** In fixture runs, every official file control yields exactly one list row, while sync and list issue **zero** attachment-body requests; exact metadata routes and named Panopto aborts permit a completed archive operation but an unrelated request fails closed.
- **SC-002** A selected file succeeds only when its official file ID binds to an approved storage URL, its signature/text classification is compatible, and published bytes, reported byte count (at most **200000000**) and full SHA-256 agree; every policy/transfer failure publishes **zero** files or partial temps. ZIP is stored unopened.
- **SC-003** Every tested final path component is at most **200 UTF-8 bytes** and within its output root; no symlink, name collision or concurrent publication overwrites an existing file.
- **SC-004** A verified same-ID/same-destination retry makes **zero LMS requests** and returns the same path/hash; an interrupted transfer makes **zero Range requests**.
- **SC-005** A full discovery failure leaves **all prior rows and original catalog timestamp** unchanged while reporting unknown enrollment; a failed course commits **zero** new rows.
- **SC-006** Human list renders every full selectable ID and a stale warning when needed; human download prints an absolute output path; agent mode emits exactly one versioned JSON envelope per completed command.

## Out of scope

No lecture video download, stream interception/capture, media/range fetch, LMS logging endpoint, read-state/coursework mutation, arbitrary URL download, bulk download, scheduled process, legacy notifier ledger/database ownership, assignment/notice detail packaging, or production legacy notifier checkout edits. The source-package runtime owns its separate job record. No live LMS invocation during implementation or CI.
