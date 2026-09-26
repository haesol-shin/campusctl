# PR5 — selected LMS source packages: specification

This specification is governed by the [project constitution](../constitution.md). PR5 depends on PR1.1's reviewed UI policy, PR2/3 list catalogs and PR4's official non-video transfer. Owner-approved request pins and sanitized observations permit fixture-only implementation of both selected fetches now. Release remains gated on an independently recorded `/std/todo` `read_yn` before/after check, an observed notice-detail attachment download through its official control, selected-detail identity/fixture acceptance and a separately authorized live check. Opening a notice detail increments its per-course view count by one: this is the only **observed and approved** state effect; if `read_yn` flips, the owner accepts it once verified and recorded. The goal is one selected local package of text, images, official files and provenance.

## Planned v0.4.0 UX reconciliation

This folder remains the sole detail/package contract; 50/60/70 do not duplicate it. Fetch still accepts exactly one full entity ID, not a materials list number; no picker or new `--course` flag is added. If a command has `--course`, it must use [50's resolver](../50-v040-ux/design.md), but fetch derives its selected course from the full ID/catalog row. Proposed command-local `--headless` syntax in the original design is superseded by `campusctl --headless assignments fetch <entity-id>` (and notices), with [60's global precedence](../60-headless-replay/design.md) and independent fetch headless gates. Parser/capability publication still waits for this folder's existing release evidence. Domain command edits are sequenced after 50's shared CLI integration owner; package/detail adapters remain parallel.

## User stories

### P1 — Bind selected detail without unapproved LMS effects [US1]

As an LMS account owner, I want a selected detail to be read through the ordinary UI without silently changing submission or other coursework state. A notice view-count increment is accepted as ordinary reading; `read_yn` behavior must be observed before release, and a flip, if present, is accepted but recorded.

**Independent test:** Replay sanitized owner-observed navigation/request/state traces for both detail types in fixtures; separately check `/std/todo` `read_yn` before/after and an actual notice attachment before publishing the commands. Fixture tests are not live-provider proof.

- **WHEN** an assignment detail is viewed, **THEN** its submission status stays unchanged; fetch MUST NOT operate its submission controls, including `#uploadFile` and the file-upload modal.
- **WHEN** a notice detail is viewed, **THEN** a single per-course view-count increment is accepted; a `read_yn` flip is accepted only after it is independently verified and recorded. Any other state effect (submission, attendance, grading or enrollment) disables the affected path.
- **WHEN** the selected row cannot be independently bound to its opened detail, **THEN** no package is published; do not match titles, URL guesses or row order.

### P2 — Retrieve one selected source without crossing media or origin boundaries [US2]

As a student, I want a selected assignment or notice packaged with complete readable text, same-origin embedded images, and officially offered non-video attachments, so I can inspect the actual source rather than a list-row summary.

**Independent test:** For each approved sanitized page type, replay the chosen detail interaction and compare text/order, image bytes, official file IDs, omission reasons, and absence of forbidden requests. Fixture a policy exclusion and a transfer failure separately.

- **WHEN** a full entity ID from the matching list is selected, **THEN** exactly that detail is opened and one package is created; list and sync never download its bytes.
- **WHEN** an allowed same-origin image or official non-video attachment is referenced, **THEN** its original bytes, type, length, hash and normalized query-free provenance are included and its content link is local.
- **WHEN** an external, video/stream, or unknown-type resource is referenced, **THEN** its bytes are never requested and its content position and omission reason remain visible; the package is explicitly policy-filtered.
- **WHEN** a redirect, media/signature mismatch, technical transfer failure, unapproved request or state-changing request occurs, **THEN** no package is published; no failure is disguised as a policy omission.

### P3 — Consume the package in a terminal or JSON client [US3]

As a user, I want readable terminal output by default and a versioned JSON response on request, so I can select full IDs, locate outputs, and distinguish complete, filtered, failed, and busy outcomes.

**Independent test:** Fixture both domain commands under terminal and explicit JSON modes, a long full ID, missing/wrong-domain ID, output conflict, policy-filtered result, technical failure, and lock contention.

- **WHEN** a package completes, **THEN** the terminal shows the entire selected ID, package and content paths, completeness and any omissions; JSON includes a versioned envelope, absolute result paths and safe provenance.
- **WHEN** only policy omissions occur, **THEN** a usable package is published with partial status and an explicit omission array; **WHEN** transfer fails, **THEN** no package is exposed.
- **WHEN** the session is occupied, **THEN** the command returns busy without treating it as success or creating output.

## Functional requirements

- **FR-001** Both fetch adapters MAY be implemented and fixture-tested using the owner-approved sanitized observations and pinned requests. Neither command MUST be advertised or released until selected-detail identity and completeness are fixture-verified, `/std/todo` `read_yn` transition is checked and recorded (whether changed or unchanged), notice attachment download is proven through its official selected-detail control, and the separately authorized live release observation is approved. Headless remains disabled until independently verified.
- **FR-002** The system MUST accept exactly one full, opaque entity ID returned by the matching list and MUST confirm the selected detail identity before transferring resources. It MUST NOT match by title, guessed URL, or row order.
- **FR-003** The system MUST render complete readable selected-detail text in order, preserve heading and link labels, rewrite included image/file links to local relative paths, and show explicit omission markers. Ordinary hyperlinks MUST preserve visible text but MUST NOT persist clickable remote URLs, queries or fragments.
- **FR-004** The system MUST include only preclassified same-origin images and officially linked, preclassified approved non-video attachments; it MUST reuse PR4's selected official-file transfer helper rather than invent a second attachment downloader. Notice attachments require live official-control download proof before release.
- **FR-005** The system MUST deny unapproved origins, routes, methods, subresources and redirects before bodies. It MUST abort only the three reviewed Panopto script/connection-logging requests at the network layer **without failing** the operation; every other unexpected request MUST fail closed. It MUST deny explicit mark-read, submission, attendance, grading, video/audio, HLS/DASH, stream and range requests and MUST NOT interact with assignment submission controls (`#uploadFile`, upload modal). A failed guard or response verification MUST abort publication. Opening an approved notice detail with its automatic view-count increment and an independently recorded `read_yn` flip, if present, is not an unapproved request.
- **FR-006** The system MUST publish a versioned manifest with selected ID, kind, course, normalized source reference, retrieval timestamp, content path, completeness, included resources, omitted resources and SHA-256 of every included original resource.
- **FR-007** The system MUST compute query-independent resource IDs from selected file identity or inline reference order and count omitted references in that order. It MUST deduplicate only repeated identical resource IDs; distinct inline positions MUST retain distinct IDs even when URL/bytes match, and conflicting uses of one ID MUST fail.
- **FR-008** The system MUST normalize resource names safely, contain all paths, reject symlinks and unresolved collisions, and enforce a 200-byte UTF-8 limit on every final resource path component after suffixing.
- **FR-009** The system MUST publish a package atomically only after all non-policy transfers and integrity checks succeed; it MUST reuse an identical default package without rewriting its original retrieval time and MUST NOT overwrite an explicit destination.
- **FR-010** The system MUST hold the single existing account browser lock throughout network fetch and publication; contention MUST return busy with no output. It MUST NOT use legacy notifier's lock or database.
- **FR-011** The system MUST provide human-readable output by default on a terminal and JSON on explicit request; full selectable IDs and output paths MUST NOT be truncated; policy-filtered output MUST visibly identify every omitted reason.
- **FR-012** The system MUST preserve the existing response envelope, statuses, exit codes and safe error shape, including success, policy partial, user-action, operational error and busy.
- **FR-013** The system MUST leave downstream job-local source records, source selection time, roles, authorization attestations and retention policy to the consumer runtime; it MUST NOT write or read those records.
- **FR-014** Implementation verification MUST use fixtures only. A separately authorized later live observation MUST verify selected detail completeness, notice `read_yn` and official attachment download and MUST record the accepted view-count effect and any `read_yn` flip before release. The owner-approved +1 count is the only currently observed approved state effect; a verified and recorded `read_yn` flip is also allowed, while all other coursework effects block publication.
- **FR-015** The system MUST NOT advertise either fetch in public capabilities or the canonical contract until its implementation, fixture acceptance and remaining release gates pass in one reviewed release; it SHOULD keep headed mode as default and MUST NOT advertise unverified headless compatibility.
- **FR-016** Fetch MUST authenticate once through existing `ensure_logged_in` before installing PR1.1's operation interceptor. Guarded course/roster re-entry (`GET /std/myLecture` included) MUST use exact pins; if login expires mid-operation, its redirect/navigation MUST fail closed as `login-action-required` or the existing login error, without automatic guarded re-login/retry.

## Edge cases

- Missing catalog or an ID from the other domain; a stale row whose opened detail cannot be independently matched.
- Null/missing detail fields, changed markup, invisible content, absent or duplicate official file IDs, repeated resource IDs with conflicting bytes.
- External-origin or signed links, redirects, content-type/extension/signature disagreement, unknown type, too-large or interrupted transfer; automatic image loads before classification.
- NFC/case-only filename collisions, slash/backslash traversal, Windows device names, symlinks, 200-byte boundary with long extension and collision suffix.
- Explicit destination already exists; interrupted atomic staging; identical cached bytes retrieved at a later time; lock contention.

## Measurable success criteria

- **SC-001** Owner-reviewed sanitized detail/request/state observations and pins allow both adapters to be implemented with fixture-only tests; the capability and contracts remain unpublished until a documented `/std/todo` `read_yn` before/after result, successful notice attachment download via the selected official control and separately approved live release review exist (FR-001, FR-014–015).
- **SC-002** Each detail fixture yields its readable text and ordered references with exact selected entity/file binding; assignment submission status stays unchanged, `#uploadFile` and upload modal receive no interaction, and notice view count increases by one without any other unapproved request/effect (FR-002–005).
- **SC-003** Fixture traces show zero body consumption for all disallowed resources and zero published packages after any technical failure; a policy-only omission publishes one explicitly partial package (FR-004–005, FR-009).
- **SC-004** Package tests verify all required fields, hashes, deterministic digest/cache behavior and final 200-byte naming/containment boundaries, including case-only collisions and symlinks (FR-006–009).
- **SC-005** Both domain terminal views preserve full IDs/paths, and JSON/error/busy fixtures produce exactly the documented statuses and exit codes (FR-010–012).

## Out of scope

No video/audio download, interception or capture; no LMS logging, explicit mark-read, submission or other unapproved coursework mutation; no bulk or scheduled detail retrieval; no integration with a legacy notifier's database, messaging or task-service adapters; no runtime-owned job source-record writes; no live LMS calls in implementation branches. Assignment/notice list-only scheduled migration remains independent of the fetch release gate.

[NEEDS CLARIFICATION: Before release, independently verify and record whether `/std/todo` `read_yn` flips on notice detail open; either observed outcome is accepted.]
[NEEDS CLARIFICATION: Before release, observe a notice detail with an attachment and prove download using its selected official control; current observed notices have none.]
[NEEDS CLARIFICATION: Notice provider-native ID, response URL availability for detail official controls, MIME/signature pairs for unobserved attachment types, and CNU headless compatibility remain unverified. Do not invent IDs or URLs; use only signature-verified known extension mappings and keep headless disabled.]
