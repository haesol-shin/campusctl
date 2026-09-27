# E — Reuse authenticated sessions and explicitly selected fetches

## Goal and expected saving

Understood as: avoid redundant credential submission when a browser still has a live LMS session, detect expiry without trusting cached authentication, and evaluate—not silently introduce—a multi-fetch CLI contract. Treat the reported loopback service collision separately from campusctl's own requests.

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

### E2 — Multi-fetch proposal, blocked on owner approval

**Do not implement this unit until the owner approves the CLI change.** There is no existing public command that can request several fetches within one open session. An internal refactor with no reachable caller is not completion. Recommend homogeneous explicit multi-ID fetch rather than adding fetch side effects to sync:

```text
campusctl assignments fetch ENTITY_ID... [--out DIR] [--json]
campusctl notices fetch ENTITY_ID... [--out DIR] [--json]
```

Proposed decisions for approval:

- Deduplicate IDs in first-seen order; validate every ID against its domain catalog before browser startup. Unknown IDs retain `entity-unknown`; missing catalog retains `catalog-missing`. Never interpret numbers, glob patterns or a whole course as selected details.
- `--out` keeps its existing single-package meaning. Reject it with more than one distinct selected ID using `usage-error`/2 before startup. Multi-item requests use existing default package destinations; do not reinterpret `DIR` as a parent folder.
- One distinct ID keeps the exact existing `result.source_package` shape, human rendering and exit semantics. More than one returns `result.items`, for example:
  `{"items":[{"entity_id":"<full-id>","outcome":"completed","source_package":{}},{"entity_id":"<full-id>","outcome":"failed","reason_code":"fetch-failed"},{"entity_id":"<full-id>","outcome":"not-started"}]}`.
  The empty package object above denotes the existing full package object, not an allowable empty result. Allowed item outcomes: `completed`, `partial`, `failed`, `not-started`; only the first two carry `source_package`, and `partial`/`failed` carry `reason_code`. Envelope errors keep the established `{code,message,remediation}` shape (`docs/contracts/cli.md:125-140`).
- Use one headed session and its exclusive lock for the complete ordered queue. Authenticate and settle SSO once at entry; run the existing detail capture and package builder serially for each item without nested `open_session` calls. Keep course entry inside each detail reader initially; no unverified “same course” shortcut. Build and publish each package before leaving that item's page.
- A usable package with resource omissions is `partial` with `resource-omitted`, and the queue continues. The first fatal item failure stops the queue; preserve earlier packages and mark later items `not-started`. Do not reauthenticate/replay a possibly opened detail. All-complete returns `ok`/0; omissions or failure after any published package return `partial`/1; a failure before any package retains its underlying status/exit code. Pre-session errors use the existing ordinary error envelope without item records. Interruptions propagate with normal session cleanup; no rollback of published packages.
- Implementation owner after approval: `src/campusctl/commands/assignments.py`, `src/campusctl/commands/notices.py`, `tests/test_assignment_fetch_commands.py`, `tests/test_notice_fetch_commands.py`, `tests/test_fetch_commands.py`, and `docs/contracts/{cli,assignments,notices}.md`. Change parser, dispatch, rendering and the existing async fetch wrappers together; do not add unused session APIs. Keep provider detail identities and package schema unchanged. Integration owns changelog updates.

Reusing an active sync session is **not approved**: the current contract says metadata only, sync can be headless while fetch must be headed, and sync has independent domain/stale outcomes. Do not add implicit fetches or a sync callback hook. Cross-command CDP context reuse already shares authentication without keeping a sync page alive.

### Loopback telemetry: operator-environment issue, owner decision

Owner-held sanitized evidence (2026-09-27) records 1,366 page telemetry POSTs to a loopback port during 9,769 sync requests. The reported collision with an unrelated local listener is an operator-environment issue: loopback resolves in the browser's host environment, and a page can contact a service listening there. Do not publish the port, service identity, payloads or private host configuration.

Recommendation pending owner decision: **no campusctl request interception or telemetry-specific behavior**. `docs/specs/constitution.md:9,27` requires letting the page issue ordinary requests, and `docs/specs/70-sync-performance/design.md:18` forbids operation-scoped suppression. Do not block/redirect those POSTs, inject script to disable telemetry, reserve the port, kill a local service, or import selected-download policy into page traffic. The owner may isolate the browser environment or reconfigure their unrelated service outside campusctl. Whether to add sanitized troubleshooting guidance or another operator-facing action is explicitly open; a request-filtering proposal would require changing the constitution first. Do not attribute a performance gain to dropping this traffic.

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

If E2 is approved, add queue behavior tests to existing fetch command suites: preflight rejection prevents all browser activity; duplicate IDs open once; ordered successful packages; one partial package does not stop the next item; fatal second item retains first and leaves third unopened; unsupported multi-item `--out`; identity mismatch creates no package; interruption releases the lock; no duplicate notice read through recovery. Also exercise the real CLI parser against synthetic catalogs and a local browser fixture, including one-ID backward compatibility and JSON/human results.

```sh
uv run pytest tests/test_assignment_fetch_commands.py tests/test_notice_fetch_commands.py tests/test_fetch_commands.py
```

## Next single full-path live verification

Follow `docs/specs/live-run.md:3-17`: one owner-authorized recorded full-path run; do not launch additional exploratory LMS commands. Run the existing sync, selected notice fetch, selected assignment fetch and material download in sequence, using explicit owner-selected IDs and fresh output destinations. Record the browser mode, whether the context/profile was warm, each step's wall time, whether the visible login form appeared, whether credentials were submitted, and landing readiness separately from the `auth` total. Record existing notice read/view and assignment submission before/after states, package identity/completeness and preserved catalogs. Keep raw evidence owner-held.

The follow-on fetches are the natural cross-command reuse check. If the run uses only CDP, mark local restart persistence unverified (and vice versa). Do not log out, clear cookies or force real expiry merely to obtain another sample; offline tests cover expiry branches. If E2 is approved, the owner must approve an expanded fetch queue within this same full-path run; otherwise do not fetch extra details. Record aggregate loopback traffic and whether the collision remains, without suppressing requests. A second run needs owner approval, not an automatic retry.

## Open questions / approval gates

1. Does the actual LMS keep its session across local persistent-context close/reopen, or only in a still-running CDP browser? The baseline `auth` duration alone cannot answer. Verify login/landing origins and selector coexistence before E1 recognition hardening. A live account-identity signal and an authenticated empty-roster marker are also unverified; do not invent selectors or weaken roster checks.
2. Approve or reject E2's explicit multi-ID grammar, single-ID compatibility, multi-item result shape, fail-stop semantics and `--out` restriction. Until approved, ship only E1; multi-fetch remains explicitly unimplemented, not a claimed saving.
3. Should campusctl do anything about the loopback collision beyond leaving page behavior untouched? Recommend operator isolation/reconfiguration; sanitized troubleshooting documentation requires the owner's decision. Filtering is incompatible with the current constitution.
