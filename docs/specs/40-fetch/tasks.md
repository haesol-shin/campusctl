# PR5 — selected LMS source packages: tasks

[Specification](spec.md) · [Design and frozen interfaces](design.md) · [Project constitution](../constitution.md). Owner-approved detail observations and operation pins unblock fixture-only implementation now [owner-held sanitized policy decision (2026-09-25, lines 21–35,61–72); owner-held sanitized observation report (2026-09-25, lines 61–75,103–133)]. The remaining `/std/todo` `read_yn` and actual notice attachment checks gate **release**, not code work. `[P]` means disjoint source/test files with predecessors landed; shared-file edits are sequential. Implementation workers skip formatters, linters, live LMS and project-wide suites; the integration owner runs integration verification after lanes land.

v0.4.0 scheduling delta: T012/T013 and T018/T019 wait for 50 U5 to hand over `commands/assignments.py` and `commands/notices.py`; use the global effective browser preference from 60, never register per-command headless flags. T007/T008 package/detail adapters remain disjoint and parallel. Add scoped acceptance to T014/T020: numeric material selections are not fetch IDs, full selected fetch IDs retain course binding, and unsupported effective headless fails before navigation. 40 remains the sole owner of fetch release evidence.

## Phase 0 — Reviewed pins and synthetic fixtures (not an implementation gate)

- [ ] T001 [P] [US1] Derive a **synthetic, labeled** assignment detail fixture from observed `.card-body h4`, `.card-body` prompt, points and submission controls in `tests/fixtures/lms_sources/assignment_detail_synthetic.html`; verifying check: compare documented fields/unchanged submission badge with owner-held sanitized observation report (2026-09-25, lines 118–133), assert no real titles/IDs and no `#uploadFile` interaction.
- [ ] T002 [P] [US1] Derive a **synthetic, labeled** per-course notice detail/list fixture with board-item ID binding and view-count +1 in `tests/fixtures/lms_sources/notice_detail_synthetic.html`; verifying check: compare paths/ID selectors and count effect with owner-held sanitized observation report (2026-09-25, lines 69–75,103–116,183–187), without claiming a live attachment or `read_yn` outcome.
- [ ] T003 [US1] Translate reviewed `assignments.fetch`/`notices.fetch` method/path/origin pins, PR1.1 suppression/static/selected-file contract and approved MIME/signature/size table into fixture expectations in `tests/fixtures/lms_sources/fetch_policy_synthetic.json`; verifying check: compare every data pin against owner-held sanitized policy decision (2026-09-25, lines 8–35,41–72), with all identities placeholders. No invented real capture, selected notice file route or live state record.

Checkpoint: Implementation proceeds using these fixtures and reviewed report; unknown live `read_yn`, notice attachment and headless behavior remain explicit release gates, not blockers to coding.

## Phase 1 — Foundational package contract (blocking)

- [ ] T004 [US2] Implement frozen immutable `ResourceReference` and `DetailSnapshot` types plus canonical source-ref/resource-ID encoding in `src/campusctl/source_package.py`; verifying test: `tests/test_source_package.py::test_resource_ids_and_query_free_source_refs` (omitted references count, no secrets persisted).
- [ ] T005 [US2] Implement safe component assignment, page-order collisions, duplicate IDs/bytes, relative path containment, canonical manifest/digest and atomic private publication in `src/campusctl/source_package.py`; verifying test: `tests/test_source_package.py::test_atomic_manifest_digest_collisions_and_reuse` (includes long extension, case-only name, symlink and failed transfer). Depends on T004; same file, **sequential**.
- [ ] T006 [US2] Implement the builder's included-image/official-attachment transfer and policy omission mapping using PR4 `attachment_transfer.py` and `material_files.safe_component` in `src/campusctl/source_package.py`; verifying test: `tests/test_source_package.py::test_guarded_resources_and_policy_partial` (zero forbidden byte reads; non-200/redirect/signature failure leaves no package). Depends on T005; same file, **sequential**.

Checkpoint: Package logic consumes only the frozen snapshot contract and PR4 helpers; no provider navigation, no material cache write.

## Phase 2 — User story P1: fixture-backed selected detail adapters [US1]

- [ ] T007 [P] [US1] Implement selected assignment task row → `GET /std/taskView` + POST detail/stdDetail extraction in `src/campusctl/providers/cnu/assignment_detail.py`; verifying test: `tests/test_assignment_detail.py::test_selected_assignment_detail_capture_readonly` using synthetic labeled fixture (full task ID, `.card-body h4`, prompt/points, ordered references, unchanged submission badge and **zero** `#uploadFile`/modal interaction).
- [ ] T008 [P] [US1] Implement selected notice per-course row → `GET /std/noticeDetail` + POST notice/info/cmt extraction in `src/campusctl/providers/cnu/notice_detail.py`; verifying tests: `tests/test_notice_detail.py::test_selected_notice_detail_capture_readonly` and `::test_wrong_selected_notice_fails_before_transfer` (synthetic board-item binding; view count +1 accepted; no fabricated live `read_yn`/attachment outcome).
- [ ] T009 [US1] Reject an opened assignment detail whose observed task identity differs from selected row in `src/campusctl/providers/cnu/assignment_detail.py`; verifying test: `tests/test_assignment_detail.py::test_wrong_selected_task_fails_before_transfer`. Depends on T007; same file/lane, **sequential**.

Checkpoint: Both adapters bind exact selected detail, emit ordered references and never touch submission controls; no fake live-provider proof.

## Phase 3 — User story P2: package only selected official non-video content [US2]

- [ ] T010 [US2] Integrate detail snapshots with guarded builder, preclassified same-origin images and PR4 `OfficialAttachmentTarget(file_id,parent_kind,parent_id,control_locator,candidate_url)`/`fetch_official_attachment` in `src/campusctl/source_package.py`; verifying test: `tests/test_source_package.py::test_selected_attachment_and_image_original_bytes` proves both `(assignment,task_id)` and `(notice,board_item_id)` parent binding before bytes, plus hash/signature/link rewrites and omissions. Depends on T006–T009; same source-package file, **sequential**.
- [ ] T011 [US2] Cover external/video/unknown/redirect/method/logging/header/size cases and original-byte vs omission transitions in `tests/test_source_package.py`; verifying tests: `tests/test_source_package.py::test_policy_exclusions_never_request_bytes`, `::test_allowed_get_vs_range_header` (same URL/method, only case-insensitive `Range` header differs; second request blocked before body), and `::test_failed_transfer_never_publishes`. Depends on T010; **sequential**.

Checkpoint: Policy omissions produce marked partial package; technical mismatch creates no output; no video/stream capture or LMS logging.

## Phase 4 — User story P3: human/JSON command surface [US3]

- [ ] T012 [P] [US3] Extend assignment module's fixture-exercisable `dispatch/render` and domain-local reviewed fetch policy pins in `src/campusctl/commands/assignments.py`; verifying test: `tests/test_assignment_fetch_commands.py::test_assignment_fetch_json_and_human` (selected ID, paths, partial/error/lock outcomes). Defer parser `register` and advertised `CAPABILITY.commands` fetch until T017 clears; preserve approved list/sync. Depends on T010–T011.
- [ ] T013 [P] [US3] Extend notice module's fixture-exercisable `dispatch/render` and domain-local reviewed fetch policy pins in `src/campusctl/commands/notices.py`; verifying test: `tests/test_notice_fetch_commands.py::test_notice_fetch_json_and_human` (full composite ID, paths, omissions). Defer parser `register` and advertised fetch capability until T017 clears; preserve approved list/sync. Depends on T010–T011; disjoint from T012.
- [ ] T014 [US3] Verify selected-ID/error/partial/busy and human/JSON integration with direct domain handlers in `tests/test_fetch_commands.py`; verifying test: `tests/test_fetch_commands.py::test_fetch_unpublished_surface`, confirming public CLI still does **not** advertise fetch while release gates remain. Depends on T012–T013.

Checkpoint: Fixture-only handlers use existing domain hooks and envelope/human outputs; public parser/capability still expose only released list/sync. `cli.py`/`presentation.py` need no PR5 edits.

## Phase 5 — Release evidence and publication (outside implementation branches)

- [ ] T015 [US1] Observe selected notice through the authorized global `/std/todo` route set, capture `read_yn` immediately before and after exact detail open and accepted `조회수` +1 in `tests/fixtures/lms_sources/detail_live_observation_sanitized.json`; verifying check: owner independently reviews either `read_yn` outcome (flip allowed but recorded), no other coursework change. This is **not** T002's synthetic fixture.
- [ ] T016 [US2] Observe a notice **with a real official attachment**, selected board-item parent and attachment ID, invoke its official detail control with PR4 target `(file_id,"notice",selected_board_item_id,control_locator,candidate_url)` and PR1.1 bound selected-file request, append sanitized origin/path/method, MIME/signature, size and no extra effects in `tests/fixtures/lms_sources/detail_live_observation_sanitized.json`; verifying check: owner confirms one bounded successful PR4 transfer and exact selected parent/file match. Depends on T015 and separately authorized live observation; no notice attachment was present in initial report.
- [ ] T017 [US1] Review T015/T016, selected-detail completeness/identity and fixture acceptance, ensuring notice `read_yn` outcome and +1 view count are documented and submission/attendance/grading/enrollment unchanged in `tests/fixtures/lms_sources/detail_live_observation_sanitized.json`; verifying check: independent owner signoff, preserve any original `detail_read_state_sanitized.json`. Depends on T014–T016; do not fabricate missing observations.
- [ ] T018 [P] [US3] After T017 approval, register assignment `fetch`, append approved routes/command to domain `CAPABILITY` in `src/campusctl/commands/assignments.py`, and publish only assignment fetch contract in `docs/contracts/assignments.md`; verifying tests: `tests/test_assignment_fetch_commands.py::test_assignment_fetch_json_and_human` and exact pins/status review.
- [ ] T019 [P] [US3] After T017 approval, register notice `fetch`, append approved routes/command to domain `CAPABILITY` in `src/campusctl/commands/notices.py`, and publish only notice fetch contract in `docs/contracts/notices.md`; verifying tests: `tests/test_notice_fetch_commands.py::test_notice_fetch_json_and_human` and exact official-control/read-state disclosure review. Disjoint from T018.
- [ ] T020 [US3] Verify both public CLI commands, status/exit/lock and untruncated IDs/paths in `tests/test_fetch_commands.py`; verifying test: `tests/test_fetch_commands.py::test_fetch_released_surface`. Depends on T018–T019; the integration owner coordinates any shared contract index/version publication.

Checkpoint: Implementation may land before release observations; **no fetch command/capability/contract is published** until T015–T017 pass. No live LMS run in implementation branches; legacy notifier remains untouched.

## Dependencies & parallel lanes

```text
PR1.1 guarded UI policy + PR2 assignment list + PR3 notice list + PR4 selected transfer
                                  |
           T001 assignment fixture || T002 notice fixture → T003 policy fixture
                                  |
                        T004 snapshot types
                  /               |                 \
    T005 → T006 package  T007 → T009 assignment  T008 notice
                  \               |                 /
                          T010 → T011 package
                                  |
                 T012 assignment || T013 notice commands
                                  |
                      T014 unpublished integration
                                  |
              T015 read_yn → T016 notice attachment (authorized)
                                  |
                       T017 owner release review
                                  |
                 T018 assignment || T019 notice publish
                                  |
                       T020 public integration
```

- **Synthetic evidence lanes:** T001 owns only `assignment_detail_synthetic.html`, T002 only `notice_detail_synthetic.html`; T003 owns `fetch_policy_synthetic.json`. All literals derive from reviewed pins/report, never real IDs/titles/tokens; synthetic files are not release evidence.
- **Package lane (one implementation branch):** T004–T006 then T010–T011 own `src/campusctl/source_package.py`, `tests/test_source_package.py`; after T004 freezes types, T005/T006 proceed concurrently with detail lanes in separate implementation branches. T010 waits for T006/T008/T009.
- **Assignment detail lane:** T007 then T009 own `src/campusctl/providers/cnu/assignment_detail.py`, `tests/test_assignment_detail.py`; **notice detail lane:** T008 owns `src/campusctl/providers/cnu/notice_detail.py`, `tests/test_notice_detail.py`. Neither edits PR1.1/PR4 shared helpers.
- **Domain command lanes:** T012 then T018 own only `src/campusctl/commands/assignments.py`, `tests/test_assignment_fetch_commands.py`, later `docs/contracts/assignments.md`; T013 then T019 analogously own notices files and `docs/contracts/notices.md`. Until owner approval, released list/sync remain discoverable but fetch parser/capability remain absent.
- **Integration owner:** T014 then T020 own `tests/test_fetch_commands.py`, never mutate shared CLI/presentation. T015–T017 are owner-authorized **release evidence**, outside implementation branches; the integration owner serializes the shared release observation JSON and any contract index/changelog/version change.

No implementation lane runs formatters, linters, project-wide tests or live LMS checks; the integration owner performs final scoped verification after branches land. Authentication is trusted `ensure_logged_in` **once before** installing PR1.1's interceptor; all guarded roster/course navigation including L GET `/std/myLecture` uses exact pins. A mid-operation login redirect fails closed (`login-action-required` or existing login error), without guarded retry. Reviewed policy values come from the owner decision, not a synthetic fixture.
