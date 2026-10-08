# B — Page readiness, not network quiet

## Goal and expected saving

Replace global network/load-state settling in sync and selected-detail fetch with bounded waits for the page that will actually be consumed. Understood as: accelerate navigation without weakening course/item identity, completeness or stale-cache behavior; background requests continue untouched.

Owner-held sanitized evidence (2026-09-27): full sync took 188 s over 68 main-frame documents; about 90 s of lock time had no child span. Render-after-commit median/max was roster 2528/4029 ms, course-entry topbar 1519/1849, entry lecture link 1894/2026, lecture topbar 686/1645, lecture rows 1973/2552, notices 1564/1960, assignments 1565/1566, archive 1536/1944; to-do grid rendered in 563 ms. Fetch took 11–14 s each. These are readiness observations, not complete-page proofs or timeout values. Planning estimate: saving one conventional 500 ms quiet interval per document would save about 34 s; actual savings may be much smaller because waits overlap rendering. Do not add this estimate to C's eliminated-document savings or claim that all unattributed time is removable.

## Current behavior and source of truth

All source paths below are relative to `src/campusctl/` unless stated otherwise.

- `providers/cnu/sync_all.py:94-118,145-207,218-262`: `_settle` waits for `networkidle` before/after selections, roster returns, sections and to-do; roster `goto` waits for `domcontentloaded` before waiting for links. Selection already proves one selection POST, one subsequent entry document, one commit and the topbar identity.
- `providers/cnu/course_context.py:10-37,40-68`: 7 s menu/topbar timeout, 15 s response timeout; topbar identity resolves one dropdown entry by the displayed name, not a guessed ID. `prepare_course_section` currently waits only for a menu.
- `providers/cnu/sync.py:66-94`: lecture collection proves route/topbar and waits for `.learningRow`; a row timeout currently produces an empty result.
- `providers/cnu/assignments.py:18-51,127-215,240-329`: task response capture observes all requests, waits for all pending requests and `networkidle`, then checks task table/course/response count. An unreadable JSON body currently permits `count=None`.
- `providers/cnu/notices.py:323-419,531-647`: to-do and board captures use `networkidle` to give response arrays time to fill; to-do already distinguishes rendered rows from a visible empty marker. Board validates both list responses, their request sequence and course identity.
- `providers/cnu/materials.py:49-75,184-259,298-338,342-459`: archive captures use all-request pending sets and `networkidle`; complete page proof is `_archive_state`, not the presence of an attachment icon. Attachment-list lookup currently treats no observed request as an optional empty metadata result.
- `providers/cnu/assignment_detail.py:161-241`: fetch settles before course selection, binds exactly one task link, verifies both detail responses and waits for `.card-body h4`.
- `providers/cnu/notice_detail.py:248-280,310-362`: fetch settles board/detail globally before validating list/detail responses. Detail content comes from verified response HTML, not a rendered-detail DOM extractor.
- Fetch command callers are `commands/assignments.py:157-176` and `commands/notices.py:180-198`; preserve their adapters and error wrapping. `browser.py:22,172-180` supplies the 10 s protocol bound and `browser-timeout`.
- Governing contracts: `docs/specs/constitution.md:7-20,27`, `docs/specs/README.md:16-20`, `docs/specs/70-sync-performance/spec.md:9-19`, and `docs/specs/live-run.md:3-35`.

## Design

### Shared readiness contract

Add `providers/cnu/readiness.py`:

```python
async def wait_page_ready(
    page: Any,
    page_kind: str,
    *,
    expected_course_id: str | None = None,
    domain: str | None = None,
    ordinal: int | None = None,
) -> None: ...
```

Supported `page_kind` values below are a closed set; reject unsupported values with `ValueError`. This helper proves route, an appropriate rendered surface and, on course pages, the existing unique topbar identity. It does **not** prove response completeness or list cardinality: collectors retain those responsibilities. Do not add a generic selector parameter or an arbitrary delay. Require `expected_course_id` for course-entry/lecture/assignments/notices/archive. A resolved different ID fails immediately; an absent/unresolved ID may wait within the deadline. Check expected origin using existing provider origin configuration and exact route before and after the predicate; never accept a login redirect or another committed document merely because it contains a matching selector. Navigation callers establish the new commit before invoking this helper; same-document pagination instead proves its new page state.

Use one 7 s total deadline for each helper invocation (existing menu/row limits), including identity polling; nested waits consume the remaining deadline, not fresh 7 s allowances. Keep 15 s relevant-response deadlines and 10 s protocol-operation bounds. Normalize Playwright/async timeout exceptions through `bounded` to `browser-timeout`. The existing lecture-row timeout-to-empty behavior is the sole exception: the lecture collector owns it after verified route/topbar checks, as specified below; identity or navigation timeouts must never become empty success. No sleeps based on the measured render times and no retries of page actions. Retain bounded navigation/response operations, but change roster `goto(..., wait_until="domcontentloaded")` to `wait_until="commit"` followed by readiness. Do not alter authentication/SSO internals: E owns them.

| Kind / route | Rendered surface and completion owner |
| --- | --- |
| roster / `/std/myLecture` | Attached `COURSE_LINK_SELECTOR` (`[data-act="moveLecture"]`, `courses.py:9`); extraction must still require addressable IDs. Keep full roster extraction/duplicate checks in `_roster`. This is not evidence of an empty enrollment. |
| course-entry / `/std/lecture` | Attached `a[href="/std/course"]` **and** uniquely resolved `#topbarCurrentLecture` identity through the existing dropdown resolver. Next action separately requires its own menu link. |
| lecture / `/std/course` | Verified route and course identity; keep the bounded `.learningRow` wait in the collector, not the shared helper. Extract every rendered row, or preserve successful empty-course behavior when the row wait alone expires. No invented empty marker. |
| assignments / `/std/task` | Attached `#table_list tbody#tbody`, plus identity. Nonempty completion waits for expected task count in `a[data-act="detail"][data-id]` rows; empty success requires the completed, bound task response to prove zero and an empty rendered tbody. A tbody shell alone is never complete. |
| notices / `/std/notice` | Attached `tbody#table-body`, plus identity. Collector waits for rendered `tbody#table-body > tr` content to match the two verified list responses using existing board normalization. No unconditional first-row wait for a verified zero-item board. |
| archive / `/std/archive` | Attached `#table_list #listBody` and `#totalCnt strong`, plus identity. `_archive_state` must additionally prove completed flag, current page, total and exact expected row count; zero requires visible `#listBlankDiv`. `[data-act="file"][data-boarditem_no]` is an observed useful row signal, not mandatory for empty/no-attachment pages. |
| todo / `/std/todo` | Existing function predicate: `#noticeList .tabulator-row` or visible `#noticeNoData` within `#noticeList`. Preserve verified response/coverage and pagination checks. |
| assignment-detail / `/std/taskView` | Visible `.card-body h4`; caller proves selected task and course using both completed detail responses before extraction. |

Selectors above are grounded in `course_context.py:10-24`, `sync_all.py:112-117,179-191`, `sync.py:78-94`, `assignments.py:18-51`, `notices.py:382-418`, `notice_detail.py:312-324`, `materials.py:49-75`, and `assignment_detail.py:21,211-241`. Owner-held sanitized evidence (2026-09-27) corroborates the page surfaces and aggregate render times. Do not publish evidence paths, records or selected attribute values.

Notice detail intentionally has no invented DOM selector: use selected detail URL plus completed, identity-checked info/comments responses (`notice_detail.py:327-362`). Its `page-readiness` span uses `page_kind="notice-detail"` around that existing semantic gate. It need not call the element helper.

### Replace the waits, not the evidence

1. **Combined traversal:** delete only `_settle` and migrate every call in `sync_all.py`. Keep `_return_to_roster(page)` as a callable traversal/fallback function: navigate to the roster with `wait_until="commit"`, preserve the return Referer, then await roster readiness before returning. `_roster` likewise returns only after roster readiness and retains its diagnostics. Before section navigation, wait only for that section's actual menu and verify current course, not global idleness. `_select_course` retains its armed request/commit observers through entry readiness and rechecks all existing sequence/count/identity conditions immediately before return. `_todo` delegates to the to-do collector's readiness, not a pre-navigation settle. Do not change traversal selection policy here.
2. **Fetch course entry:** `prepare_course_section` uses course-entry readiness with its requested course ID before returning; callers still arm section observers before clicking menus. Assignment fetch explicitly waits for roster readiness before selecting its course; notice fetch changes its roster goto to commit+roster readiness and validates entry/topbar before entering notices.
3. **Relevant responses:** replacing `networkidle` with a selector alone is forbidden. Captures remain armed before actions and until final validation. Add bounded completion notifications to existing captures for the exact expected request identities; wait for required response(s), require successful `response.finished()`, then rendered state, then revalidate duplicate/stale flags and request/commit boundaries. Do not snapshot `commit`/response arrays before awaited completion, as `_NoticeCapture.collect` currently does. Never use an initially-set pending event as evidence that the required request has started. Every response wait has a 15 s cap; delayed/failed/missing responses fail the existing operation.

   Route completion waits through `browser.finish_response`. The pinned Playwright implementation leaves its `Response.finished()` target-close task alive after completion or caller cancellation; racing its existing completion and target-close futures directly avoids teardown stderr noise without cancelling shared futures or suppressing genuine response/target-close errors. Keep this private-API compatibility boundary in the browser module and regression-test it against Playwright's actual response implementation.
4. **Assignments:** replace `activity.idle`/global pending gating with relevant task document/list completion. Keep the ordered request log, document count, before-document detection and duplicate-response checks. Background assets must not prevent success. After completed response and helper shell readiness, wait until extracted row count matches the verified response count, then parse and revalidate capture identity. Remove the unreadable-body `count=None` empty-success shortcut: without a verified count or separately evidenced completion signal, fail this course rather than erase its prior tasks. Do not treat the first rendered task as the full list.
5. **Notices/to-do:** initial to-do requires its one post-commit response to finish and its rendered grid. On pagination record prior fingerprint/page before clicking; readiness requires advancement and the final grid predicate, not old rows that remain visible. For response-backed pagination finish/validate its exact request; for UI-only pagination retain the no-request branch only after DOM advancement. Keep duplicate/stale validation active through snapshot extraction and retain the 120 s overall traversal cap/100-page limit. Board waits for both list responses, then matching rendered board state; verified zero is valid without a row. Do not add a quiet interval to classify UI-only pagination.
6. **Archive:** preserve the complete ordered `_RequestWindow.requests` log for C's document/refresh detection. Remove `_RequestWindow.idle` and its all-traffic pending/settled machinery; replace post-action use with `wait_response(path: str, method: str, *, after: int = 0) -> Any`, a bounded exact-request/response-completion wait with duplicate rejection. Remove pre-action idle calls at capture arm/navigation/modal setup: arm observers, snapshot the boundary and act. `_archive_navigation` already has a selected-response wait; keep it and use rendered archive completion before final count/duplicate validation. Initial collection requires the bound list response before checking list state. `_post_names` waits until modal or inline controls render, then completes any attachment-list request observed since its baseline; only a rendered inline/modal target with no such request permits the existing optional metadata path. Never accept no request merely because the observer has not fired yet. Keep listeners through extraction and reject late relevant duplicates before accepting the post. C owns changes inside `_archive_state` and the post-modal restore block; B supplies the completion changes outside that block and leaves the restoration policy unchanged.
7. **Lecture absence:** preserve today's `sync.py:66-94` behavior and `tests/test_sync.py:96-98` (`test_lecture_collector_empty`). For `page_kind="lecture"`, the shared helper verifies route and matching topbar only; `collect_lectures_rows` retains ownership of the existing bounded `.learningRow` wait and catches only its row-absence timeout. A verified `/std/course` with a matching topbar and no `.learningRow` within that wait is a successful empty course, not a failed/stale course. Recheck route and course identity before accepting empty success; identity, redirect, navigation and other failures remain fail-closed. Preserve successful empty parsing when rendered learning rows contain no lecture entries too. An observed explicit completion marker may later replace the row timeout, but must never turn a legitimate rowless course into a failure.
8. **Fetch detail:** assignment keeps unique selected link, entity ID, detail/stdDetail identities, route and visible title checks. Notice keeps selected board-link uniqueness, composite ID resolution, selected URL native ID and info course/item checks; replace `response_payload`'s immediate array read with a bounded await for its required response, followed by finished/status/method/duplicate checks. Board rendering must match verified responses before selecting a link; info/comments completion replaces the detail network settle. No download/build-source-package work starts until those gates pass. Never wait for unrelated resources to load.

### Profiling, errors and output

Agreed with A: `profile_span("page-readiness", page_kind=..., wait_kind="readiness", domain=..., course=ordinal)`; omit optional fields when unknown. `course` is an ephemeral positive ordinal, **never a course ID**. Use A's allowlisted kinds/metadata. Preserve A's `response-completion` and navigation spans; remove obsolete settle spans along with their waits, and avoid nesting duplicate spans for one readiness gate. No selectors, URLs or identities enter profiling output. No new CLI flags, output JSON keys, public error codes, request filtering, persistent selection epochs or concurrency.

Helper identity/route mismatch raises `ValueError`; timeout is `browser-timeout`. Existing wrappers remain authoritative: roster failure `course-discovery-failed` (`sync_all.py:131-140`); assignment selected-task mismatch `entity-unknown` and malformed detail `fetch-failed` (`assignment_detail.py:81-90`); notice identity failure `entity-unknown` (`notice_detail.py:33-36`); archive course failure `course-sync-failed` (`materials.py:383-398`). Keep per-domain/course failure handling, previous records marked stale, and atomic publication. Do not rewrite an identity mismatch as a readiness timeout.

## File ownership and work units

- B owns new `providers/cnu/readiness.py`, readiness replacements in `sync_all.py` (delete only `_settle`; retain and update `_return_to_roster`, roster and selection gates), `sync.py`, `course_context.py`, `assignments.py`, `notices.py`, both detail adapters, and the archive request-completion changes described above. Command adapters are intentionally unchanged.
- C owns archive retained-document evidence, `_archive_state` changes and post-modal restore decisions; integrate B's waits before C's patch. Do not rewrite C's restore block.
- D owns `_switch_course` and the traversal decision. It calls the exact helper signature above with `page_kind="course-entry"` only if its observed switch lands on `/std/lecture`; fallback roster return uses B's `_return_to_roster`.
- A lands profiling infrastructure first; B preserves its fields and replaces instrumented waits. E owns login/session reuse and fetch-command session lifecycle, not detail readiness.
- Implement as (1) helper plus combined navigation, (2) collector response/render gates, (3) fetch adapters and behavioral regressions. These are ordered units within B, not competing edits to shared files. Update the existing relevant domain/fetch documentation in the implementation change; release integration owns shared changelog/index edits.

## What must stay true

Principle 1 precedes speed: all currently evidenced nonempty/empty pages must work, including a verified rowless lecture course accepted after its bounded row wait. Failure to establish course identity must never publish another course or an empty list. The lecture-row absence exception does not apply to identity/navigation failures or other domains' completeness checks. One serial course context, unchanged catalog/envelope schemas, ordinary UI navigation, unfiltered page requests, existing attachment policy, and stale retention all remain. B changes no authentication, modal restoration, number of course selections, read/view/submission behavior or package publication semantics. Tests must distinguish a removed irrelevant wait from a removed correctness check.

## Offline acceptance and smoke proof

Extend existing synthetic fixtures/tests, not live data. Targeted command from the repository root:

```sh
uv run pytest tests/test_sync_all.py tests/test_sync.py tests/test_cnu_course_context.py tests/test_assignments_provider.py tests/test_cnu_notices.py tests/test_cnu_materials.py tests/test_assignment_detail.py tests/test_notice_detail.py tests/test_assignment_fetch_commands.py tests/test_notice_fetch_commands.py
```

Required behaviors:

- A synthetic Chromium page keeps unrelated background requests active after its content renders. Actual sync collectors and both detail adapters finish correctly without waiting for those requests; traffic is not blocked/rewritten. Exercise delayed key rows even after DOMContentLoaded, and delayed required responses even when a matching shell already exists.
- Old-document selectors, wrong/ambiguous topbar course, login redirect, duplicate list responses, a response started before navigation and relevant request failure cannot publish success. Verify retained old catalog records are stale and another course/domain continues.
- Multi-row progressive rendering is not truncated. Assignment/notice/archive count mismatch or missing completion times out without an empty replacement. Verified zero assignments/notices/archive and visible empty to-do succeed; hidden to-do empty marker does not. Keep `test_lecture_collector_empty` passing: verified `/std/course` plus matching topbar and no learning rows within the bounded row wait returns `[]` successfully. Wrong/ambiguous course or a route change must still fail, never enter that empty-course branch.
- To-do response-backed and UI-only pagination both advance; unchanged fingerprints, mixed old/new responses and duplicate requests fail. Archive nonempty, empty, paginated, modal and inline attachment paths still bind the correct list/post/files without requiring an attachment icon on every page.
- Fetch delayed board/detail responses succeed only for the selected entity; wrong task/course/native ID and a stale detail document fail before any file/package publication. Notice info/comments may complete without every image finishing.
- Scoped smoke uses a real local Chromium fixture and real changed adapters (not mocks echoing calls), records resulting IDs/counts and confirms an unrelated request is still outstanding at successful extraction. Do not depend on absolute speed assertions. Record the fixture invocation and observed output in implementation evidence; project-wide validation runs once after integration.

## Live verification

Join the next owner-authorized **single** full-path run (`docs/specs/live-run.md:12-17`): full four-domain sync, one selected notice fetch, one selected assignment fetch, one material download. Record commit-to-readiness timing, readiness/response-completion spans and timeout/error counts per page kind, section counts and normalized catalog/package equality, plus unchanged submission state and accepted notice read/view effects. Compare 188 s sync and 11–14 s fetch baselines without promising a threshold; explain all catalog differences. Verify empty courses as well as nonempty ones. Attribute document-count reductions to C/D, not B. A second live run needs owner approval; publish only aggregate sanitized evidence.

## Open questions / release gates

- No rowless lecture-page completion marker is established by the cited code/evidence. An owner-held DOM observation during the authorized run may identify a faster explicit completion signal later. Until then, preserve successful empty-course behavior after the bounded row wait on a verified `/std/course` with matching topbar; lack of an observed marker is not a release blocker or permission to fail a rowless course.
- Confirm the LMS can expose UI-only to-do pagination and optional attachment-list absence with the existing visible completion signals. If live evidence shows a deferred request after those signals, strengthen the operation-specific completion gate from that evidence; never reintroduce global network quiet as an identity proof.
- Measured samples do not prove readiness under every browser mode or slow connection. Keep the existing bounded limits and investigate timeouts on legitimate nonempty pages as correctness defects; do not tune limits downward from medians. The preserved lecture-row timeout for a verified empty course is not an error.
