# D — Course-to-course navigation without a roster return

## Goal and expected saving

Understood as: use an observed, person-usable course switcher instead of returning to the roster between courses; otherwise retain roster navigation and make its waits cheaper through slice B. **The switch action and its requests are UNOBSERVED. This is a conditional design, not authorization to invent a selector or replay an API.**

Owner-held sanitized evidence (2026-09-27): seven courses, seven course selections, roster-document dwell averaging 3.13 s. The requested upper-bound estimate is up to seven returns × about 3.1 s ≈ 22 s, before switch overhead. The actual loop has six inter-course returns for seven courses; its additional post-to-do return starts outside a course page (`src/campusctl/providers/cnu/sync_all.py:391-421`). Thus the course-page-only fast path targets about 19 s gross; retain the to-do return. No measured saving is claimed, and roster dwell is not purely avoidable wait time.

## Current behavior and evidence

- One authenticated session discovers the roster, optionally reads global to-do, then selects each enrolled course serially (`src/campusctl/providers/cnu/sync_all.py:349-429`). There is no final roster return (`:430-452`).
- `_return_to_roster` settles, navigates to `MY_LECTURE_URL` with the previous page as Referer, and settles again (`src/campusctl/providers/cnu/sync_all.py:199-207`). `_settle` currently waits for network idle (`:94-97`).
- `_select_course` clicks `moveLecture` and requires exactly one selection POST before exactly one main-frame `/std/lecture` document request and commit, then the expected topbar identity; listeners are detached in `finally` (`src/campusctl/providers/cnu/sync_all.py:145-196`). Request-body identity is deliberately not the proof.
- Identity extraction already refers to `#topbarCurrentLecture` and `#topbarLectureDropdown a[data-act="changeLecture"][data-courseid]` (`src/campusctl/providers/cnu/course_context.py:18-37`). This suggests where to look, **not** that the control is visible, actionable, or equivalent to roster selection. The test topbar supplies identity markup without a switch click handler (`tests/test_sync_all.py:30-47`); its roster handler alone implements selection (`:50-64`). Do not treat that fixture as live evidence.
- Failed selection fails all requested domains for that course and uses roster recovery before the next course; invalid section navigation abandons remaining domains (`src/campusctl/providers/cnu/sync_all.py:420-447`). Publication happens after browser close under the lock (`:451-515`). Existing design requires verified entry identity and unchanged failure/stale semantics (`docs/specs/70-sync-performance/design.md:11-22`).

## Observation gate: next authorized full-path run

Follow `docs/specs/live-run.md:3-35`; one owner-authorized full-path run records this observation alongside normal sync, fetches and download. A second run for the same change requires owner approval. Do not enable an unobserved fast path just to make the observation run faster.

At the end of a course's final requested section, inspect the actual topbar and perform an ordinary UI course change to the next course already in the discovered roster, if possible. Record:

1. Which course page exposes the control; opener and option DOM attributes, visibility/enabled state, whether opening the dropdown is required, and whether the target has one unambiguous course-ID-bearing option. Include archive after its modal has closed, and other terminal sections exercised by the run. Unexercised section variants remain unverified.
2. The ordinary click sequence. No forced hidden-element click, injected handler invocation, constructed navigation URL, or direct selection HTTP request.
3. All requests caused by the action: sequence/time, method, origin/path, query keys, resource type, frame, response status/type, redirects and committed destination. Determine whether it makes one `/api/v1/course/addSessionCourseInfo` POST followed by one main-frame `/std/lecture` document, or something different. Record response JSON key paths/types and identity equality flags only, not values (`docs/specs/live-run.md:21-29`).
4. When the destination menu and unique topbar identity appear; equality to the intended next roster course; whether each subsequently collected section proves that same course. A changed label or selection POST alone is insufficient.
5. If absent/unusable, record that result and source page type; do not infer global absence from one missing page. Continue via roster. Record hidden/disabled, missing-target and ambiguous-target cases separately from an attempted navigation failure.

Raw observation and selectors containing real values stay owner-held. Public fixtures use synthetic IDs and `.invalid` hosts; cite results only as owner-held sanitized evidence (2026-09-27) with aggregate counts/timings. Update owner-held LMS behavior notes. If discovery first occurs in this run, it informs implementation; it cannot count as a live verification of code not yet implemented.

## Conditional design and work units

### D1 — Evidence decision (must precede D2)

- **Observed normal switch with the existing entry protocol:** implement D2 only for source page kinds verified to expose that control. Freeze the observed opener/option selectors in the implementation and sanitized fixture; the selector currently used for identity is only a candidate until then.
- **No usable control:** keep `_return_to_roster` followed by `_select_course`; implement no speculative switch helper. Slice B owns replacing their settle waits with bounded readiness. Preserve normal navigation and Referer behavior, not a cached roster or synthesized selection request.
- **Different landing page, request order/count or in-place switch:** retain roster navigation for now. Amend this contract from the recording before implementation; do not loosen the existing course-entry proof to accommodate a guess. This branch is an explicit unverified prerequisite, not a silently shipped partial fast path.

### D2 — Proven switch implementation

Own `src/campusctl/providers/cnu/sync_all.py` new `_switch_course` and the course-loop transition block, plus switch-specific behavior tests in `tests/test_sync_all.py`. Integrate after B's overlapping traversal readiness edits; do not independently rewrite `_settle` or `_return_to_roster`. B owns `src/campusctl/providers/cnu/readiness.py`; A owns profiling primitives. Release documentation is integrated by the coordinating change, not a competing worktree edit.

New internal contract:

```python
async def _switch_course(
    page: Any, course: dict[str, Any], ordinal: int, domain: str
) -> bool:
    # False: unsupported source/control absent or unusable before selection.
    # True: one observed UI selection completed and entry identity proved.
    # Raises: selection attempted but navigation/readiness/identity failed.
```

- Before invoking, re-prove the source course using `_wait_for_topbar_course_id`; it must equal the last successfully selected course. Restrict source paths to those validated by D1. A missing/wrong source identity makes the optimization ineligible and routes through roster recovery; never read rows in that state.
- Discover the observed control after B's source-page readiness, open it normally if needed, and require exactly one visible, enabled option for the next roster ID, quoted with `_css_string`. Unsupported/missing/ambiguous targets or an opener readiness timeout return `False` **before** a selection is issued. If opening itself can select/navigate, D1 must identify that action as the selection boundary instead. Once the selection click is attempted, errors raise because navigation may have begun. Do not add sleeps or probe unknown endpoints.
- Arm request/commit listeners before the selection click. Preserve the existing exact POST → main-frame entry request → commit proof, including frame filtering and listener cleanup in `finally` (`src/campusctl/providers/cnu/sync_all.py:148-196`). Keep `course-selection` and `course_selections` semantics for the actual selection action, not dropdown opening or eligibility checks; no new profile schema is needed.
- After navigation proof call B's agreed interface:

```python
await wait_page_ready(
    page,
    "course-entry",
    expected_course_id=course["course_id"],
    domain=domain,
    ordinal=ordinal,
)
```

  B requires `/std/lecture`, attached `a[href="/std/course"]`, and unique matching topbar identity. Absence times out with existing `browser-timeout`; mismatch raises `ValueError`. Keep `_select_course`'s roster behavior intact, reusing its proof implementation internally only if that avoids duplicate listeners without weakening either entry path.
- Replace `selected_before` traversal decisions with explicit last-selected-course identity and a `needs_roster_recovery` flag. First selection and post-to-do selection remain roster-based. After a fully completed, identity-proven course, try `_switch_course`; `False` means return to roster and use `_select_course` once. After any selection or section exception, force the next course through roster recovery. This conservative failure path does not change which domains are allowed to continue within the current course.
- If the switch issued a selection and then fails, do **not** retry that course through roster or double-select it. Use existing `staged[domain].fail(domain, course, "course selection", error)` for every requested domain; preserve its prior records and continue the next course through roster recovery. A failed roster recovery retains the current session-level `course-sync-failed` behavior (`src/campusctl/providers/cnu/sync_all.py:410-428`).

No CLI option, persistent capability cache, new envelope, error code, enrollment source, or alternate public API. Internal eligibility is not an error; selection failures surface through existing `course-sync-failed` diagnostics (`src/campusctl/providers/cnu/sync_all.py:70-91`).

## What must stay true

- Every existing command remains usable via roster when a person can use it; optimization never blocks on the presence of a switcher (`docs/specs/constitution.md:7-13`). Pages send their own requests unfiltered (`:9,27`).
- Roster remains the authoritative target set. A switcher option cannot add a course, replace roster discovery, or change enrollment semantics. Filtered/one-course runs gain no inter-course switch.
- No rows are accepted under a target ID until committed entry and collector-specific identity checks succeed. Preserve serial domain order, to-do snapshot semantics, old rows for failed courses, successful-empty replacement, atomic per-domain publication and session lock (`docs/specs/70-sync-performance/design.md:13-22`).
- Do not change archive modal restoration: C finishes or fails that collector before D decides the next transition. A failed collector must not leave D trusting a modal or ambiguous page.

## Offline acceptance tests

Extend the existing local browser/server fixture, not mock echoes. Add a functional switcher only after its behavior is observed; the existing identity-only topbar must still exercise fallback.

Run from repository root: `uv run pytest tests/test_sync_all.py -q`. The implementation owner runs this scoped command after integration with B; no project-wide checks in competing worktrees.

Required behavioral cases:

1. Observed successful switch across seven synthetic courses: exact normalized catalogs unchanged, one selection per course, zero inter-course roster documents on eligible transitions. Initial discovery and post-to-do return remain. Assert actual requests and resulting identities, not helper invocation counts.
2. Missing control, missing target and duplicate target: roster fallback publishes the correct next course without a second selection. Include terminal-section variants supported by the observed design.
3. Delayed destination identity within the bounded readiness deadline succeeds. Wrong identity, readiness timeout and invalid navigation order/duplicate selection never publish wrong-course rows: fail the target course, keep its previous records, and let a later course succeed after roster recovery. Build on `tests/test_sync_all.py:551-563,611-649`.
4. Prior selection failure and section-navigation failure force roster recovery rather than switching from untrusted state. Keep existing redirect and stale-record cases (`tests/test_sync_all.py:652-689`).
5. Filtered one-course sync performs no switch; unknown course retains `course-not-found`; to-do failure still restores roster before course entry (`tests/test_sync_all.py:693-718,1118-1137`).
6. With no control observed/implemented, exercise B's readiness-based roster return on the existing seven-course browser fixture; no speculative switch fixture is required.

## Live verification of the implemented branch

The authorized full-path run for the implemented change uses the real CLI with profiling, preserving the governing run scope. Record eligible transitions, switches attempted/proved, roster fallback count/reasons, main-frame roster and entry documents, selection count, wait spans, wall time and normalized before/after catalog record sets. Use ordinal/category aggregates publicly, never identifiers. Confirm course identity and normal notice/assignment/material behavior, no request filtering, and no new failures. Compare like-for-like browser modes; report net wall-time change, not just the 22 s gross ceiling. A successful offline fixture is not evidence that the LMS switcher works.

## Open questions

- Is the candidate topbar switcher visible/actionable on course pages, especially archive after attachment-modal cleanup? Which opener is actually used?
- Does switching issue the same selection POST and `/std/lecture` entry protocol? Which fields/response identities, redirects and readiness signals does it expose?
- Is availability uniform across terminal section types and courses? Until observed, use roster fallback on unverified variants.
- Can the to-do page itself switch courses? This slice does not assume it; the seventh-return portion of the upper bound remains unproven and outside the course-page fast path.
