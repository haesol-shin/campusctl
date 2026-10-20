# Package responsibility structure

[Constitution](../constitution.md) · [Specification index](../README.md) · [Code standard](code-standard.md) · [CLI contract](../../contracts/cli.md) · [Assignments](../../contracts/assignments.md) · [Notices](../../contracts/notices.md) · [Materials](../../contracts/materials.md) · [Contribution policy](../../../CONTRIBUTING.md)

## Status, intent and limits

Planning only; implementation and verification gates below are future work. Understood as: redesign ownership across the whole package, including safety and browser infrastructure, through small behavior-neutral structural PRs, each immediately followed by a separate logic-cleanup PR. P1–P11 and the material/browser splits are all required roadmap work, not optional follow-ups. Playback structure and its cleanup through P6 precede configured YouTube speed; the remaining required work does not block that feature.

Baseline source map: `2a48f17`; code-standard measurements: `331a51e`. Locations below are inspection offsets under `src/campusctl/`, not promised future offsets. Each implementation PR measures and compares its immediate integrated parent. This revision changes only this specification. No implementation, browser/LMS run, push, GitHub write, version change or changelog fragment is included. Carry relevant plan sections into each implementation outcome; do not publish a separate specification-only outcome PR. Behavior-neutral PRs need no fragment under [fragment policy](../../../changelog.d/README.md).

Public commands, help, JSON bytes, errors, exit codes, catalogs, packages, receipts and ordered LMS/filesystem actions must remain unchanged in both structural and logic-cleanup PRs. A new provider, domain, command or media kind is a future feature, not an implementation deliverable here. The [code standard](code-standard.md) is binding. Its separate-commit minimum for idiom changes is strengthened here to a separate PR: never mix logic cleanup with a structural move.

Owner decisions: “안정장치도 이번에 수정하면 좋지 않아? 이런 일 자주 없으니까 수정 한 번에 다 하고 싶은데” includes material/browser safety ownership in this roadmap. Logic goals are “코드 로직이 깔끔하게”, “함수 정리 및 python 문법 적극 활용 및 class 활용”, “모범적인 코드”; the standard determines where functions, Python idioms and classes actually improve clarity. The merge choice “전자로” means separate small PRs, automatic merge after all gates and a result report, as specified below.

## 1. Current map and verified problems

### Layers and import directions today

| Area | Current owners and edges | Maintenance consequence |
| --- | --- | --- |
| Process and CLI | `cli.py:127-207` builds parsers; `654-887` dispatches; `890-977` handles process output, status, profile and publication. `cli.py:488,838` imports CNU courses/player. | Adding a command usually edits parser, dispatch, command-key calculation, profile eligibility, capabilities and rendering separately. |
| Domain commands | `commands/__init__.py:7-53` discovers sorted modules requiring register/dispatch/render/sync/CAPABILITY. Assignment, material and notice modules use it; lectures and local commands do not (`cli.py:49-55,205-206,764-834`). | Two command conventions; a local command has no meaningful sync hook. |
| Output | `presentation.py:10-16,734-810` discovers command renderers and handles other commands itself. Notice/material renderers import its private width helpers (`commands/notices.py:290-297`, `commands/materials.py:325-329`). Both commands also import parser types from CLI (`notices.py:112`, `materials.py:28`). | Reverse imports require delayed imports to avoid cycles. Materials picker calls the global renderer (`commands/materials.py:83-84`). |
| Application work | `sync.py:13,30-76` imports CNU sync and its outcome data; `source_package.py:18-19,263-293` imports CNU transfer and content policy. Fetch queue imports CNU login and selector (`commands/fetch_queue.py:13,157`). | Generic aggregation/publication depends on the only provider; another provider would require changes in unrelated core and command code. |
| Provider workflows | `providers/cnu/sync_all.py:399-591` owns lock, session, auth, roster, serial courses, domain isolation, preparation and publication. `_section:228-328` holds per-domain routing. Commands directly import CNU capture, login, archive private helpers and transfer (`commands/materials.py:203-216`, assignments `195-196,241`, notices `62,217-218,263`). | Provider UI details leak upward; adding a domain edits a central branch chain and multiple route maps. |
| Playback | `providers/cnu/player.py:269-331,877-1059,1136-1424` combines selection, preview lifetime, media dispatch, queue, row verification and catalog update. YouTube code is `676-835`; Panopto auth `358-396`, frame/splash `533-659`, speed/monitoring `848-874,937-1034`. | A media change can alter queue/auth/cleanup and diagnostics accidentally; rate restrictions occur at item and replay-queue levels (`894-898,1165-1177`). |
| Local persistence | `catalog.py`, `domain_catalog.py`, `catalog_view.py`, `material_files.py`, `source_package.py` own different local schemas and publication rules. `status.py:46-98` has domain-specific projections. | These are not interchangeable generic repositories; preserving identity and failure semantics matters more than uniform file shape. |
| Browser and support | `browser.py:75-369` profiling context/document timing; `399-419` session lease; `431-480` SSO popup ownership; `483-641` protocol/CDP helpers; `763-950` session lifetime. Config, credentials, lock, paths and clocks are leaf services. | These are distinct owners, but cancellation, lock and page lifetimes must survive extraction. Split them only after #95 lands, without changing launch policy. |

The baseline contains 47 Python source files. AST/count inspection confirms `player.py` 1,424 lines, `cli.py` 981, `material_files.py` 1,020, CNU `materials.py` 977, `browser.py` 950 and `presentation.py` 810. Long functions: `play_lectures` 289 lines, `_play_visible_lecture` 183, notice detail capture 264, login 209, notice grid snapshot 204, combined sync 193, session opening 188, official transfer 179 and attachment publication 165. These are inspection facts, not complexity scores or a mandate to split every long function.

### Four extension paths today

| Change | Current required touch points | Target owner after migration |
| --- | --- | --- |
| New command | Inline path: `cli.py:127-207,586-610,654-887,905-919`, `presentation.py:734-790`, capabilities `cli.py:50-55`. Domain path: new `commands/<name>.py` plus discovery hooks `commands/__init__.py:7-53`; top-level help grouping `cli.py:58-80` still needs an edit. | One command module plus one ordered composition entry; help grouping, parser, dispatch, render and profile metadata come from that entry/module, not several branch chains. |
| New playback media | Normalization `providers/cnu/lectures.py:102-108`; display `presentation.py:235-237`; eligibility `player.py:316-321`; dispatch `917-960`; auth `1182-1186`; queue-specific speed restriction `1165-1177`. | One handler module and one media table entry; provider label normalization/display remain separate responsibilities if a genuinely new catalog label is introduced. |
| New LMS domain | CLI/discovery above; canonical domains `catalog_view.py:25`; supported catalog set/type `domain_catalog.py:16-17`; sync parsing `sync.py:16-27`; provider routes/page kinds `sync_all.py:41-52`, section branch `228-328`, staging `340-396`; status `status.py:46-98`; headless support `browser_options.py:9-18`. | One local domain definition, one provider section owner, one command owner and their explicit composition entries. A new status meaning or schema is still an intentional feature contract change. |
| Another provider | Validation `config.py:117-118`; generated defaults `config_init.py:27-68`; keyring namespace `credentials.py:17`; browser profile/session conventions `browser.py:763-950`; CNU imports above; entity identities `identity.py:10-25`; package provenance `source_package.py:153-157`. | Provider package plus composition selection. Provider-specific config, credential namespace, profile and identity policies must be designed with that real provider; this plan removes import barriers without pretending those contracts are already portable. |

### Priorities

1. One speed rule: `cli.py:48` list duplicates `config.py:13` tuple. Parser/doctor/config can drift (`cli.py:203,343`, `config.py:151-154`). Cheapest high-confidence first PR.
2. One command pipeline and acyclic UI helpers: reverse imports and two dispatch styles multiply the edit sites for every feature.
3. Per-media control owners: queue/preview/official-row publication are different from YouTube or Panopto playback loops. Separate them without merging their different state machines.
4. Provider boundary: local packages, aggregation and command parsing must not know CNU selectors, login types or transfer implementation.
5. Per-domain section ownership and notice parsing: routing branches and detail-to-collector dependency make domain changes harder than necessary.

The earlier narrow draft's source observations were rechecked rather than accepted as authority. Its deferral of command/player structure and speed-first order does not meet this broader goal and is not retained.

## 2. Target package and dependency rules

Keep the existing flat core rather than moving every file into new core/application/infrastructure directories. New files are introduced only at real responsibility boundaries. Unlisted `__init__.py` files are package markers, not re-export facades.

| Target path under `src/campusctl/` | One job |
| --- | --- |
| `cli.py` | Resolve output mode, parse arguments, invoke the selected command, emit the final envelope and handle process-level interruption. |
| `composition.py` | Assemble the ordered command modules, render routing and CNU operation bindings once; the sole non-provider importer of concrete provider implementation. |
| `command_types.py` | Plain command metadata/result records: help group/order, existing capability contribution, profile scope and final response/publication facts. |
| `cli_args.py` | Existing parser subclasses, help formatting and JSON-option helpers, with no command imports. |
| `text_layout.py` | Pure cell width, wrapping and truncation functions, with no command or provider imports. |
| `presentation.py` | Format shared envelope errors/cache advice and write human lines using an injected renderer map. |
| `progress.py` | Own existing progress line formatting and carriage-return/finish state. |
| `commands/{config,doctor,auth,setup,sync,status,courses,lectures,assignments,notices,materials}.py` | Own one command family's arguments, input policy, dispatch and command-specific rendering; call bound operations, not provider modules. |
| `commands/course_inputs.py` | Existing cached/network course-selection and refresh policy shared by commands. |
| `commands/fetch_selection.py` | Pure explicit-ID normalization and exact-one catalog matching for assignment/notice fetch. |
| `commands/fetch_queue.py` | Ordered fetch progression and item/session-failure handling using supplied authentication, capture and failure-classification callbacks. |
| `sync.py` | Validate canonical domain order and aggregate provider-neutral domain outcomes using a supplied collector. |
| `operation_types.py` | Existing `DomainOutcome` and small callable type aliases for bound operations; no provider imports or behavior. |
| `source_types.py` | Existing plain `ResourceReference` and `DetailSnapshot` data. |
| `transfer_types.py` | Existing plain `OfficialAttachmentTarget` and `FetchedAttachment` data, preserving fields exactly. |
| `attachment_policy.py` | Existing extension, size, MIME/signature and incremental validation policy moved from CNU request policy without broadening it. |
| `source_resources.py` | Resolve selected resources and image bytes under the existing rules, with a supplied official-transfer callable. |
| `source_package.py` | Build/digest/reuse/atomically publish local source packages from supplied resolved resources, not perform provider UI transfers. |
| `domains.py` | Ordered local domain definitions and supported catalog/headless metadata; no browser or command discovery. |
| `providers/cnu/operations.py` | Bind existing CNU discover/play/fetch/download operations, provider-specific errors and session-expiry probes. |
| `providers/cnu/sections/{lectures,assignments,notices,materials}.py` | Each owns its menu/path/page-kind metadata and current capture-before-click/collect sequence. |
| `providers/cnu/sync_all.py` | Preserve shared auth/roster/course traversal, domain failure boundaries and staged publication, calling section functions. |
| `providers/cnu/player.py` | Serial playback queue, authoritative row verification, result aggregation and catalog completion publication. |
| `providers/cnu/player_preview.py` | Course/row preview UI lifetime, modal opening/closing and media-handler invocation, including cleanup precedence. |
| `providers/cnu/player_types.py` | Existing playback failure and diagnostic data/formatting shared by preview and handlers, with no imports back to player. |
| `providers/cnu/media/{youtube,panopto}.py` | Each owns frame discovery, player-specific startup/auth, speed restrictions and observation for one implemented playback kind. |
| `providers/cnu/media/__init__.py` | Explicit mapping from existing catalog media keys to handler functions/metadata. |
| `providers/cnu/notice_parsing.py` | Pure notice identities/metadata parsing shared by collection and selected detail. |
| `material_files.py` | Download layout and public download orchestration; no compatibility re-exports for moved helpers. |
| `material_publication.py` | Safe no-clobber publication, pinned-directory ownership and inode checks as one indivisible safety unit. |
| `material_adoption.py` | Existing bounded adoption policy, using the publication unit without duplicating its checks. |
| `material_receipts.py` | Receipt serialization, validation and verification, preserving exact bytes and identity. |
| `browser.py` | Session lifetime and launch orchestration; keep the existing `open_session` cleanup scope. |
| `browser_profiling.py` | Profiling context/labels and document timeline integration; recorder serialization remains in `profiling.py`. |
| `browser_lease.py` | Session lock lease ownership and release. |
| `browser_sso.py` | Owned SSO popup discovery, settling and cleanup. |
| `browser_protocol.py` | Bounded protocol/CDP helpers; no session acquisition or launch policy. |
| Existing remaining modules | Retain the responsibilities listed below; no cosmetic package relocation. |

### Composition, not a framework

Use ordinary functions/modules and a few plain records. No base command/provider/player class, service container, inheritance tree, external plugin discovery or import-time self-registration. `composition.py` is an explicit composition root, not a place for workflow decisions. Build bindings once per CLI invocation, not per row/poll.

Replace current directory scanning with a single ordered tuple of command modules. Each module exposes `register`, `dispatch`, `render` and plain metadata; a module need not implement sync to be a command. Required registration order remains config, doctor, auth, setup, sync, status, courses, lectures, assignments, materials, notices, preserving baseline parser choice/help bytes. Help groups retain their existing explicit order, including notices before materials in Everyday. Store help-group position separately from parser registration position. Capability contributions retain the exact existing keys/order/values: sync, lectures, status, then assignments, materials, notices; do not suddenly advertise local commands. Select command dispatch/render/profile rules from this same assembled table. Root version, usage and global output-mode handling remain process policy.

A plain command result record replaces ad hoc namespace output fields (`_status`, `_status_errors`, `_course_roster`, `_material_generation`) only when its command moves. Preserve the existing `(result, error)` semantics within workflows; convert at the command boundary. The record holds envelope status/errors plus an optional post-output publication function. Invoke that function only after successful human rendering and stdout flush, in exactly the existing order. Materials picker receives the assembled output function as an input rather than importing `presentation` again. Do not turn callbacks into a general event bus.

The media table has exactly `video -> panopto` and `youtube -> youtube`: baseline has no separately implemented plain-video backend. `video` is the catalog label used for the Panopto path, not a third independently proven handler. Each handler supplies plain functions for validation, queue preparation and preview playback. Queue preparation runs once in first-seen required-handler order at the existing point after LMS authentication, and only Panopto requires its current SSO step. Preserve baseline queue-wide replay/YouTube speed rejection before authentication; the handler supplies the rule, and the queue preserves when it runs. Item-level restrictions remain at their original point too until the separate speed feature removes them. The table removes scattered media selection branches, not differing playback algorithms.

Preview owns opening and closing exactly once; handlers never close the modal, write catalogs or own the queue. Handlers can update the existing diagnostic record and invoke existing started/position callbacks at the same moments. Keep cleanup-error precedence, elapsed accounting and callback-failure handling unchanged. Shared numeric/state helpers move only when both handlers actually use identical behavior. In particular do not silently make Panopto rate checks reject Booleans or equate its rate predicate with YouTube's exact-1x predicate during extraction.

Operation bindings are a small plain record of existing callable seams, not a provider super-interface. Commands receive only operations they use. CNU operations retain provider-specific authentication, notice error adaptation and archive identity checks. `sync.run_sync` receives a collector; `DomainOutcome` moves to `operation_types.py`. Source packaging receives resource resolution/transfer explicitly, with no default CNU callback. Transfer target fields describe the existing verified official-control data; moving that data does not make another provider's UI conform to it. A future provider may need its own target representation and contract change rather than adding optional fields preemptively.

### Dependency direction

- Process CLI imports composition and UI/core contracts. Composition may import commands and concrete providers. No command imports CLI, composition or concrete provider modules; it receives bound operations.
- Commands may import leaf UI helpers, local services and operation types. Presentation takes renderers as inputs; it never discovers/imports command modules. Command renderers import `text_layout`, not presentation internals. Provider code never imports commands, CLI, composition or presentation.
- Core data, catalogs, local publication, policy and sync aggregation never import concrete providers. Provider implementations may import those core owners. Cross-provider imports are forbidden.
- Provider queue imports preview; preview imports media table and player types; handlers import only player types/leaf browser/clock helpers and necessary CNU login data. No handler imports queue or preview. Media startup requiring preview diagnostics stays in the handler, not the queue.
- Shared speed values remain the existing `config.SUPPORTED_SPEEDS` tuple; doctor creates `list(SUPPORTED_SPEEDS)` to retain its Python result shape. Do not introduce a separate speed module for three values. Content policy lives in `attachment_policy`; identity serialization stays in `identity.py`; canonical domain order moves once from `catalog_view` to `domains`.
- Browser session imports lease/SSO/protocol/profiling owners; these leaf owners never import `browser.py` or acquire a session. Pass bounded-await and profile hooks explicitly where needed to avoid reverse imports; do not duplicate them. Material layout/orchestration imports adoption, receipts and publication; adoption/receipts may use the publication safety unit, never import the orchestrator. Shared plain data moves to its actual owner without a re-export facade.
- No compatibility aliases, re-export wrappers or duplicate constants remain after each cutover. Migrate all source/test/tool imports in the same PR. Language-server references are required if available; none was configured during this inspection, so implementation must otherwise audit exact-symbol imports, attributes, dynamic imports and monkeypatch sites.

### Remaining owners and deliberate non-changes

- `catalog.py` owns lecture schema/merge/publication; `domain_catalog.py` owns the existing three non-lecture schemas/retention; `catalog_view.py` owns roster/cache and printed selection snapshots. Do not force lecture catalog representation into the other schema. `status.py` owns local status projections and stale coverage; new semantics require an explicit projection, not guessed generic counting.
- Material download ownership is split in P12–P14: layout/orchestration stays in `material_files.py`, safe publication moves as one unit, then adoption and receipts get their own owners. Never separate pinned-directory handles from inode validation or duplicate their safety checks in callers.
- Browser ownership is split in P15–P18: profiling/document timeline leaves browser first, then lease, SSO and protocol/CDP helpers. Session lifetime stays in `browser.py`; do not fragment `open_session`'s resource scope, move launch policy into helpers or merge lock scopes. #95 must already be merged before any browser extraction.
- `config.py`, `config_init.py`, `setup.py`, `credentials.py`, `paths.py`, `lock.py`, `envelope.py`, `course_selection.py`, `identity.py` and package version remain their existing owners. Move inline doctor diagnostics to `doctor.py` as a service used by `commands/doctor.py`; keep `config_init` and setup interactive workflows rather than duplicating them in command modules.
- CNU `login`, `course_context`, `courses`, `readiness`, `roster_diagnostics`, lecture normalization and standalone sync entry points remain provider work. Preserve `providers/cnu/sync.py` lecture collection/health behavior and migrate its internal consumers when section ownership changes. `selected_file_policy` and `attachment_transfer` retain CNU origin/control/redirect binding. Assignment/detail, notice/detail and materials collectors keep distinct event/capture/restoration state machines. L10 also covers these retained provider owners for complexity cleanup; it is not a universal collector rewrite.
- Do not merge `_RequestWindow`, `_TodoCapture` and `_NoticeCapture`, generalize domain error adapters, remove legacy notice identities, or unify CSS escaping. `course_context.py:83-94` escapes all C0/DEL, whereas `player.py:334-343` escapes fewer controls; changing either is not neutral. Keep comments explaining identity, lifecycle and protocol constraints; no comment/format sweep. Ruff already exists (`pyproject.toml:28-38`).
- Plain source/transfer/result records are data, not behavior hierarchies. Existing stateful validators, capture objects and session context managers remain classes where they actually own incremental state or resource lifetime.

### Rejected alternatives

| Alternative | Why rejected |
| --- | --- |
| Only deduplicate helpers, defer player/CLI indefinitely | Does not solve the requested extension and single-responsibility problems. |
| New top-level core/application/infrastructure hierarchy for every file | Huge import churn without a new responsibility; the flat leaf core is already navigable. |
| Keep two command conventions or extend mandatory sync hooks to local commands | Every command still requires choosing a special path or adding a fake operation. |
| Preserve automatic scanning as a third-party plugin API | No such public CLI/provider contract is documented; current internal discovery tests do not justify a plugin framework. Explicit composition is easier to inspect and preserves existing CLI behavior. |
| Abstract provider/command/media base classes or a universal registry | More contracts and inheritance than the single current provider needs. Two small explicit tables and function inputs cover actual seams. |
| One shared media monitoring loop or eager rate-predicate cleanup | YouTube native autoplay and Panopto resume/splash/replay behavior differ; consolidation would hide behavior changes. |
| Generalize all domain collection/catalog/status logic | Notice global to-do, lecture completion and archive restoration are genuinely different. Share orchestration, keep domain decisions local. |
| Structure and YouTube speed in one PR | Destroys neutral comparison and makes a rate regression hard to attribute. |

## 3. Ordered migration PRs

Sizes estimate additions plus removals, including callers, tests and plan updates; they are not net growth or permission to bundle outcomes. P denotes structural ownership; L denotes its immediately following logic PR; G is the early lint-only gate. Every row is a separate PR. A row that exceeds its range must be narrowed into complete owner/caller cutovers before review, never a broken intermediate state. No next row starts while the preceding row's gates or reviews are unresolved.

| Order | One outcome and expected changed lines | Risk | Additional equivalence cases |
| --- | --- | --- | --- |
| P1 | One speed definition; preserve doctor list shape. **20–60** | Low | Accepted/invalid speeds, Boolean config rejection, help choice order and doctor JSON/text. |
| G | C901/RUF100 lint gate and per-function baseline only; no body cleanup. **60–120** | Low | Full lint; no executable changes; compare baseline identities/scores and public CLI corpus. |
| L1 | Simplify only moved speed consumers if justified; no second speed owner. **10–40** | Low | Same speeds, error precedence and doctor output. |
| P2 | Acyclic parser/help, text layout and progress leaf owners; migrate callers. **450–750** | Low–medium | Fixed-width help, wide/combining characters, IDs, progress CR/newline/finish on failure/interrupt. |
| L2 | Guard clauses and pure layout/progress steps in moved code. **100–250** | Low–medium | Width boundaries, empty cells, interruption and finish state. |
| P3 | Local command owners, doctor service, metadata/result and explicit composition; remove discovery. **900–1,350** | Medium | Prompt/auth/config precedence, doctor/status errors; domain parsers/renderers/capabilities survive without scanning. |
| L3 | Local command branching and result shapes; explicit JSON-edge conversion. **250–500** | Medium | Status/envelope keys, omission/order, post-output callback and prompt timing. |
| P4 | One command pipeline; course inputs, fetch selection and remaining renderers; remove old dispatch. **1,100–1,700** | Medium | Registration/help/capability order, refresh old cache, profile scope, fetch/replay consent, post-flush publication/failure. |
| L4 | Command/render/input logic; internal records and shared envelope status set. **350–650** | Medium | Duplicate/untrimmed IDs, validation-before-session, exact recursive sync rendering and JSON bytes. |
| P5 | Player types, preview lifetime and YouTube handler/table; video remains in preview. **850–1,250** | Medium | Autoplay blocked/advancing, both 1x restrictions, missing video, stall/end grace, callbacks, cleanup precedence. |
| L5 | YouTube predicates/parsing, preview pure steps and media value set. **200–400** | Medium | Boolean/rate predicates, replay coverage, event timestamps and primary/cleanup failure precedence. |
| P6 | Panopto handler; remove remaining media dispatch branches through metadata. **800–1,200** | Medium | Mixed queues, one SSO at original point, splash deadlines, resume/replay, row verification, catalog failure/cancellation. |
| L6 | Panopto pure steps and queue/result logic; retain distinct playback state machines. **250–500** | Medium | Persisted rates, recorded/no-attendance, first-seen preparation order, elapsed time and cancellation. |
| Speed | Separate configured YouTube speed feature, after L6; not a neutral move. **Size/risk set by its feature plan** | Behavior-changing | Its own feature/live acceptance; rebase later neutral comparisons onto its integrated result. |
| P7 | Provider-neutral outcomes and bound sync/discovery operations through composition. **300–550** | Low–medium | One session, canonical order, domain failure/busy/75, close-before-publication, discovery fallback. |
| L7 | Outcome/accumulator records and aggregation/discovery guards. **120–300** | Low–medium | First/later failure, partial outcomes, profile labels and output omission/order. |
| P8 | Provider-free source/transfer data, attachment policy and resource resolution/publication. **850–1,300** | Medium, safety-sensitive | Resource omission/deduplication, target/signature/size/redirect failure, dispose/unlink, package bytes/digests/reuse. |
| L8 | Resource/package pure steps and typed internal transfer values. **250–500** | Medium, safety-sensitive | Lazy transfer order, no extra copies/hashes, cleanup ownership and no publication on failure. |
| P9 | Inject CNU auth/capture/error/expiry/archive bindings into command/fetch workflows. **600–950** | Medium | Item continues/session stops, no extra detail read, notice mapping, receipt-before-auth, restoration/transfer order. |
| L9 | Fetch queue/error branches and internal capture/result shapes. **180–400** | Medium | Single/batch differences, later unknown ID, saved/reused/adopted/skipped outputs. |
| P10 | Domain metadata and CNU section owners/table; add import-direction check. **500–800** | Medium | Capture-before-click, response binding, empty/incomplete, to-do isolation, subsets, prepare-all-before-publication. |
| L10 | Domain StrEnum, section/outcome shapes, sync/status projections; retained provider collectors/login pure steps. **400–800** | Medium | Archive restoration, partial write, stale coverage, login/capture cleanup and ordered LMS steps. |
| P11 | Pure notice parser/identity owner; remove detail-to-collector dependency. **570–750** | Low–medium | Native/legacy/missing/ambiguous joins, dates, deleted/top duplicates, malformed/count-zero envelopes, privacy omission. |
| L11 | Notice parsing guards and typed internal parsed values. **150–350** | Low–medium | Same identities, whitespace, number/date parsing, malformed input and omitted fields. |
| P12 | Extract safe material publication as one pinned-directory/inode unit; layout remains in `material_files.py`. **800–1,100** | High, filesystem safety | Symlink/directory replacement and inode mismatch, no-clobber race, pin lifetime, cancellation and cleanup. |
| L12 | Pure publication validation/predicates and layout values; retain ordered safety protocol. **150–300** | High, filesystem safety | Exact check/action order, no unsafe fallback, original error/cleanup precedence, destination bytes. |
| P13 | Extract bounded material adoption owner. **250–400** | Medium, filesystem safety | Bounds, candidates, existing/mismatched files, no unverified adoption or extra traversal. |
| L13 | Adoption decision steps and internal result values. **80–180** | Medium | Same adoption/reuse/skip decisions, first failure and filesystem effects. |
| P14 | Extract material receipt owner. **450–650** | Medium, persistence | Missing/malformed/stale receipts, identity/hash mismatch, exact key order/omission and auth fast path. |
| L14 | Receipt validation guards and internal records at JSON edge. **120–250** | Medium | Exact receipt bytes, validation errors, old receipts and verification order. |
| P15 | Extract browser profiling/context and document timeline together. **650–850** | Medium | Nested labels/context reset, event order/timestamps, listener removal, session-close flush and failed navigation. |
| L15 | Timeline/context state and pure profile payload steps. **150–300** | Medium | Same diagnostic fields, elapsed values, cancellation and callback failures. |
| P16 | Extract session lock lease owner. **120–220** | High, lifetime safety | Busy/75, failed acquire, release exactly once, cancellation, nested cleanup and original lock scope. |
| L16 | Lease state transitions and guards without changing ownership. **40–100** | High, lifetime safety | Primary/release failure precedence, failed launch and no leaked/reacquired lock. |
| P17 | Extract owned SSO popup owner. **150–250** | Medium | Owned/unowned pages, popup close races, settle deadlines and cancellation; no extra click. |
| L17 | SSO predicates and bounded settling steps. **60–140** | Medium | Same best-effort exceptions, page close order and timeout behavior. |
| P18 | Extract protocol/CDP helpers; browser retains session/launch lifetime. **350–550** | High, lifetime/protocol | Bounded awaits, attach/detach failures, unsupported CDP, launch flags from #95, session cleanup. |
| L18 | Protocol payload validation and session lifetime pure steps; final complexity/data-shape audit. **200–450** | High, lifetime/protocol | Exact launch/context/page/lease teardown order, cancellation, cleanup errors, profile flush and no extra protocol calls. |

P1–P18, G and L1–L18 are required roadmap stages. A logic row is bounded cleanup, not permission to rewrite surrounding owners. Do not invent cosmetic changes to satisfy a line estimate: if a stage has no justified cleanup, stop and report that finding for an explicit scope decision rather than silently skipping it or merging a no-op PR.

### Logic cleanup and complexity coverage

Land G immediately after P1 and before L1/P2. Follow the code standard exactly: C901 threshold **15**, RUF100, the existing documented BLE001 external marker, and tests/tools exclusions. Measure the integrated parent, expected to have 29 source functions above 15 (15 above 25 and 14 at 16–25), and stamp only that per-function baseline. Do not use source-wide/file-wide C901 ignores. Track original function identity, parent score and moved owner in review evidence; a move carries its existing marker, not a new exemption. [RUF100](https://docs.astral.sh/ruff/rules/unused-noqa/) rejects unused markers once scores reach 15 or below; the author removes them in that same PR. Compare unignored parent/candidate scores; reject any baseline increase, new violation or new exemption.

Every L row applies the standard's guards, tables, `match` where appropriate, keyword-only options and precise types only within its outcome. Plain functions remain the default; classes own real mutable state/resource lifetimes, not static namespaces or single-implementation hierarchies. No extra allocation/copy/hash is justified by cleaner syntax. Keep async/cleanup scopes and the distinct media/domain state machines.

Cover all **15 functions above 25** with cleanup, not deferral. Assess and clean the **14 at 16–25** where splitting improves reading; mostly retain the **21 at 11–15**. Use the parent measurement to assign every baseline function to its L row: CLI/rendering L2–L4; playback L5–L6; aggregation/packages/fetch L7–L9; provider sync/collectors/login and status L10; notice parsing L11; material layout/publication/adoption/receipts L12–L14; browser profiling/lease/SSO/protocol/session L15–L18. At G, explicitly assign any baseline function outside those families to the L row owning its workflow; L18 checks the inventory is exhausted, not a bucket for deferred cleanup. Each L row records before/after scores and removed markers. Long ordered LMS or filesystem-safety sequences may retain a marker only with a specific dependency/cleanup reason in the standard's existing comment style; extract safe pure steps first and state why further splitting would obscure the protocol. No blanket exemption for a whole module.

The measured **299 `dict[str, Any]` lines** are an inventory, not a mechanical replacement quota. In each L row replace fixed internal boundary values with frozen/slotted dataclasses, workflow accumulators with slotted mutable records, and genuinely shared closed sets with `StrEnum`; retain positional results as `NamedTuple` where appropriate. JSON stays dict-shaped, shared validated JSON views use owner-defined `TypedDict`, and raw JSON/duck-typed browser handles retain justified `Any`. Convert once at the owner edge with explicit keys, insertion order and omission conditions; never `asdict`/`vars`. Preserve validators' plain-string/type behavior and test enum/string boundaries. Introduce the command result record in P3/P4 only as necessary for the ownership seam; broader shape/enum/idiom changes belong to L3/L4, media enums to L5/L6 and domain enums to L10. Migrate every affected consumer within that PR.

### Review and merge process

Keep PRs small and separate. Each structural P row and lint G receives one correctness/equivalence review. Every logic L row requires both that correctness/equivalence review and an independent code-quality review against the binding standard. Both must pass; an unavailable reviewer is a blocker, not an implicit approval. The designated reviewers and merger are identified in the execution handoff, not encoded as infrastructure names in public documents.

Once CI, the immediate-parent offline before/after equivalence gate and required reviews pass, the designated merger merges that PR without asking again and reports the result. Stop the line at the first test, equivalence, review or merge failure; do not begin or merge the next row until corrected and rechecked. This is the process for this roadmap, not a change to unrelated repository policy. #95 and configured speed keep their own behavior-change acceptance; never claim neutral equivalence for their intentional changes.

### PR-specific cutover details

**P1 is the first neutral PR**, after #95's separate outcome. It removes a three-consumer drift risk without changing playback. G immediately follows P1 as its own lint-only PR, then L1; this is the sole gate insertion between a structural row and its logic row. Do not combine unrelated dead-code/helper cleanup with P1.

**P3/P4:** Before adding local command modules, P3 replaces both import-time discovery callers (`cli.py:49`, `presentation.py:14-16`) with explicit bindings for the existing assignments/materials/notices modules assembled by composition. Presentation receives that renderer map as input in P3, not P4. Remove directory scanning, its exception/validation machinery and obsolete discovery-shape tests in P3; remove unused command sync hooks after reference audit, retaining real provider sync entry points where used. New local modules therefore never pass through the old five-export validator and need no fake sync hook. P3 uses the new table for moved local families while the remaining coursework dispatch bodies stay intact; explicit domain bindings preserve their current parser/render/capability behavior. No alias or duplicate moved implementation is kept. P4 completes dispatch/metadata ownership for those families and removes the interim branch split.

P4 migrates the remaining command-specific renderers out of presentation, including recursive multi-domain sync output. Shared cache/error formatting stays in presentation, with explicit command metadata for cache/error advice. Preserve doctor error suppression, status missing-all behavior and materials picker output. Move original bodies first; do not optimize sorting, validation or refresh. P4 shares fetch ID helpers without altering first-seen order, untrimmed IDs, duplicate-catalog detection or validation-before-session precedence. Remove `_not_implemented` only if references/dynamic callsites are absent at that point; do not remove a documented error vocabulary solely for tidiness.

**P5/P6:** P5 is independently complete for YouTube while preserving the existing video implementation in preview; P6 finishes the second media owner. Both preserve 1x-only YouTube behavior; neither implements the speed feature. Preview's `finally` block and diagnostic/error timing must survive byte/action comparisons. `_mark_catalog_completion` stays with queue publication. Rehome tests' monkeypatches to the actual owner, not compatibility aliases. No new media class or inheritance is needed.

**P7/P9:** CNU operations are assembled once; do not make core call composition as a hidden service locator. P7 covers sync/course discovery only; command-module discovery has already been removed in P3. P9 migrates the remaining direct imports, including notice error-type checks and login-form expiry probes; translate those with supplied provider predicates/error adapters, not by teaching core a CNU selector or importing its exception class. Keep single-item and batch fetch distinctions. Existing standalone provider sync entry points remain real functions if used.

**P8:** Preserve current resource IDs, manifest keys, hardcoded public provenance value and validated policy decisions. Move provenance ownership to the CNU-supplied resolver/binding while retaining the exact `cnu` value; do not add config-driven provenance or a provider option. Resolve resources lazily at the existing traversal points after staging creation; do not eagerly fetch a batch, reorder transfers or change failure/cleanup order. Keep temporary-file ownership explicit: resolver reads/verifies/unlinks fetched temporary files as before; package builder owns staging cleanup/publication. Neutral type/policy modules have no dependency on provider code. Do not migrate transfer origins, selected-file routes or UI binding into neutral policy. No extra copies of resource bytes or extra per-resource hashing beyond existing work are justified by extraction.

**P10:** `domains.py` is metadata, not a plugin system. Existing order is lectures, assignments, notices, materials. Catalog validation reads supported domain names from that owner, retaining lecture/non-lecture schema differences. Headless flags remain operation-specific; unsupported play stays unsupported. Each CNU section module supplies current accepted paths, menu and collection function. Shared navigation verifies the current course before delegation; each function retains its existing capture lifecycle. A new domain gets one section entry plus its actual collector and command; no silent new status projection. P10 also adds the cheap import-direction gate described below, now that all intended core-to-provider edges have been removed.

**P11:** Move `parse_board_rows`, `_read_state`, `parse_notice_rows`, `parse_legacy_notice_text`, `_board_response_items`, `_board_item` and their actual identity/date/number/role dependencies. `_todo_response_coverage` stays with to-do completeness, JavaScript/captures/navigation stay in notices, and shared native-ID validation imports the parser owner. No legacy removal, identity redesign or capture unification.

**P12–P14:** Keep pinned-directory descriptors, inode verification, no-clobber publication and their cleanup in one owner; only the complete safety unit moves in P12. Download layout remains separate from publication policy. P13/P14 migrate every adoption/receipt caller and monkeypatch, without aliases. Real isolated filesystem scenarios must exercise replacement races and failure paths; fakes alone cannot prove inode/no-clobber behavior.

**P15–P18:** Start only on the #95-integrated parent and preserve its launch flags byte-for-byte. Move profiling/context and document timing together without changing event ownership or recording. Lease release, SSO owned-page cleanup, protocol attach/detach and session lifetime remain separate scopes with the original nesting/exception precedence. `browser.py` keeps `open_session`; L18 extracts only safe pure session steps and documents any retained ordered sequence. Protocol helpers never become an alternate session factory.

## 4. Offline equivalence gate for every implementation PR

The [CLI](../../contracts/cli.md#response-envelope-and-exit-codes) and domain contracts are the authority. Tests cannot bless a changed contract. This gate applies equally to structural and logic-cleanup PRs. No live LMS run is planned for neutral changes; if a move or cleanup changes interactions or cannot establish neutrality, stop and separate that behavior change under the existing live-run protocol rather than quietly marking it not applicable.

1. Capture the immediate parent's behavior before editing. Run its complete suite with locked dependencies, then the same candidate suite. Full-suite local synthetic Chromium tests are required during implementation; they are not live LMS tests. This planning session runs neither suite nor browser.
2. Run actual `cli.main(argv)` in separate isolated parent/candidate processes/import trees, using identical locked dependencies and a reset synthetic config/data root at the same absolute location. Reuse existing fake page/frame/locator and `VirtualClock` conventions (`tests/test_play.py`, `tests/test_replay_cli.py`, `tests/test_sync_dispatch.py`, fetch tests). Patch only external boundaries: credential/backend probes, browser availability, deterministic time/platform/TTY/input and entropy/name generation. A session replacement is allowed only when session ownership is outside the changed path. In P15–P18/L15–L18 run the actual `open_session` and affected profiling/timeline, lease, SSO and protocol owners, replacing only the underlying Playwright/backend with synthetic objects; reuse the real-lifecycle/fake-backend pattern in `tests/test_browser.py`. Do not stub any changed owner, command, selector, renderer, parser, aggregator, player handler, package resolver or catalog publisher with canned results. Apply the same rule to P12–P14/L12–L14: exercise actual material owners over isolated real filesystems, not a substitute publisher.
3. Freeze envelope/cache/retrieval time, virtual elapsed time, platform/Python reporting, version, terminal width and stdin/stdout TTY. Reset identical deterministic UUID streams for each parent/candidate case: every publication receives a distinct value, but both processes receive the same sequence. This controls catalog `generation_id` and its selection-snapshot hashes without deleting those fields. Control secrets/temp-name entropy likewise wherever temporary names enter paths or fake action traces, retaining uniqueness and real file creation/publication/cleanup. Compare entropy/name consumption counts too, so an extra call cannot disappear behind fixed values. Use scripted inputs without real credentials or installer/helper execution. Compare stdout and stderr UTF-8 bytes and return/exit code exactly, plus produced catalog, selection, receipt and package bytes. Do not normalize keys/arrays, round elapsed values, delete timestamps/IDs or mask paths to hide a difference. All fake hosts/IDs are synthetic; hosts use `.invalid`.
4. Compare ordered fake actions as well as results: session/lock open/close, auth, menu navigation, capture registration/removal, preview open/close, reads/clicks, callback events, transfer disposal/unlink and publication. Model independent state transitions and virtual deadlines, not fake methods that echo the desired result. Use baseline-normalized action names shared by both revisions, not internal module/function names.
5. Common corpus: root and every subcommand help, version, invalid/missing/abbreviated arguments; JSON/environment/TTY precedence; config/auth/setup success/error/prompt paths without real side effects; doctor accepted/invalid speeds and diagnostics; all four lists with empty/stale/filtered records; status missing/partial catalogs; sync success/failure and subsets; refresh old-cache success/failure; assignment/notice single/multi fetch, unknown later ID, duplicate ID/catalog match, item/session failure; materials saved/reused/adopted/skipped-existing; replay consent and JSON no-prompt; player blocked/start/stall/end/row-unverified/interrupt. Run JSON and human forms at narrow and normal widths, and exercise status/exit 0, 1, 2, 75 and interruption semantics. Add each PR's cases from the migration table.
6. Parent capture is the independent baseline, never a snapshot regenerated from the candidate. Deliberately mutate a disposable comparison input field and confirm the comparator rejects it. Record case counts, mismatch count, status coverage and action/state equality, not raw outputs. Any unexplained difference blocks the PR; do not weaken snapshots or the comparator. Fake equivalence proves the covered paths, not live LMS correctness.
7. Migrate existing behavior tests/imports. Keep new permanent tests only for uncovered consumer-visible boundaries, transitions and errors. Delete obsolete source-shape/wiring/internal-discovery tests rather than re-pinning implementation details. Use disposable CLI scenarios for comparison, not a large new snapshot framework. Keep generated artifacts out of tracked files and remove only explicitly designated disposable artifacts.
8. Run focused existing tests, full suite, Ruff and docs parity once the implementation is complete. Required Chromium coverage cannot be replaced by a silently skipped suite. Update only documentation made stale by the structural change, with contracts/user guides intentionally unchanged if behavior is identical. No format sweep, changelog fragment or release/version edit.

Implementation commands (not executed by this plan):

```sh
# Select affected existing suites for each PR; always also run the complete suite
CAMPUSCTL_REQUIRE_CHROMIUM=1 uv run --locked pytest
uv run --locked ruff check .
uv run --locked python scripts/check_docs_parity.py
```

Focused families: P1/L1 foundation/CLI-output/play/replay; P2/L2 presentation/CLI-output/selection/play-progress; P3/L3 config-init/guided-setup/setup/status/foundation; P4/L4 command-discovery/list/selection/fetch/CLI-output; P5–P6/L5–L6 play/replay/play-progress/play-diagnostics; P7/L7 sync-dispatch/sync-all/course-selection/profiling; P8/L8 source-package/request-policy/attachment-transfer/assignment-detail/notice-detail; P9/L9 fetch/materials-commands/material-files; P10/L10 sync-all/domain-catalog/catalog-view/browser-options/status plus affected provider collector/login tests; P11/L11 CNU-notices/notice-detail/notice-fetch; P12–P14/L12–L14 material-files/materials-commands with isolated real filesystem races; P15–P18/L15–L18 browser/profiling/lock and synthetic session/protocol failures. G runs lint, full suite and the public CLI comparison without browser/LMS policy changes. Existing filenames use the repository's `tests/test_*.py` convention; update imports/patch sites with the move, not expected output to permit different behavior.

## 5. Cheap rules after migration

In P10, add only these short rules to `CONTRIBUTING.md` (constitution already covers the important safety/PR policies; do not duplicate it):

- Put command policy in its command family, player-specific behavior in its media handler and CNU page behavior in its provider module; register explicitly at the composition boundary.
- Core never imports concrete providers; commands receive bound operations; leaf UI helpers never import commands; media handlers never import queue/preview. Move all callers with an owner change; leave no alias layer.
- Structural and logic-cleanup changes preserve complete-suite behavior and exact offline CLI/state/action equivalence against their immediate parent; do not update expected outputs to accept a neutral difference.

Add a small standard-library AST import check to existing CI, not a new linter dependency. Check absolute and relative imports, including imports inside functions and literal dynamic imports; forbid concrete-provider and reverse UI/player edges, and extend the same check in P12–P18 for the new material/browser owner directions. Report file/line/edge. Composition and provider modules are explicit allowed owners, not a broad ignore list. Prohibit nonliteral dynamic campusctl imports rather than pretending they were checked. Test the checker with tiny synthetic allowed/forbidden snippets and relative imports. The early G complexity gate remains active throughout: no new violations, no baseline increases, and unused per-function markers removed. No function-length limits, import sorting sweep, universal metrics or new CI service.

## 6. Integration with autoplay #95 and configured YouTube speed

#95 lands **first as its own PR**, before P1 and before any browser split. Its separately held autoplay change touches browser launch arguments, browser tests, configuration guides, the CLI contract and its own `95-youtube-autoplay-policy` fragment. Do not copy, stage or cherry-pick another checkout's uncommitted changes into this planning work. Browser splitting starts only on top of the merged #95 result, retaining its launch policy; #95 does not wait for restructuring.

The separate speed planning commit is `1368cc5`, specification `120-youtube-speed/spec.md` in that branch. It is planned behavior, not implemented rate support. Its present mechanism section keeps helpers inside the old `player.py`; that placement must be revised after structure. Preserve its official-UI-only policy, owner-evidence/approval requirements, rate precedence, bounded menu/verification behavior, observed-rate checks and no forced autoplay/rate fallback.

Required default merge order: **#95 → P1 → G → L1 → P2 → L2 → P3 → L3 → P4 → L4 → P5 → L5 → P6 → L6 → YouTube speed → P7 → L7 → P8 → L8 → P9 → L9 → P10 → L10 → P11 → L11 → P12 → L12 → P13 → L13 → P14 → L14 → P15 → L15 → P16 → L16 → P17 → L17 → P18 → L18**. The neutral prerequisites for speed end at L6; P7–P18 and their cleanup remain required roadmap work, not speed prerequisites or optional/deferred outcomes. Keep one active outcome at a time, with the first failure stopping the line.

Adjust the speed plan on the P6/L6-integrated baseline, not by transplanting its old line numbers:

- Put YouTube menu interaction, native-startup/rate monitoring and removal of its local 1x restrictions in `providers/cnu/media/youtube.py`. Remove its replay-queue restriction supplied through the media table; leave queue traversal, official row verification and catalog publication alone.
- Reuse P1's `config.SUPPORTED_SPEEDS`; speed needs no second CLI/config speed definition. `commands/lectures.py` forwards the same resolved override/default inputs without broadening accepted rates or changing consent.
- A genuinely identical rate-verification helper may live in `providers/cnu/media/rate.py` in the speed feature, once its intended changed predicate has feature tests. Do not pre-introduce it by altering Panopto behavior in P5/P6. Keep distinct state readers/startup loops and preserve the speed plan's Panopto regression coverage.
- Media-neutral preview lifetime stays in `player_preview.py`; media-specific setup/monitoring stays in the handler. Map old tests' patch sites to real new owners and retain the same consumer-visible assertions. Remove obsolete 1x expectations only in the behavior-changing speed PR.
- Retain speed's documentation/contract/usage/configuration/consumer-instruction updates and its separate feature fragment. Merge overlapping #95 CLI/configuration paragraphs semantically, preserving both launch and speed statements; never edit #95's fragment for speed.
- Keep both 120 and 130 index entries when integrated. Do not add a broken 120 link to this branch before that specification is present. Rebase each comparison corpus onto the immediate integrated parent: pre-speed and post-speed are different intentional baselines.

The sequence is autoplay, small gated foundations, command ownership/cleanup, media ownership/cleanup, then speed, followed by required provider/domain, material-safety and browser ownership/cleanup. Later neutral comparisons use the integrated behavior-changing parent; they never erase #95 or speed to recreate an older baseline.

## Planning acceptance

The deliverable is this revised whole-package map, binding cleanup standard, complete PR order and gates, plus a local planning commit. Keep the existing specification index entry unchanged. Record the review outcome and designated execution roles in the final handoff; implementation and equivalence remain future acceptance gates.

Planning verification is limited to source/contract inspection, documentation/link checks, public-content scanning and review. No implementation/full-suite/offline-output/live verification is claimed as completed here.
