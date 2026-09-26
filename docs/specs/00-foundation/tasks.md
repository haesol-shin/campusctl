# PR1 (merged) / PR1.1 — Foundation implementation tasks

Execute under [requirements](spec.md), [design](design.md), and [constitution](../constitution.md). PR1 baseline T001–T019 below is merged history, **not** an invitation to rerun work; PR1.1 T020–T027 is one small sequential follow-up lane before PR2/3/4. No public capability, live LMS request, commit by spec writer, or domain-specific provider implementation.

## 2026-09-26 side-request cutover

- [x] Specify passive third-party GET suppression versus fatal POST/data/Range/redirect and reviewed named exceptions before changing the guard
- [x] Require operation-scoped exact suppress pins, external telemetry origin outside policy origins, image-only roster upload and the exact reviewed activity-status read route
- [x] Require interceptor reason counts and fake-request tests for all fatal/suppressed boundaries
- [x] Require an exact image-path favicon suppression for resource type other, without admitting other LMS requests

## Setup

- [ ] T001 [US1] Freeze sanitized identity and package schema vectors in `tests/fixtures/lms_sources/identity.json` and `tests/fixtures/lms_sources/package_schema.json`; verify with `tests/test_identity.py::test_fixture_identity_vectors`
- [ ] T002 [US4] Freeze filename and digest/omission vectors in `tests/fixtures/lms_sources/safe_names.json`; verify with `tests/test_identity.py::test_design_fixture_resource_ids`
- [ ] T003 [US2] Freeze disabled policy, sanitized row/menu trace and identical allowed-GET versus mixed-case-`Range` vectors in `tests/fixtures/lms_sources/policy.json`, `course_navigation.json` and `request_headers.json`; verify with `tests/test_cnu_ui_policy.py::test_range_header_blocks_otherwise_allowed_get`

Checkpoint: fixture schema, expected IDs, disabled policy and sanitized event vocabulary are fixed; no provider fixture claims approval.

## Foundational (blocking)

- [ ] T004 [P] [US1] Implement pure assignment/notice/material ID builders in `src/campusctl/identity.py`; verify with `tests/test_identity.py::test_fixture_identity_vectors`
- [ ] T005 [P] [US1] Freeze full/partial/unknown/filtered merge vectors in `tests/fixtures/lms_sources/catalog_transitions.json`; verify with `tests/test_domain_catalog.py::test_fixture_merge_transitions`
- [ ] T006 [P] [US2] Implement deny-by-default `UiRequestPolicy` and header-aware `guard_ui_request` in `src/campusctl/providers/cnu/ui_policy.py`; verify with `tests/test_cnu_ui_policy.py::test_range_header_blocks_otherwise_allowed_get`

Checkpoint: three pure interfaces and their fixtures are available; no command, network request or capability is enabled.

## US1 — Domain catalog identity and staleness

- [ ] T007 [P] [US1] Implement path/read/write/merge/unknown-state helpers in `src/campusctl/domain_catalog.py`; verify with `tests/test_domain_catalog.py::test_fixture_merge_transitions`
- [ ] T008 [US1] Add atomic write, missing catalog, stale-marker and unchanged-discovery-timestamp coverage in `tests/test_domain_catalog.py`; verify with `tests/test_domain_catalog.py`
- [ ] T009 [US1] Add missing-component and legacy notice-key golden assertions in `tests/test_identity.py`; verify with `tests/test_identity.py`

Checkpoint: domain catalogs coexist with unchanged lecture catalog; complete/partial/unknown/filtered transitions are observable with no uncommitted course rows.

## US2 — Ordinary course context and request boundary

- [ ] T010 [P] [US2] Implement authenticated row→menu navigation in `src/campusctl/providers/cnu/course_context.py`; verify with `tests/test_cnu_course_context.py::test_enters_section_after_course_row`
- [ ] T011 [US2] Add empty-section, wrong-course, route-denial, logging/media/redirect fixtures and non-ranged/ranged GET pair in `tests/test_cnu_course_context.py` and `tests/test_cnu_ui_policy.py`; verify with `tests/test_cnu_course_context.py` and `tests/test_cnu_ui_policy.py::test_range_header_blocks_otherwise_allowed_get`

Checkpoint: fake page records context-establishing row click before section entry; unreviewed/logging/media/redirect requests fail closed and the identical approved GET with `Range` header fails while the non-ranged request proceeds.

## US3 — Inert CLI, renderer and browser hooks

- [ ] T012 [US3] Implement sorted `pkgutil` discovery and strict partial/malformed-module rejection in `src/campusctl/commands/__init__.py`; verify with `tests/test_command_discovery.py::test_partial_and_malformed_modules_raise`
- [ ] T013 [US3] Derive domain parser/dispatch and capability composition from discovery, keeping reserved `sync --headless` rejection in `src/campusctl/cli.py`; verify with `tests/test_command_discovery.py::test_no_registered_domains_preserve_lecture_capabilities` and `tests/test_cli_output.py`
- [ ] T014 [US3] Derive renderer routing from the same discovery while preserving lecture branches in `src/campusctl/presentation.py`; verify with `tests/test_command_discovery.py::test_discovered_domain_renderer` and `tests/test_presentation.py`
- [ ] T015 [US3] Add only a non-advertising pointer to separately released per-domain contracts in `docs/contracts/cli.md`; verify with `tests/test_command_discovery.py::test_contract_pointer_does_not_advertise_unregistered_domain`
- [ ] T016 [US3] Add optional internal `headless: bool = False` to `src/campusctl/browser.py` without enabling lecture playback headless; verify with `tests/test_browser.py`

Checkpoint: existing lecture result, human presentation, and `CAPABILITIES` value are unchanged; a later approved domain participates by adding only `commands/<domain>.py`, its provider/test files, and `docs/contracts/<domain>.md`.

## US4 — Future source-package fixture only

- [ ] T017 [US4] Validate deterministic manifest/resource digest and safe-name examples in `tests/test_identity.py` from `tests/fixtures/lms_sources/package_schema.json` and `safe_names.json`; verify with `tests/test_identity.py::test_design_fixture_resource_ids`

Checkpoint: design vectors freeze PR4/PR5 naming and package contracts without implementing fetch, download or writing a package.

## Polish

- [ ] T018 [US3] Review `src/campusctl/commands/__init__.py`, `src/campusctl/cli.py`, `src/campusctl/presentation.py` and `docs/contracts/cli.md` for unchanged released lecture behavior, strict discovery, and only the non-advertising contract pointer; verify with `tests/test_command_discovery.py`, `tests/test_cli_output.py` and `tests/test_presentation.py`
- [ ] T019 [US4] Review sanitized fixtures under `tests/fixtures/lms_sources/` for no credential/token/query/student identifier/video payload; verify with `tests/test_cnu_ui_policy.py::test_unapproved_fixture_denies_every_request`

Checkpoint: PR1 is private, fixture-only with respect to provider operations, and ready for one integrated validation after lanes merge; no live LMS check or project-wide suite in a lane.

## Dependencies & parallel lanes

1. **Fixture/schema lane (sequential):** T001–T003 establish IDs and design-vector format first, then T017/T019 after consumers exist. This small shared-fixture boundary has one owner; other lanes consume the frozen JSON paths.
2. **Identity/catalog lane:** T004 and T005 may run `[P]` with policy/navigation; T007 follows identity/schema and owns only `domain_catalog.py`; T008–T009 finish sequentially in this lane.
3. **Course/policy lane:** T006 and T010 are disjoint `[P]` files after T003; T011 joins their fixture tests sequentially.
4. **Discovery/CLI integration lane (sequential):** T012–T016 after function signatures from lanes 2–3 are fixed; one PR1 owner edits the shared CLI/presentation/browser/contract pointer. T018 follows integration. No domain PR edits these files.

PR1 discovery and its contract pointer are merged. **PR1.1** owns the shared request guard/interceptor and navigation-only course entry before PR2/PR3/PR4 begin. Separate implementation lanes add only their own domain modules, providers, tests and per-domain contracts; PR4 consumes the selected-file binder without editing the foundation. PR5 waits for all three. Release integration serializes CHANGELOG/version changes; no lane edits the external legacy notifier.

## PR1.1 — Request boundary follow-up (single sequential implementation lane)

- [ ] T020 [US2] Make `enter_course_section` navigation-only on an already-authenticated page (remove internal `ensure_logged_in`, no flag) and update authenticated fake-page/row→menu assertions in `src/campusctl/providers/cnu/course_context.py` and `tests/test_cnu_course_context.py`; verify `pytest -q tests/test_cnu_course_context.py`
- [ ] T021 [US2] Extend `UiRequestPolicy.from_reviewed_config` to validate named per-operation `suppress`, static origins/types, selected-file templates and per-route optional `query` validators (`cachebuster`, `encrypted`, `page`, `board-item-id`); deny repeated/unknown names, unsafe values, logging/data overlap and malformed URL/config in `src/campusctl/providers/cnu/ui_policy.py` and `tests/test_cnu_ui_policy.py`; verify `pytest -q tests/test_cnu_ui_policy.py`
- [ ] T022 [US2] Implement `guard_ui_request` returning `"allow"|"suppress"` with Range/redirect precedence, only reviewed suppressions, static GET resource-type exclusions and exact data routes in `src/campusctl/providers/cnu/ui_policy.py`; add synthetic suppression/static/unknown-route matrix in `tests/test_cnu_ui_policy.py`; verify `pytest -q tests/test_cnu_ui_policy.py`
- [ ] T023 [US2] Implement `match_selected_file_path` and `bind_selected_file_request` for the two approved L/C templates, selected ID/exact URL and official-control/fileDownload provenance in `src/campusctl/providers/cnu/ui_policy.py`; test selected/wrong ID, encoded slash, traversal, double encoding, segment suffix, query, redirect and Range in `tests/test_cnu_ui_policy.py`; verify `pytest -q tests/test_cnu_ui_policy.py`

Checkpoint: a missing or malformed approval cannot expand access; exactly reviewed Panopto requests suppress, not arbitrary logging or data; an attachment template alone never authorizes a second file.

- [ ] T024 [US2] Implement `UiRequestDiagnostics`, `UiRequestInterceptor` and `install_ui_request_interceptor` with Page/BrowserContext route installation, complete headers, real `request.redirected_from`, abort/continue/deny propagation and scoped cleanup in `src/campusctl/providers/cnu/ui_policy.py`; use fake Playwright request/route for page and reused context in `tests/test_cnu_ui_policy.py`; verify `pytest -q tests/test_cnu_ui_policy.py`
- [ ] T025 [US2] Exercise pre-authenticate-once → install guard → pinned `/std/myLecture` roster/re-entry → per-course navigation, and mid-operation login redirect denial/no re-login, on fake pages in `tests/test_cnu_course_context.py` and `tests/test_cnu_ui_policy.py`; verify `pytest -q tests/test_cnu_course_context.py tests/test_cnu_ui_policy.py`
- [ ] T026 [US3] Check PR1 lecture capability/CLI unchanged and existing policy/nonranged/ranged tests remain valid, updating existing tests only where the `guard_ui_request` result changed, in `tests/test_cnu_ui_policy.py` and `tests/test_command_discovery.py`; verify `pytest -q tests/test_cnu_ui_policy.py tests/test_command_discovery.py`
- [ ] T027 [US4] Inspect PR1.1-only fixture/test data for no real course identifiers/titles, signed URLs, credentials or media payload in `tests/test_cnu_ui_policy.py`, `tests/test_cnu_course_context.py` and any new `tests/fixtures/lms_sources/` fixture; verify `pytest -q tests/test_cnu_ui_policy.py::test_fixture_hygiene_scans_lms_sources`

Checkpoint: a fake page/context aborts and counts only reviewed suppressions, propagates all other denials before catalog commit, unregisters its handler, and preserves PR1's released lecture contract. PR1.1 alone owns `ui_policy.py`, `course_context.py`, their targeted tests and own synthetic fixtures; domain lanes begin only after this lane merges. Integrated validation runs once after parallel lanes; no lane runs project-wide checks mid-flight.
