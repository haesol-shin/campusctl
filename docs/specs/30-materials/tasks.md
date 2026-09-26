# PR4 materials implementation tasks (FR-001–FR-020)

Use [spec.md](spec.md), [design.md](design.md) and [constitution](../constitution.md). PR1 and PR1.1 are prerequisites. PR4 writes only its material-owned modules, tests/fixtures and `docs/contracts/materials.md`, never foundation `ui_policy.py`, `course_context.py`, CLI or presentation. No live LMS/production legacy notifier edits; the integration owner runs project-wide checks after lanes land.

**Release dependency:** Owner-approved operation pins, two storage origins, selected-file templates, three observed MIME/signatures and **200000000**-byte limit are fixed by owner-held sanitized policy decision (2026-09-25, lines 23-60); PR1.1 implements their shared matcher/interceptor. An unknown MIME for an owner-allowed extension is not permission to guess it. Archive empty-state/board-title mapping and headless remain open; do not fake evidence or turn on a capability whose path cannot pass fixture-backed gates. Authentication uses `ensure_logged_in` once before the guard, then guarded course navigation/roster re-entry; a mid-operation login redirect fails closed without retry.

## Phase 1 — Setup

- [ ] T001 [P] [US1] Prepare sanitized modal, inline, unresolved modal/inline course failure, completed empty-state, duplicate-ID, `example.pdf 바로보기`, Panopto and YouTube archive controls in `tests/fixtures/lms_sources/materials_archive.json`; verify `tests/test_cnu_materials.py::test_archive_fixture_cases`. Do not use unreviewed post-detail `/std/archiveView`.
- [ ] T002 [P] [US2] Prepare synthetic two-origin selected-URL binding/template bypasses (foreign ID, extra segment/query, encoded slash, traversal, double encoding, redirect), strict observed PDF/PPTX/ZIP MIME/signature responses, valid/missing-entry OOXML/hwpx ZIP central directories, full-eight-byte/broken-four-byte OLE, signed unobserved types with unmapped server MIME, invalid UTF-8 text, and paired GET requests without `Range` and with `rAnGe: bytes=0-` in `tests/fixtures/lms_sources/materials_responses.json`; verify `tests/test_cnu_attachment_transfer.py::test_response_fixture_cases`.

Checkpoint: fixture inputs are local, redacted and independent of an LMS session; a synthetic test pin is never owner approval. Representative sanitized provider fixtures and read-only-effect evidence remain separate release prerequisites.

## Phase 2 — Foundational (blocking)

- [ ] T003 [P] [US2] Implement PR4 `RequestPolicy` response guards for strict observed PDF/PPTX/ZIP MIME pairs, extension-specific signatures (eight-byte OLE, ZIP central-directory OOXML/hwpx entry without extraction, image magic, UTF-8 text), `video/*`/`audio/*` rejection and `max_bytes=200000000`; unobserved unmapped MIME is informational, never an admission gate. Keep request/URL guards in PR1.1 `guard_ui_request`/`bind_selected_file_request`, not a second matcher, in `src/campusctl/providers/cnu/request_policy.py`; verify `tests/test_cnu_request_policy.py::test_selected_response_mime_signature_and_limit`.
- [ ] T004 [P] [US3] Implement `safe_component(name, *, suffix="")` with NFC, both basename separators, device/invalid-name handling and final 200-byte UTF-8 collision-key rules in `src/campusctl/material_files.py`; verify `tests/test_material_files.py::test_safe_names_and_collision_boundaries`.

Checkpoint: unapproved requests fail before body access, and safe-component behavior is deterministic without filesystem writes. T003 and T004 own disjoint files and may run in parallel.

## Phase 3 — US1 metadata sync and list

- [ ] T005 [US1] Implement bounded modal → inline archive enumeration (no unreviewed post-detail `/std/archiveView` fallback), board ID/title extraction, official control or `getAttachFileList` display-name precedence with trailing `바로보기` removal, multiple targets, metadata-only video/external rows, whole-course duplicate/missing ID failure and archive restoration in `src/campusctl/providers/cnu/materials.py`; if modal and inline controls do not resolve a post, fail its course; verify `tests/test_cnu_materials.py::test_modal_inline_detail_and_course_failure`.
- [ ] T006 [US1] Implement `sync_materials(config, root, course_id=None, *, headless=False)` with pre-guard `ensure_logged_in`, PR1.1 interceptor, guarded `/std/myLecture` and `enter_course_section`, exact `materials.sync` routes/background; pin `getAttachFileList` `query:{"e":"encrypted"}` (provider requires the selected post's `e`) and both properties GET routes `query:{"_":"cachebuster"}`; named Panopto suppressions, static resource gate, catalog merges and zero file-body requests in `src/campusctl/providers/cnu/materials.py`; verify `tests/test_cnu_materials.py::test_full_filtered_and_failed_course_merges`.
- [ ] T007 [US1] Implement domain `CAPABILITY`, `register`, `sync`, cached filtered `list` dispatch and human sync/list renderer with full IDs, material counts and stale warnings in `src/campusctl/commands/materials.py`; verify `tests/test_materials_commands.py::test_sync_list_human_json_and_staleness`.

Checkpoint: a complete course replaces only its own attachment rows, failed courses remain stale, lists never open a browser, and sync makes zero attachment-body requests. Keep `CAPABILITY.policy.approved:false` until UI/effect evidence clears; the foundation discovery skips it.

## Phase 4 — US2 selected download

- [ ] T008 [P] [US2] Implement parent-neutral `OfficialAttachmentTarget(file_id,parent_kind,parent_id,control_locator,candidate_url)`, `FetchedAttachment`, `fetch_official_attachment(page,target,policy,temp_dir,*,max_bytes)` in `src/campusctl/providers/cnu/attachment_transfer.py`: recheck selected official parent/file, bind only its official control/fileDownload URL with PR1.1, guard method/headers/response before bytes, stream at most **200000000**, validate strict observed pairs or signed unobserved type including ZIP central directory without extracting, and return exclusive temp plus observed MIME or `None`; verify `tests/test_cnu_attachment_transfer.py::test_selected_guarded_bounded_transfer`.
- [ ] T009 [P] [US2] Implement byte-verified regular-file reuse, exactly one full-SHA collision suffix, symlink/containment refusal and atomic no-clobber publication in `src/campusctl/material_files.py`; verify `tests/test_material_files.py::test_publish_collision_and_symlink_safety`.
- [ ] T010 [US2] Add one-full-ID `download` dispatch, archive `parent_kind="archive"` and selected board-item `parent_id`/file-ID revalidation, pre-guard authentication, two-origin selected URL binding, Panopto/YouTube refusal, headless gate, output directory and absolute-path human/JSON `outcome` (`saved`, `reused`, `skipped-existing`) in `src/campusctl/commands/materials.py`; verify `tests/test_materials_commands.py::test_download_selection_policy_and_outcome`.

Checkpoint: for every unapproved origin/type/redirect/oversize/status/signature, selected download issues no forbidden body request and publishes no partial output; only explicitly selected approved non-video attachments can succeed. T008 and T009 are parallel after Phase 2; T010 depends on both.

## Phase 5 — US3 idempotent retry

- [ ] T011 [US3] Add private atomic per-ID/per-absolute-output-directory receipts with `observed_mime` (`null` if absent), regular non-symlink byte/hash verification and no MIME-based skip admission in `src/campusctl/material_files.py`; verify `tests/test_material_files.py::test_verified_receipt_and_interrupted_temp`.
- [ ] T012 [US3] Check a receipt inside the single session lock before navigation, set `result.material.outcome` to `skipped-existing` with zero LMS requests, or `reused` after fresh byte-match; otherwise restart without Range in `src/campusctl/commands/materials.py`; verify `tests/test_materials_commands.py::test_skip_existing_and_restart_after_interruption`.

Checkpoint: same verified destination makes zero LMS requests, changed/symlinked files never skip, and no interrupted temp remains. T012 depends on T011 and Phase 4.

## Phase 6 — Polish and reviewed release

- [ ] T013 [US1] Ensure all five `CAPABILITY`, `register`, `dispatch`, `render`, `sync` exports are complete and approved metadata is discoverable through foundation `campusctl.commands.discover_domain_modules()`; keep an unapproved module intentionally undiscovered in `src/campusctl/commands/materials.py`; verify `tests/test_materials_commands.py::test_discovery_only_exposes_reviewed_materials`.
- [ ] T014 [US1] Prove discovery-based human rendering, full IDs, stale warnings and Saved/Reused/Skipped-existing absolute paths without touching shared renderers in `tests/test_materials_commands.py`; verify `tests/test_materials_commands.py::test_full_ids_stale_warning_and_outcome_paths`.
- [ ] T015 [US2] Publish only evidence-cleared materials commands, capability policy, JSON `result.material.outcome`/errors and human output (foundation owns the canonical index link) in `docs/contracts/materials.md`; verify `tests/test_materials_commands.py::test_download_selection_policy_and_outcome`.
- [ ] T016 [US2] Exercise fixture-only CLI sync → list → selected download → verified retry, partial course, busy lock, named Panopto abort/unexpected-request failure, both bound storage origins, bounded `e`/`_` queries, Panopto/YouTube refusal, ranged/non-ranged GET, strict observed PDF/PPTX/ZIP pairs, eight-byte OLE rejection of four-byte-only match, missing required OOXML/hwpx ZIP entry, signed unobserved type with unmapped MIME and receipt `observed_mime`, invalid UTF-8, exact **200000000**-byte cap and JSON/human outcomes in `tests/test_materials_commands.py`; verify `tests/test_materials_commands.py::test_end_to_end_fixture_cli_scenario`.

Checkpoint: one small reviewed PR publishes only complete, evidence-cleared material capabilities and matching canonical documentation; no live LMS use or video/logging path. The integration owner runs the final project-wide validation once after all implementation branches land.

## Dependencies & parallel lanes

- **Prerequisite PR1/PR1.1 (not PR4 lanes):** `domain_catalog`, `identity.material_entity_id`, navigation-only `course_context.enter_course_section`, pre-guard `ensure_logged_in`, `ui_policy.match_selected_file_path`, `bind_selected_file_request`, `guard_ui_request(...,selected_file=None,selected_file_id=None)`, `install_ui_request_interceptor(...,diagnostics,selected_file=None,selected_file_id=None)` with `.raise_if_denied()` and `.close()`, sorted `commands.discover_domain_modules()` and CLI/presentation routing. PR4 never edits those files; PR1.1 binds selected ID plus exact URL and treats named Panopto aborts as nonfatal but any other unexpected request as fatal. Headless remains separately gated.
- **Lane A — archive metadata:** T001 → T005 → T006, sole owner of `providers/cnu/materials.py` and its fixture/test. Parallel with B/C after prerequisite; no shared CLI edits.
- **Lane B — selected request/response:** T002 → T003 → T008, sole owner of `providers/cnu/request_policy.py`, `providers/cnu/attachment_transfer.py` and their own fixture/tests. Parallel with A/C. PR5 consumes the frozen temp-transfer API.
- **Lane C — safe files and receipts:** T004 → T009 → T011, sole owner of `material_files.py` and `tests/test_material_files.py`. Parallel with A/B; no catalog writes.
- **Lane D — domain command and release:** T007 → T010 → T012 → T013 → T014 → T015 → T016, sole owner of `src/campusctl/commands/materials.py`, `tests/test_materials_commands.py` and `docs/contracts/materials.md`. These coupled tasks are sequential in one implementation branch and depend on A/B/C contracts and reviewed release evidence. No CLI, presentation, or canonical contract-index edits.
