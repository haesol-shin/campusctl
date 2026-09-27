# A — Attribute sync waits and document time

## Goal and expected saving

Understood as: specify behavior-preserving instrumentation that explains elapsed sync time, not a speed optimization. Expected saving: **0 s**; profiling overhead must be **≤5%**. Owner-held sanitized evidence (2026-09-27): 188 s full sync, seven courses, 68 main-frame documents, approximately 90 s of lock time without child spans. Existing archive-restore exclusive time is 35.8 s (53.1 s inclusive, 21 restores). B and C must compare measured waits and document transitions, not infer savings from those totals.

## Current behavior

- `src/campusctl/profiling.py:18-44,117-162,164-222`: allowlisted categories, ephemeral course/window ordinals, bounded spans, nested interval-union accounting, one completion line. Schema 1 aggregates away the individual timeline; it has no document timing. Disabled spans do not read the clock.
- `src/campusctl/browser.py:37-57,172-181,553-561,589-602`: contextual recorder, bounded timeout conversion, main-frame navigation count, listener cleanup. Navigation events alone do not distinguish a new document from same-document navigation.
- `src/campusctl/providers/cnu/sync_all.py:94-97,145-184,199-262,451-452`: network-idle settles before/after transitions, unspanned roster return, menu and identity waits. Roster already has document/DOM/extract spans (`104-123`).
- Remaining waits: `providers/cnu/course_context.py:27-37,54-58` (identity polling/menu); `sync.py:72-81` (identity/rows); `assignments.py:249-268,292-301` (response/load/table); `notices.py:330-350,382-397,538-564` (load/grid/response); `materials.py:253-257,319-325,469-470,503-547` (load/completion/modal/restore). Paths in this bullet are relative to `src/campusctl/`.
- `src/campusctl/providers/cnu/login.py:74-99,102-112,131-149,180-207,233-245`: authentication includes an existing-session probe, credential submission only when needed, response and landing waits. Do not equate the entire auth span with a fresh login.
- `tools/profile_sync.py:132-171` rejects non-v1 profiles and requires exact span keys; migrate this consumer with the producer. `tests/test_profile_sync.py:140-184` covers profile sanitization/persistence.

## Design and ownership

Implement A before B/C rebase their behavior changes. A owns recorder/browser instrumentation, the profile report reader and their tests; touches provider code **only to measure existing operations**. B owns readiness helper and replacement of settles; C owns archive restoration decisions; D owns any new course switch; E owns reuse behavior. Shared contract agreed with B: `page-readiness`, `page_kind`, `wait_kind`, and ephemeral `course` (never a course ID). C retains the `archive-restore` envelope around proof plus any reload. Preserve D's `course-selection` envelope. No changes to fetch CLI profiling scope: `src/campusctl/cli.py:820-821` restricts profiling to sync/refresh.

### Work unit A1 — recorder and schema

Extend `SpanRecorder.span` and `browser.profile_span` with keyword-only `page_kind`, `wait_kind`, `document`. Use a private unset sentinel in the browser wrapper: omitted labels inherit, explicit null clears, and labels ultimately absent serialize as null. The recorder receives resolved labels with null defaults. Add contextual `browser.profile_labels(...)` using a reset-in-finally ContextVar so collectors inherit domain/course ordinals without accepting course objects. Maintain an explicit session-local label snapshot for event callbacks, which must not depend on callback task context. Capture labels when a span starts, not when it finishes. Existing window remains unchanged.

All emitted strings must be enums. Retain existing phases; add `wait` and `page-readiness`. Allowed page kinds: `login`, `todo`, `roster`, `course-entry`, `lecture`, `assignments`, `notices`, `archive`, `assignment-detail`, `notice-detail`, `other`. Allowed wait kinds: `selector`, `function`, `load`, `timeout`, `action`, `response`, `navigation`, `readiness`. Use `idle`/`load` for existing network-idle settles, `dom-ready`/`selector` for explicit DOM waits, `wait`/`function` for identity polling, and `page-readiness`/`readiness` for B's eventual helper. Never derive labels from `bounded(..., what)`, exception text, selectors or identifiers.

Emit schema **2**, retaining the existing prefix and top-level fields from `profiling.py:209-221`; add only `documents` and `coverage`. Aggregate span key and emitted row are exactly:

```json
{"phase":"idle","domain":"materials","course":1,"window":null,"page_kind":"archive","wait_kind":"load","document":1,"failed":false,"count":1,"inclusive_ns":100,"exclusive_ns":100}
```

Nanoseconds below are nonnegative integers relative to the recorder start, never wall-clock timestamps. Document ordinals are positive integers, run-local. A document row is exactly:

```json
{"document":1,"page_kind":"archive","domain":"materials","course":1,"start_ns":100,"commit_ns":200,"end_ns":500,"end_reason":"next-document"}
```

`commit_ns` is nullable for failed/uncommitted navigation. `end_reason` is `next-document`, `session-end`, or `failed`. Document duration is `end_ns-start_ns`; commit latency is `commit_ns-start_ns` when known. Final session interval is censored; never present it as a completed dwell interval. Span `document` identifies the document active at span entry, not every document crossed by a long span.

`coverage` is exactly `{ "lock_ns": N, "covered_ns": N, "unattributed_ns": N }`: union of lock intervals, union of all non-lock spans clipped to those intervals, and their difference. Do not sum overlapping inclusive spans. This is gross coverage, not proof that a broad auth/roster envelope explains its internals; acceptance also requires a leaf/exclusive wait breakdown. Document dwell overlaps spans and is **never** added to phase totals.

Retain the 4096 span budget; add a separate 4096 document budget. Either overflow increments existing `dropped_events`; no unbounded request history. An incomplete profile cannot pass attribution/overhead acceptance. Disabled mode installs no listeners, reads no profiling clock and allocates no event records. Validate enums/ordinals at API boundaries as existing `ValueError` programmer errors; preserve existing timeout, cancellation and command error codes. Caught timeout spans retain `failed=true` under existing recorder semantics; profile outcome is not a replacement for command status.

Migrate `tools/profile_sync.py::_safe_profile` to v2 only, validate exact key sets, enums, numeric types (reject bool-as-int), ordering, unique document ordinals and references, and reconstruct safe output. Unknown keys invalidate the profile instead of being copied. Update synthetic profile fixtures and tests together; do not add a v1 compatibility shim. Keep report schema independent from embedded profile schema.

### Work unit A2 — every wait/settle

Wrap each logical wait exactly once, including implicit action auto-wait, response completion, event/future waits, retry/poll loops, final settle, and cleanup waits on the sync path. Preserve existing timeout values, ordering, exception handling and return values. For topbar polling instrument the whole bounded poll, not each 100 ms sleep. Existing enclosing operation spans remain; introduce children only where they explain distinct waits. No global monkeypatch of Playwright or asyncio, and no automatic second span on every `bounded` call (which also wraps extraction).

Required inventory: the current-behavior callsites above plus `browser.py:121-137,289-290,386-400,480-540` for SSO, connection and cleanup. Instrument login direct `asyncio.wait_for` calls as well as provider `bounded` calls. Span the full response expectation scope where its context-manager exit waits, not just its enclosed click. A whole `auth` span alone does not satisfy this inventory.

Set labels around each course/domain traversal in `sync_all`; propagate them into shared collectors. `_settle` gets `idle`/`load`; `_return_to_roster` gets document navigation plus its two individual settles. Menu waits and topbar waits remain separate. When B removes a wait, its span disappears rather than timing a no-op. B retains readiness timing and identity proof. C retains nested response/load spans only on paths actually taken. Never put a broad new wrapper around all traversal merely to make unattributed time zero.

### Work unit A3 — passive document timeline

Replace the count-only listener in `browser.open_session` with enabled-only request/response/navigation/failure listeners on the selected sync page. Count only **main-frame document requests**, not frames, popup documents, assets, telemetry or same-document URL changes. Preserve `counts.documents` as committed document count with this clarified definition; redirects are separate request-start rows but only the final committed request increments the count. Track request objects only while needed to correlate response/redirect/commit, then discard them; never serialize them.

Timestamp each main-frame document request start in Python with the same monotonic clock as spans. Close the previous row at the next document start; attach commit time only to the request whose document actually committed, using request/response/redirect correlation, not URL equality alone. A late response must not mark another navigation committed. Failed navigation remains an uncommitted row. Ignore the initial pre-existing blank page. Close the final row at session teardown entry, before closing browser resources; detach every listener in finally, including partial session setup failure.

Classify paths transiently through an explicit fixed mapping to the page-kind enum (existing section paths: `sync_all.py:35-40`; course-entry matching: `145-192`; roster target: `104-110`). Unknown paths become `other`; never retain origin, path, query, fragment, request headers or response bodies. Domain/course are snapshotted from explicit traversal labels at request start, not an event callback's inherited task context. Existing-session authentication and redirects may legitimately have null course/domain. Do not synthesize selectors, probe the DOM, evaluate performance scripts, add polling, wait for network idle, intercept requests, or install routes for profiling.

## What must stay true

Identity, stale-cache semantics and publication order remain unchanged: course selection proof at `sync_all.py:185-193`; failure/stale staging at `421-450`; publish after session close while lock held at `475-514`. Sanitization follows `docs/specs/constitution.md:7-20`; never suppress page requests. No new CLI flags, envelope fields, retry behavior or user-visible error codes. The single stderr profile line remains opt-in and emitted on failure as well as success.

## Tests and acceptance

Implementation commands (from repository root):

```sh
python -m pytest tests/test_profiling.py tests/test_profile_sync.py
```

Extend those existing tests; add a focused `tests/test_profile_documents.py` for the passive browser lifecycle and run it explicitly. Required behavioral cases:

1. Fake monotonic clock: nested and overlapping waits, timeout/cancellation, context reset, union coverage, disabled zero clock reads, both caps, finish idempotence. Assert exact known durations, not just nonempty output.
2. Synthetic event stream: two navigations to the same URL, redirect chain, late response, failed navigation, hash navigation, subframe/popup traffic, teardown during navigation. Assert correct starts/commits/censoring, committed count and listener removal. Test stale callback context versus explicit current labels.
3. Sensitive sentinel strings in URLs, selectors, exception messages, course IDs and payloads never appear in serialized output. Sanitizer rejects extra fields and invalid enums/references and accepts all legitimate v2 rows.
4. Extend a synthetic sync scenario to exercise roster return, course entry, all domains and modal restore: compare catalogs/errors/request action order with profiling off/on, then assert each observed wait is attributed. Do not use source-text or forwarding-only tests.
5. Run the real CLI against a local synthetic LMS/browser scenario, not only mocks; observe one safe profile line and matching command results. This is the smoke proof; no real LMS traffic in offline verification.

Overhead gate: same candidate revision, same browser mode and identical synthetic workload/state, profiling **off versus on**, at least five complete interleaved pairs; independently time whole CLI processes (including profile serialization), compare medians using `(on-off)/off ≤ 0.05`. Also repeat against the previous recorder enabled to identify incremental cost. Reject mismatched results or dropped events. No artificial sleep added to dilute overhead; exercise the observed shape (seven courses, 68 documents and 21 modal restores) and a smaller CPU/event-heavy case separately. Record host/browser conditions and dispersion; a noisy/borderline result is unresolved, not a pass. Existing `tools/profile_sync.py` has controlled comparison tests at `tests/test_profile_sync.py:24-68,83-137`; extend its offline synthetic workload rather than run repeated live trials.

## Next single full-path live verification

Use the orchestrator's one owner-approved run under `docs/specs/live-run.md:3-17`, not a separate run for A. Its sync command includes `--profile`; fetch/download state checks remain part of that same full-path run. Record private independent request/page timestamps alongside profile v2, CLI wall/exit status, catalog equivalence, mode and revision.

Report wait/settle inclusive and exclusive totals by domain/page kind/course ordinal, document start-to-start dwell and commit latency by page kind, final censored interval, zero dropped events, and gross lock coverage. Compare passive document counts/timings with independently recorded main-frame events. Target: unexplained lock time ≤5% of lock time, with no residual >1 s hidden solely by a new broad wrapper; investigate any larger residual before declaring attribution complete. The approximately 90 s baseline gap need not recur exactly. B measures removed idle time against added readiness time; C compares restore envelope time and actual document count, without adding dwell to span totals.

Public reports cite only owner-held sanitized evidence (2026-09-27) and aggregates. A single live run cannot establish a causal ≤5% on/off overhead bound; report the offline paired gate and the live attribution check separately. Any further live comparison needs owner approval.

## Open questions

- Actual readiness contributions and residual CPU/protocol time remain unmeasured until the instrumented run; do not promise that all 90 s is network idle.
- Verify redirect/commit event correlation on the supported Playwright/browser version with the synthetic browser smoke before shipping; request/response event order must not be guessed from URL matches.
- If the owner needs a live causal overhead guarantee rather than the controlled offline bound, an additional approved paired live comparison is required; the existing single-run evidence cannot supply it.
