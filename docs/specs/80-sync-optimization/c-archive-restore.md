# C — Retain the proven archive list after attachment inspection

## Goal and expected saving

Skip the archive-menu reload only when the closed attachment UI leaves the same validated archive document and list intact. Otherwise execute the existing reload and page restoration. This is an optimization, not a new collection path.

Owner-held sanitized evidence (2026-09-27): 21 attachment restores caused 21 extra archive documents; archive-restore inclusive time was 53.1 s (35.8 s exclusive). Estimated ceiling: 21 × approximately 2.5 s, or approximately 52.5 s saved. This is not a measured improvement or a guaranteed budget; retained-list proof and genuine fallbacks reduce that ceiling.

## Current behavior and evidence

Paths below are repository-relative; line numbers describe the pre-change baseline.

- `src/campusctl/providers/cnu/materials.py:49-76,412-436`: `_ARCHIVE_STATE_JS` and `_archive_state` validate table completion, exact row count, total, page size, page number and selected topbar course ID. The topbar identity is accepted only when its normalized label matches exactly one dropdown course. `posts` preserves DOM order and includes board-item IDs and titles.
- `materials.py:298-339,376-409`: section entry/list navigation is bound to a committed archive document, one subsequent list POST, archive Referer, successful response and JSON payload. This proof must remain intact.
- `materials.py:481-542`: pages are enumerated in order, attachment metadata is read per post, and duplicate post/file identities fail. No attachment is downloaded by enumeration.
- `materials.py:104-112,543-557`: cleanup closes the modal and removes backdrops; then every post unconditionally clicks the archive menu, restores pagination and compares the full restored `posts` list. Cleanup's intent is not evidence of its resulting DOM state.
- `materials.py:184-212,498-502,558-559`: the per-post `_RequestWindow` observes requests and archive commits and is closed in `finally`. Its archive-only commit list is insufficient to prove that no navigation away occurred.
- `tests/test_cnu_materials.py:49-199,247-249,308-330,348-373`: fake pages cover modal cleanup, multipage restoration, wrong-course restores and failure cleanup. Some assertions currently assume every post causes a reload.
- `docs/specs/constitution.md:7-11` and `docs/specs/70-sync-performance/design.md:20-22`: completeness and verified identity precede speed; a failed course retains previous domain rows rather than publishing partial results.

## Design and contracts

### Ownership and work units

One implementation worktree owns this slice, in this order:

1. `src/campusctl/providers/cnu/materials.py`: extend archive state evidence and implement conditional restoration in `enumerate_archive`; keep `_restore_archive_document` as the unconditional fallback helper.
2. `tests/test_cnu_materials.py` and, only where needed, `tests/fixtures/lms_sources/materials_archive.json`: behavioral regressions and fixture migration below.
3. Update this spec's verification outcome after integration. Release integration owns shared changelog/index changes.

No changes to `sync_all.py`, publication, CLI, file transfer or public JSON schemas. Slice B owns `_RequestWindow` completion/readiness changes; preserve its completion API and the existing entry/list-response validation. Slice A owns added wait instrumentation; preserve those nested spans. The existing `archive-restore` span remains the envelope for proof **and** any fallback, including pagination. No new profile counters or public error codes are required.

### Evidence carried across the modal

Add a private helper `_archive_unchanged(page, *, document, expected_page, expected_total, expected_course_id, expected_state, activity, after) -> bool`.

- Immediately before the icon click, retain a bounded Playwright `JSHandle` to the current `document` using `page.evaluate_handle("() => document")`. Re-observe `_archive_state` against that handle and the expected course/page/total; require its `posts`, `page_size` and `row_count` to equal the already validated page snapshot. Record the request baseline before this observation. The handle is per post, never persisted or shared between pages.
- Extend `_ARCHIVE_STATE_JS` with an optional expected-document argument and internal fields `same_document: boolean`, `archive_path: boolean`, and `modal_clear: boolean`. Compute these and existing list/topbar fields in the **same** evaluation. `same_document` means `document === expectedDocument`; `archive_path` means `location.pathname === '/std/archive'`; `modal_clear` means no element matches `_MODAL`, no `.modal-backdrop` exists (including hidden backdrops), and the body lacks `modal-open`. Absence of an optional expected document must never count as proof.
- Extend `_archive_state` with optional keyword `expected_document=None`, passing the handle into the state evaluation. Existing required validations and callers retain their meaning. The additional booleans are internal only and are checked by the retained-list proof, not used to weaken ordinary validation.
- A same URL, unchanged topbar or equal post IDs alone is insufficient. A different committed document at the same URL must fail the retained-document comparison. A navigation away and back must not become eligible merely because the old document was restored.
- Playwright's [JSHandle contract](https://playwright.dev/python/docs/api/class-jshandle) permits passing handles to `page.evaluate` and auto-disposes them on frame navigation/context destruction. A disposed old-document handle therefore means fallback, not a retry with a new handle that could erase the evidence of navigation.
- Do not add a document-global token, request interceptor, new endpoint, fixed sleep or load/network-idle wait for this proof. Keep the existing response-completion boundaries, including slice B's replacement where applicable.

### Decision after closing the attachment UI

Keep modal/inline inspection and `_CLOSE_MODAL_JS` unchanged. In the current `finally` restoration block:

1. Run existing modal close; then evaluate `_archive_unchanged` under `archive-restore`.
2. Return `True` only if the pre-click evidence was complete and the post-close observation proves **all** of:
   - identical retained `document` and archive pathname;
   - selected topbar course equals `course['course_id']`, using the existing unique label-to-dropdown identity proof;
   - expected page number, total, page size, row count and completed-table checks;
   - exact ordered board-item IDs; additionally retain current full `posts` equality (including titles), rather than weakening the previous restore invariant;
   - no visible attachment modal, no backdrop element and no body modal-open state;
   - no main-frame navigation request and no main-frame archive-list POST recorded by `activity.requests[after:]`. Inspect main-frame navigation requests independently of `activity.commits`, which records only archive commits. These requests disqualify retention even while pending; unrelated assets/telemetry do not. Attachment-list requests remain allowed and must already have completed through the existing attachment flow.
3. If true, leave the list in place: no archive click, list reload or re-selection of the current page. Continue to the next original post.
4. Otherwise run the existing `_restore_archive_document(page)`, `_select_page` when page > 1, and `_archive_state` validation with the expected course/page/total. Require full restored `posts == posts` exactly as today. Also require the resulting modal/backdrop clearance before continuing; a reload is not itself proof of clearance.
5. Release the document handle in `finally` and retain `activity.close()` on every exit. Reacquire evidence for each post, including after a fallback; never reuse a handle from the preceding document.

Unavailable evidence is a cache miss, not successful proof: missing topbar, ambiguous identity, missing fields, DOM mutation, navigation/context destruction or a bounded proof-observation timeout routes to the existing fallback. Handle capture failure makes that post ineligible for the fast path. Catch only observation/validation failures inside the speculative proof (Playwright observation errors, `ValueError`, and `CampusError` with `browser-timeout`); unrelated errors and cancellation propagate. Do not wrap extraction or fallback in that catch. Best-effort disposal of a destroyed handle must not mask the original collection/restore failure.

### Errors and invariants

- Fallback failures keep current error behavior: failed identity/list equality or unresolved controls fail with `course-sync-failed`; duplicate file identity retains `item-identity-missing` (`materials.py:526-540,550-557`). Existing `_archive_state` validation errors continue to propagate to the traversal failure boundary (`src/campusctl/providers/cnu/sync_all.py:435-446`). Residual modal/backdrop after fallback is `course-sync-failed`.
- No new success envelope, partial result publication, silent empty-list conversion or catch-and-continue across failed posts. Modal cleanup cannot turn an extraction error into success.
- Keep successful-empty behavior, pagination limits, total-count accounting, metadata names and downloadable classification unchanged (`materials.py:132-177,473-480,560-562`).
- The committed section/list-response proof remains required even for the first fast path; a DOM snapshot cannot replace it. No archive-detail route or direct API collection is introduced (`materials.py:527-533`).

## Offline acceptance and commands

Run from the repository root: `uv run pytest tests/test_cnu_materials.py`. Dependency/test configuration is in `pyproject.toml:13,18-25`. Do not run the full suite mid-integration.

Required deterministic cases (synthetic IDs and `.invalid` hosts for new fixtures):

1. Multiple posts in one unchanged document: exact expected file IDs, filenames and post associations; zero restore-menu clicks, no extra archive-list POSTs, no residual modal/backdrop. Preserve attachment request-completion coverage.
2. Two or more pages: correct complete ordered results; normal page transitions occur once; unchanged post inspection on page 2 does not reset to page 1.
3. Parameterize each independent proof failure: wrong/missing/ambiguous topbar identity, changed page number, changed total/page size/row count, reordered/replaced post IDs, title-only mutation, incomplete table, visible modal, hidden or visible backdrop, body modal-open, wrong path, missing handle, destroyed execution context. Each must attempt the existing fallback; corrected fallback state succeeds with exact expected rows.
4. Replacement document with identical URL/topbar/list; navigation away/back; pending main-frame navigation; same-document archive-list refresh even when IDs match. None may use the fast path. Child-frame activity alone must not masquerade as a main-frame document change.
5. Invalid fallback response, wrong restored course, changed restored posts or residual overlay fails the entire collection; listeners and handles are released. Extraction failure still propagates after cleanup even if retention succeeds.
6. Completed empty archive performs no attachment restore. Inline controls after modal timeout retain their current selected-post association and may skip reload only with the same complete proof.

Migrate `failed-restore` and wrong-restore-course fixtures to **force proof failure first**, then fail the reload; otherwise their old setup no longer exercises their named failure. Delete the incidental unconditional two-reload assertion at `tests/test_cnu_materials.py:247-249`; replace it with case 1's consumer-visible results and the optimization's no-reload requirement, not a renamed implementation assertion.

FakePage alone cannot prove DOM identity semantics. Add or run a throwaway offline Playwright smoke scenario served on loopback: a table/topbar/pagination fixture, two attachment controls and a close button; exercise the real state JavaScript, retain/reopen a modal, then replace the document at the same URL with identical markup. Observe retention only for the first case and fallback for the replacement. Include an actual leftover hidden backdrop and a changed page/ordered-ID case. Record results; remove the temporary harness. No LMS or live credentials are needed for this smoke proof.

## Offline verification outcome

The targeted archive tests pass with synthetic course and attachment identities, including every retained-list fallback trigger, successful empty archives, per-page continuation, and extraction errors. The profile distinguishes the two decisions using existing spans: every inspected post contributes an `archive-restore` span, while only reloads contribute nested `document-commit` work and an additional archive document. No new profile fields are emitted.

A loopback-only headless Chromium smoke exercised the real archive state JavaScript with two attachment controls. Closing a modal retained the same document and produced no extra archive document; replacing that document at the same URL invalidated the old handle and a fallback produced one new document. A leftover hidden backdrop and a changed page/ordered-ID snapshot each rejected retention and produced a fallback document. This offline proof is not a live LMS measurement. Playwright cleanup callbacks reported closed targets after the assertions; the observed decisions and document counts completed successfully.

## Next single full-path live verification

Follow `docs/specs/live-run.md:3-17`: owner-authorized integrated run includes all-domain sync, both fetches and one official download; no separate speculative LMS run for this slice.

Record, using slice A's document observations and the existing restore spans:

- attachment inspections, retained-list decisions and fallback attempts/successes; record boolean proof outcomes and anonymous ordinals only, never document handles or private identities;
- archive document count split between initial entries and restoration reloads; baseline was 7 entries + 21 reloads = 28 documents;
- archive-restore count and inclusive/exclusive duration, sync wall time and browser mode; compare in the same mode;
- exact catalog equality/differences checked owner-side, correct next-post/page progression, no obstructing overlays, and any per-course failure/stale outcome.

Acceptance: every retained decision has all proof predicates true, ordinary metadata and file selection still work end to end, and any observed failed predicate invokes validated fallback. Fewer reloads and lower elapsed time are measured outcomes, not grounds to bypass a failed predicate. An unobserved fallback branch is covered offline, not grounds for an unauthorized second live run. Commit only aggregate results cited as owner-held sanitized evidence (2026-09-27).

## Open questions

- How often does the deployed LMS preserve the exact document/list after closing the modal? The baseline measured unconditional reloads, not eligibility for retention. The next integrated run determines the realized saving; no minimum skip rate is assumed.
- Does close trigger a delayed archive-list refresh or main-frame navigation in any course? Such observed activity disqualifies retention; the single live record must capture this behavior and feed sanitized regressions if needed.
