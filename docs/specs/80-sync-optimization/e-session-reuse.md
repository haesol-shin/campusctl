# E — Reuse authenticated sessions and explicitly selected fetches

## Goal and expected saving

Reuse live LMS authentication without trusting cached login state; implement the owner-approved explicit multi-ID fetch contract and support local headless fetch. Owner decisions below were recorded on 2026-09-27.

Owner-held sanitized evidence (2026-09-27): sync took 188 s, its `auth` span took 9.0 s, and individual fetches took 11–14 s, mostly login plus course entry. **These are not measured savings.** The current login helper already skips credentials for an authenticated landing page. Incremental cross-run savings may therefore be zero. A batch could amortize browser startup and landing verification over several items; it cannot eliminate identity checks, course entry or package transfer. Measure before assigning a numerical saving; do not claim the entire 9.0 s disappears.

## Current behavior and constraints

All file references are repository-relative.

- `src/campusctl/browser.py:479-541` connects to the existing default CDP context or launches Chromium with the persistent provider profile. `:597-602` closes the local context, or only a campusctl-owned CDP page, then disconnects. A persistent profile is not proof that server/session cookies survive restart.
- `src/campusctl/providers/cnu/login.py:64-99` already navigates to the requested landing page, waits for course links or the login form, and returns without reading credentials when course links are visible. `:99-119` reads and clears credentials only on the form path. `:178-247` submits once, handles rejection/terms/password-change and waits for the landing selector. There is no persisted application-level “authenticated” flag to add.
- `src/campusctl/providers/cnu/sync_all.py:349-374` opens one owned page under the lock, calls this helper inside `auth`, then settles SSO and discovers the roster. Thus a nonzero `auth` span does not prove credentials were submitted.
- `src/campusctl/commands/assignments.py:81-145,149-193` and `src/campusctl/commands/notices.py:105-167,171-215` validate one cached ID and one nonexistent output destination, open one browser session, authenticate, capture one detail and build one package before closing.
- Detail readers already accept a caller-owned authenticated page: `src/campusctl/providers/cnu/assignment_detail.py:161-241` and `src/campusctl/providers/cnu/notice_detail.py:219-220,263-341`. They still enter the selected course and verify detail identity. Notice capture removes its response listener in `:394-395`.
- `docs/contracts/cli.md:24-26,79` requires **exactly one** fetch ID and returns `result.source_package`; `:41` forbids fetches during sync. `:103-121` specifies lifecycle, lock, credential-free authenticated reuse and authentication errors. `auth status --check` is a credential-provider check, not an LMS session test (`:40,115`).

## Design

### E1 — Harden and prove existing cross-run reuse; no CLI change

Owner of implementation: `src/campusctl/providers/cnu/login.py`, `tests/test_cnu_login.py`, and a focused browser-session fixture in `tests/test_browser.py` if needed. Do not edit `sync_all._settle`, `_return_to_roster`, course switching, browser launch flags or profiling in this work unit. Slice A owns instrumentation; its `auth` measurement must retain the cost of verifying reuse rather than relabeling that cost as saved work.

Keep `ensure_logged_in(page, config, *, target_url, expected_selector, timeout_ms) -> None` and all callers. “Skip auth” means **skip credentials/form submission**, not skip the bounded live landing navigation that establishes freshness.

1. Continue using the same profile/default CDP context, navigation and timeout bounds. Do not export cookies, inject tokens, persist a Boolean login cache, create a daemon, leave local browsers running, or modify cookie expiry/session-cookie behavior.
2. Before changing login-page recognition, check the owner-held record for the actual landing/form origins and whether the form and landing selectors ever coexist. Do not ship a new allowlist from assumptions: that could break a legitimate SSO flow. Once verified, validate the committed origin structurally before trusting the landing or filling credentials; reject a lookalike form on an unrecognized origin with `lms-unavailable`. A legitimate distinct SSO form origin needs an explicit evidence-backed allowlist, not a wildcard. If these observations are absent, leave this hardening gated and prove the existing reuse path instead.
3. Subject to that recognition gate, read both landing and form visibility. A fresh expected landing with no visible login form is authenticated: return, with zero credential reads, fills or login clicks. A visible normal login form without the expected landing is unauthenticated/expired: run the existing single login attempt. Both visible or neither conclusively visible is unavailable, not a reason to force login. After submission, require the same verified origin/landing/no-form postcondition before returning. Do not replace the existing landing check with cookie presence or a URL-only heuristic.
4. Preserve `login-failed` and `login-action-required` as `user-action`/2; preserve credential-provider errors unchanged; navigation, inconclusive readiness and timeouts remain `lms-unavailable`/1 (`login.py:24-57,89-99,211-247`). No new public error codes.
5. Do not add mid-operation automatic login/replay. If expiry occurs after the entry probe, existing course/detail identity failures must prevent publication and retain current error/stale semantics. Reopening a notice can add another view, so an uncertain detail attempt must not be retried implicitly. A later explicit invocation can probe and authenticate normally.

This is bounded safety hardening and evidence of already-implemented reuse, not a claim to have newly implemented persistence. If live evidence shows the local profile loses authentication on close, record that as an unresolved LMS/browser lifecycle question; do not silently enable session restoration flags.

### E2 — Implement approved multi-ID fetch

**Approved by the owner (2026-09-27).** Extend the existing single-ID CLI contract to homogeneous explicit multi-ID fetch. An internal refactor with no reachable caller is not completion:

```text
campusctl assignments fetch ENTITY_ID... [--out DIR] [--json]
campusctl notices fetch ENTITY_ID... [--out DIR] [--json]
```

Required contract:

- Deduplicate IDs in first-seen order; validate every ID against its domain catalog before browser startup. Unknown IDs retain `entity-unknown`; missing catalog retains `catalog-missing`. Never interpret numbers, glob patterns or a whole course as selected details.
- `--out` keeps its existing single-package meaning. Reject it with more than one distinct selected ID using `usage-error`/2 before startup. Multi-item requests use existing default package destinations; do not reinterpret `DIR` as a parent folder.
- One distinct ID keeps the exact existing `result.source_package` shape, human rendering and exit semantics. More than one returns `result.items`, for example:
  `{"items":[{"entity_id":"<first-id>","outcome":"completed","source_package":{}},{"entity_id":"<second-id>","outcome":"failed","reason_code":"fetch-failed"},{"entity_id":"<third-id>","outcome":"completed","source_package":{}}]}`.
  The empty package object above denotes the existing full package object, not an allowable empty result. Allowed item outcomes: `completed`, `partial`, `failed`, `not-started`; only the first two carry `source_package`, and `partial`/`failed` carry `reason_code`. Envelope errors keep the established `{code,message,remediation}` shape (`docs/contracts/cli.md:125-140`).
- Use one session and its exclusive lock for the complete ordered queue. Resolve headed/headless mode through the existing preflight; local headless fetch is supported. Authenticate and settle SSO once at entry; run the existing detail capture and package builder serially for each item without nested `open_session` calls. Keep course entry inside each detail reader initially; no unverified “same course” shortcut. Build and publish each package before leaving that item's page.
- A usable package with resource omissions is `partial` with `resource-omitted`, and the queue continues. An **item-level failure** marks only that item `failed` with its existing `reason_code`, publishes no package for that failed item, and continues with the next ID. Identity mismatches, parse/content failures, attachment check failures and per-item detail timeouts are item-level failures. Preserve already published packages.
- A **session-level failure** stops the queue: mark the active item `failed` with its `reason_code` and remaining items `not-started`. Session-level failures are expired login or required login action, a closed browser/context, `session-busy`, and run-wide LMS unavailability. If session startup fails before any item starts, keep the existing ordinary error envelope without item records. Classify before generic exception normalization: `commands/assignments.py:37-53` otherwise maps untyped errors to `fetch-failed`. A per-item `browser-timeout` alone is not evidence of run-wide unavailability. Use observable login/action state, browser/context closure or a session-scoped failure; do not infer session expiry from an identity mismatch or parse failure. Preserve existing public codes (`login-action-required`, `lms-unavailable`, `session-busy`, or the existing browser error); report detected expiry as `lms-unavailable`, not rejected credentials. Clear each item's listeners and transient capture state before proceeding; the next item must establish its own course/detail identity.
- Never retry an item or reauthenticate/replay a possibly opened detail: notice views count. All items complete → `ok`/0. At least one package published and anything partial, failed or not-started → `partial`/1. Otherwise use the first failure's status and corresponding exit code, preserving all item errors in request order. Interruptions propagate with normal session cleanup; no rollback of published packages.
- Central emission must honor an explicit batch status override. Currently `_emit_response` forces every error list to `partial`, and `main` routes every list through `_emit_partial` (`src/campusctl/cli.py:304-320,841-844`). Change the error-list branch in `_emit_response` to use `status or "partial"`. In `main`, for assignment/notice fetch results containing `items`, compute the E2 status from published packages and ordered failures and call `_emit_response(..., result=result, errors=errors, status=batch_status)` rather than `_emit_partial`. With item errors but no published package, pass the first failure's status explicitly; retain **all item records and all errors**, and derive the exit code from that status using the existing `EXIT_CODES`. Do not collapse the list into one `error`, discard results, or change default partial handling for other commands or single-ID fetches.
- Implementation owner: `src/campusctl/commands/assignments.py`, `src/campusctl/commands/notices.py`, `src/campusctl/cli.py`, `tests/test_assignment_fetch_commands.py`, `tests/test_notice_fetch_commands.py`, `tests/test_fetch_commands.py`, `tests/test_cli_output.py`, and `docs/contracts/{cli,assignments,notices}.md`. Change parser, dispatch, central envelope emission, rendering and the existing async fetch wrappers together; do not add unused session APIs. Keep provider detail identities and package schema unchanged. Integration owns changelog updates.

Sync-plus-fetch is **rejected by the owner**. Sync must remain metadata-only: opening every synced notice would add a view and flip read state for each one (`docs/specs/constitution.md:18,25`). Do not add implicit fetches or a sync callback hook. Cross-command CDP context reuse already shares authentication without keeping a sync page alive.

### E3 — Local headless fetch

`HEADLESS_SUPPORT` enables `"assignments.fetch"` and `"notices.fetch"` for local Chromium. Both are supported following the successful G2 full-path run. `resolve_headless` gives an explicit `--headless` or `--headed` override precedence over `browser.headless`; CDP plus headless returns `headless-unavailable`/2 before locking or browser startup. Playback remains headed.

Fetch wrappers pass the preflight mode into their one browser session for the whole ordered queue. Headless does not bypass detail identity verification or change package semantics.

### Loopback telemetry — resolved outside campusctl

The owner resolves the loopback-port collision by retiring the local service that owns that port in the operator environment (decision 2026-09-27). campusctl does nothing: page requests remain unfiltered under `docs/specs/constitution.md:9,27`.

## What must stay true

- One non-blocking lock; `busy`/75 remains failure, never empty success. Local context closes; external CDP browser survives. No nested session (`browser.py:447-452`).
- Fresh authentication is not proof of course/item identity or of the configured account identity. Preserve all committed-course, selected-row and response checks (`assignment_detail.py:185-231`; `notice_detail.py:289-341`). Do not claim an account-name check exists.
- Sync remains metadata-only, with original stale retention and discovery failure semantics (`docs/contracts/cli.md:41,50,121`). Packages are never published from login pages or unverified details.
- No new coursework action or automatic replay. Notice reading effects remain owner-approved, including omissions for unobserved notice attachments (`docs/specs/constitution.md:18,25-26`). No credentials, cookies, tokens, URLs with secrets or raw responses enter diagnostics.

## Offline acceptance

Run focused tests only after implementation; from the repository root:

```sh
uv run pytest tests/test_cnu_login.py tests/test_browser.py
```

Extend behavioral fixtures (synthetic identities and `.invalid` hosts) for: a warm landing with a credential provider that would fail if called; an expired session that shows the login form and succeeds with one submission; rejected credentials; terms/password-change; unexpected origin with a lookalike form; both form and landing visible; redirect/timeout without either signal; post-login landing failure. Assert observable credential use, publication prevention and public error status, not source text or helper call wiring. Existing fake login scaffolding is in `tests/test_cnu_login.py:68-157`.

Use a throwaway local HTTP fixture and real Chromium persistent context to exercise close/reopen with a synthetic persistent authentication cookie, then expire it server-side and observe the login form. Exercise attachment to an external CDP context separately if available, including preservation of unrelated tabs. This proves browser lifecycle only, not real LMS cookie persistence. No live credentials or external network access belong in automated tests.

Add queue behavior tests to existing fetch command suites: preflight rejection prevents all browser activity; duplicate IDs open once; ordered successful packages; one partial package does not stop the next item; each item-level failure class on the second item leaves the first package intact and allows the third item to publish; each session-level failure class stops the queue and leaves later IDs unopened; unsupported multi-item `--out`; identity mismatch creates no package for that item; interruption releases the lock; no retry or duplicate notice read through recovery. Cover all-complete `ok`/0, published-plus-unsuccessful `partial`/1, and no-package results retaining the first failure's status even when later failures have a different status. Also exercise the real CLI parser against synthetic catalogs and a local browser fixture, including one-ID backward compatibility and JSON/human results.

Add public CLI envelope regressions in `tests/test_cli_output.py` using its existing `cli.main` coverage (`tests/test_cli_output.py:46-80,289-304`): no packages with first failure `user-action` emits `user-action`/2, first failure `error` emits `error`/1, and a session failure with status `busy` emits `busy`/75 when it is the first failure. Include mixed later statuses and assert every item and ordered error survives. With any published package and an unsuccessful item, assert `partial`/1 instead. Exercise JSON envelopes and human exit statuses, and preserve ordinary non-fetch error-list `partial` behavior; a provider-only test cannot catch the central-emitter defect.

```sh
uv run pytest tests/test_assignment_fetch_commands.py tests/test_notice_fetch_commands.py tests/test_fetch_commands.py tests/test_cli_output.py tests/test_browser_options.py
```

For E3, offline tests cover local headless configuration, explicit `--headless`, `--headed` override, and headless-CDP rejection before startup. Real local Chromium fixtures capture synthetic assignment and notice details without a display, checking identity and package contents; the live support evidence is recorded below.

## Live verification

Owner-held sanitized evidence (2026-09-28): the G2 full-path run passed with local headless Chromium, producing a complete notice package, two assignment fetches, and an official material download. Sync took 65.9 s (profile wall 65.3 s), notice fetch 13.8 s, the two-ID assignment fetch 19.5 s, and material download 14.0 s; per-invocation login took about 4.0–4.5 s. This satisfies the headless assignment and notice fetch support gate; it does not establish CDP headless support or answer whether local-profile authentication persists across restarts.

The run's approved notice reads can affect view/read state as ordinary reading. Do not retry a possibly opened detail or infer a new account-identity check from a successful package.

## Open questions / approval gates

1. Does the actual LMS keep its session across local persistent-context close/reopen, or only in a still-running CDP browser? The baseline `auth` duration alone cannot answer. Verify login/landing origins and selector coexistence before E1 recognition hardening. A live account-identity signal and an authenticated empty-roster marker are also unverified; do not invent selectors or weaken roster checks.
2. Local headless assignment and notice fetch are supported after G2. Multi-ID fetch is approved; sync-plus-fetch is rejected; loopback telemetry is resolved in the operator environment and requires no campusctl change.
