# PR1 (merged) and PR1.1 — Unreleased LMS source foundation

This specification is governed by the [LMS source constitution](../constitution.md). Merged PR1 establishes private shared contracts. PR1.1 extends its request guard before the independent domain PRs; neither publishes a command, enables provider retrieval, or alters the current lecture capability.

## Planned v0.4.0 delta

[50-v040-ux](../50-v040-ux/design.md) adds shared course resolution, generation-bound human selection snapshots and advisory freshness, while preserving schema-1 domain isolation/private atomic writes. [70-sync-performance](../70-sync-performance/design.md) permits one guarded serial orchestration session with explicit operation epochs, never a policy union or unguarded route transition. Existing denial/publication, selected-file and stale-course invariants remain authoritative. These later deltas do not retroactively alter the merged PR1 implementation claim.

## 2026-09-26 reviewed side-request decision

An unknown-origin GET of resource type script, stylesheet, font, image, or media is aborted but nonfatal, diagnosed as `third-party-asset`, only when it has no Range header and is not redirected. Unknown-origin POST and xhr/fetch/document/other requests remain fatal. An explicitly reviewed suppression is checked before the policy-origin gate; its origin need not be in `origins`. The LMS Panopto SAML script, external telemetry POST `/v1/events` at its observed origin, LMS course-roster `/upload/dunetadmin/college/{hash}.png` images (image only), and LMS GET `/assets/images/favicon-{hash}.ico` with resource type other are aborted and counted with their reviewed reasons. Owner decision 3 instead allows exact LMS POST `/api/v1/week/getStdActivityStatus` XHR as a read-only operation-scoped route, because aborting it triggers a blocking `/std/lecture` error modal. Validation accepts its explicit logging-token review flag only on this exact LMS origin/path/method/resource tuple in `assignments.sync`, `materials.sync`, `materials.download` and `notices.sync`; the logging matcher remains unchanged and denies unreviewed paths. Range and redirects remain fatal even for reviewed suppressions and the activity-status route.

## Prioritized user stories

### P1 / US1 — Trust a domain-specific catalog without mixing lecture state

As a source consumer, I need stable full item identities and honest cache freshness so I can choose exactly one item without mistaking stale rows for fresh data.

**Independent test:** Using synthetic course rosters and existing cached rows, independently merge each domain and inspect only that domain's catalog and list cache metadata.

- **WHEN** a full roster and every course succeed, **THEN** each completed course replaces its own rows, including an empty result, and a previously enrolled but absent course disappears.
- **WHEN** a course fails or its item identity is unaddressable, **THEN** its old rows remain visible and marked stale; no partially parsed rows from that course commit.
- **WHEN** full roster discovery fails, **THEN** all previous rows and the previous cache timestamp remain, enrollment becomes unknown, and no row can be treated as fresh.
- **WHEN** a course-filtered sync succeeds, **THEN** unrelated courses, failure markers, and enrollment state remain unchanged.

### P1 / US2 — Enter the selected course through the normal LMS context

As a provider implementer, I need one ordinary UI route into a course so assignments and archive files resolve in the course context established by the LMS, rather than by guessed direct URLs.

**Independent test:** A fake page records authentication/navigation/row/menu events; fixture assertions prove a selected course row is activated before any section menu, including an empty section.

- **WHEN** entering an enrolled course section, **THEN** the implementation revisits the authenticated course list, activates the exact course row, waits for its menu, and selects the observed section link.
- **WHEN** a course has no rows, **THEN** section entry may succeed without fabricating a row or bypassing the course context.
- **WHEN** a request matches a named, reviewed Panopto suppression, **THEN** its network request is aborted, its safe reason is counted, and the operation continues; every other unreviewed request fails closed before its body is consumed.

### P1 / US3 — Preserve existing UX while preparing independent domains

As a lecture CLI user, I need PR1 to leave current commands, human/JSON output, version, and capabilities unchanged while later domains can be discovered without editing shared dispatch, renderer, or contract-inventory hunks.

**Independent test:** Exercise existing lecture parser/renderer and inspect the runtime capabilities with every new domain unregistered.

- **WHEN** running any existing lecture/course command, **THEN** its behavior and response envelope remain unchanged.
- **WHEN** an unimplemented domain is requested, **THEN** it remains unsupported and unadvertised, not an empty successful result.
- **WHEN** a reviewed domain later participates, **THEN** its own command, sync count, human renderer, policy, and per-domain contract can be added by new files alone, without editing the CLI or renderer.
- **WHEN** a domain module exposes only part of the required command contract or has malformed capability data, **THEN** discovery fails visibly rather than silently treating it as unsupported.

### P2 / US4 — Freeze future package and safety rules without fetching

As a selected-source implementer, I need design fixtures for package identity, omissions, digest, safe names, and official-file boundaries before any byte retrieval is enabled.

**Independent test:** Pure fixture transformations derive deterministic IDs, names, digests, and omission status without opening a browser or transferring bytes.

- **WHEN** the same sanitized content is packaged twice at different retrieval times, **THEN** its content digest is stable and a cache copy may be reused.
- **WHEN** a resource is external, video, or unknown, **THEN** the fixture records a policy omission and cannot claim the package complete.
- **WHEN** names collide after normalization and the final byte limit, **THEN** a deterministic suffix is applied once or an unresolved conflict fails without overwrite.

## Functional requirements

- **FR-001** The system MUST assign stable full opaque IDs to assignment, notice, and material rows and retain separate native fields; consumers MUST pass returned IDs unchanged.
- **FR-002** The system MUST preserve the exact legacy notice key independently of its new ID; absent identity components MUST fail the whole course merge.
- **FR-003** The system MUST store separate atomic domain catalogs alongside, not inside or in place of, the lecture catalog.
- **FR-004** Each domain catalog MUST expose version, cache timestamp, enrollment state, course roster, failed-course reasons, and its own rows.
- **FR-005** A successful full roster MUST mark enrollment known; failed courses MUST retain old rows and stale markers; formerly enrolled courses MUST be removed only on complete full success.
- **FR-006** A full roster-discovery failure MUST mark an existing catalog's enrollment unknown without changing rows or cache timestamp; a course-filtered update MUST not change unrelated state.
- **FR-007** Readers MUST NOT acquire the browser lock; network operations MUST use the existing one-session-per-account lock; atomic writes MUST never expose a partially written catalog.
- **FR-008** Course section entry MUST follow the authenticated course-row and menu flow; it MUST NOT synthesize or directly call an unverified course-context endpoint.
- **FR-009** The UI request policy MUST fail closed until exact approved origins, operation-specific data paths/methods, and read-only-effect evidence are pinned; it MUST reject case-insensitive `Range`, unapproved redirects and subresources before body consumption. Reviewed named suppressions and passive third-party GET assets in the 2026-09-26 decision are aborted without failing the operation; other unknown-origin and media/video/audio/stream requests remain fatal.
- **FR-010** PR1 MUST leave the released command inventory and observable capability value unchanged; its contract documentation MAY gain only an index pointer to future per-domain contracts, not an advertised domain command.
- **FR-011** Future domains MUST be discovered by adding only their own domain files; they MUST render through their own human renderer and reuse the existing JSON envelope and error/exit semantics, without editing shared CLI or renderer files.
- **FR-012** PR1 MUST preserve headed lecture playback; a proposed headless non-playback path MUST NOT silently alter playback or imply verified CNU compatibility.
- **FR-013** Design fixtures MUST sanitize credentials, cookies, signed queries, student identifiers and response bodies while retaining necessary selector attributes and request method/path; implementation MUST NOT contact the live LMS.
- **FR-014** Package design fixtures MUST pin versioned provenance, resource/omission identities, deterministic digest, private atomic publication and safe-name rules; PR1 MUST NOT enable fetch or download.
- **FR-015** Published domain capabilities MUST list only implemented, owner-reviewed commands; each registered domain module MUST carry its exact approved policy pins internally in `CAPABILITY["policy"]`, which is validated at discovery but not advertised. A missing gate MUST leave a command unregistered and unadvertised.
- **FR-016** Discovery MUST skip helper modules exporting none of the domain hooks and complete modules explicitly marked unapproved; it MUST fail visibly for partial exports or malformed capability data.
- **FR-017** Request policy MUST distinguish an otherwise approved GET from the same request containing any case-variant `Range` header and block the latter before consuming a body.
- **FR-018** PR1.1 MUST support an operation-scoped `suppress` list limited to named reviewed side requests: Panopto scripts/logging, course-roster upload images, the exact LMS favicon (resource type other), and the external telemetry POST at its exact origin. Matches MUST be aborted without failing the operation, with a diagnostic count and safe reason, never a token-bearing URL. Only exact LMS POST `/api/v1/week/getStdActivityStatus` XHR MAY continue with `logging_token_reviewed:true` in `assignments.sync`, `materials.sync`, `materials.download` or `notices.sync`; validation MUST reject a different path, origin, method, resource type, operation or malformed flag data. Without the flag or outside that route, logging remains denied. A third-party passive GET asset is likewise aborted and counted as `third-party-asset`; all other denials MUST fail closed.
- **FR-018a (2026-09-26 owner delta)** For `assignments.sync`, `notices.sync`, `materials.sync`, and `materials.download` only, abort the observed Panopto SSO popup document `POST https://cnu.ap.panopto.com/Panopto/Pages/Auth/Login.aspx` with diagnostic reason `panopto-sso-popup`, without latching an operation denial. When Playwright exposes the frame, require the top frame of an LMS `/SSOServiceLogin` popup opened by the LMS `/std/myLecture` roster. Close that popup after abort; if frame context is unavailable, retain the exact origin/method/path/resource-type/operation pin. All other Panopto requests and lecture playback remain fail-closed and unchanged.
- **FR-019** PR1.1 MUST permit only GET `script`, `stylesheet`, `font`, and `image` resources from explicitly approved static-asset origins, excluding suppressed paths, audio/video/media/stream resource types, and media/video extensions. Data XHR/fetch/document MUST remain exact operation/method/path pins.
- **FR-020** PR1.1 MUST implement a selected-file URL binder and anchored path-template matcher accepting only a reviewed origin/template and resolved URL bound to the selected file ID from an official control or fileDownload response. Matching MUST be segment-bounded and reject decoded slashes, dot traversal, double encoding, unexpected query/fragment, `Range`, and redirects; no prefix permission or host inferred from page content.
- **FR-021** PR1.1 MUST provide one page-or-context request interceptor used by every new domain after trusted pre-operation authentication. It MUST guard the roster and all in-operation navigation, forward Playwright `request.redirected_from`, abort suppressions, continue allows, and abort plus propagate every other denial even when Playwright swallows route callback exceptions. Course entry MUST be navigation-only on an authenticated page; expiry during the operation fails closed without re-login/retry.
- **FR-022** PR1.1 MUST remain private: no new command, capability, live LMS call, or changed lecture playback. PR4 owns response MIME/signature/size enforcement; PR4 MUST consume, not reimplement or edit, the PR1.1 request binder and interceptor.

## Edge cases

- Empty but successfully addressable course replaces old rows with empty; a missing identity does not become an empty success.
- Partial full roster leaves previously cataloged but currently absent courses stale with a deferred-removal reason; unknown roster makes every row stale.
- Changed notice course label/date/number can alter legacy identity; never fuzzy-normalize it to suppress duplicates.
- A package resource omitted for policy remains counted in reference order; a reused resource ID with different bytes is a conflict.
- UTF-8 component length is measured **after** suffixing and truncation; a case-only or NFC-equivalent collision cannot silently overwrite a file.
- A helper module has no domain exports; a half-implemented module must raise on discovery rather than vanish from help or capabilities.
- Browser busy is exit 75, not an empty successful sync; no second legacy notifier/browser lock is acquired.
- `[NEEDS CLARIFICATION: Representative notice attachment behavior and MIME/signature evidence for unobserved attachment extensions remain unavailable; verify whether the to-do read_yn flips and whether headless works before PR5 release. Per-course global to-do coverage is unverified.]`

## Success criteria

- **SC-001** A fixture-only full/partial/unknown/filtered merge matrix demonstrates zero cross-domain or lecture-catalog changes and no stale row presented as fresh.
- **SC-002** A fake-browser event trace shows course-row activation before section link for every selected course and zero direct course-context endpoint calls.
- **SC-003** The disabled policy rejects every request; reviewed synthetic pins allow only exact fixture-approved data routes, bounded per-route query fields and reviewed resource-type static assets, while rejecting all other logging/media/redirect requests. Identical approved GET cases with and without a `Range` header prove ranged denial before body consumption.
- **SC-004** Existing lecture CLI outputs and capability value match their pre-PR1 contract; no new domain is advertised.
- **SC-005** Identity, naming, and digest fixtures produce the same outputs across repeated pure runs; no fixture contains a credential, token, signed URL, or video payload.
- **SC-006** Synthetic route callbacks show the three named suppressions abort at the network layer, tally reasons without URLs, and do not fail the operation; visually similar unlisted scripts, attempted suppression of a data route, malformed suppression entries and other unexpected requests fail `policy-blocked`/error/1.
- **SC-006a** Synthetic cases suppress and count the exact popup POST in each of the four named operations and close its popup; playback, a different origin/path/method/resource type, and a known non-popup frame remain denied.
- **SC-007** GET static resource-type matrix allows only approved-origin script/stylesheet/font/image outside suppression/media exclusions, including queried fonts/scripts without persisting URLs. XHR/fetch/document, outside-origin static, video extensions/types, redirects and `Range` never receive the resource-type exemption.
- **SC-008** Selected ID + official URL binding permits only the exact reviewed selected-file template path; false-prefix, segment-overrun, encoded slash, dot segment, double encoding, unexpected query/fragment, wrong selected file, redirect and ranged request all fail before bytes. A page and a reused context each demonstrate interception before navigation and denial propagation.

## Out of scope
No domain sync/list/fetch/download release, live LMS access, lecture-video or stream retrieval, provider state mutation other than the reviewed notice-detail view increment, legacy notifier ledger/timer/notification changes, external production checkout edits, or advertised domain command in PR1/PR1.1. PR2/3/4 implement parallel domains after PR1.1; PR5 selected packages follow their evidence gates; later notifier adapter and hosted-browser retirement remain separate.
