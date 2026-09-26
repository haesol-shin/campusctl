# PR1 (merged) / PR1.1 — Foundation technical design

Implements [foundation requirements](spec.md) under the [constitution](../constitution.md). Evidence cited as `plan:` refers to the owner-held sanitized LMS source plan dated 2026-09-25. PR1 is merged: `src/campusctl/providers/cnu/ui_policy.py:63-163` has exact-route, deny-by-default policy but no suppressions, resource-type asset permission, selected-file matcher, or installed route interceptor. PR1.1 extends that file privately before PR2/3/4; no operation is published by this follow-up.

## CLI, output and errors (FR-010–FR-012, FR-015)

PR1 adds **no enabled operation**. Existing `sync [--only lectures]`, `courses list`, `lectures list`, `lectures play` retain behavior. Parser reserves `sync --headless` as dormant: reject it for lectures or unregistered domains with usage/2. No headless flag for playback. Future PR2/3/4 register `sync --only assignments|notices|materials` and domain commands only when implemented/reviewed; local list remains cache-only. Existing CLI rejects unsupported domains before config/catalog access (`src/campusctl/cli.py:89-111,437-449`; plan: lines 23–35, 168–177).

**Full unchanged JSON example**, `campusctl --version --json` (`src/campusctl/envelope.py:9-47`; `docs/contracts/cli.md:112-129`):

```json
{"schema_version":1,"tool":"campusctl","tool_version":"<released-version>","status":"ok","result":{"version":"<released-version>"},"errors":[],"generated_at":"2026-01-02T03:04:05Z"}
```

PR1 emits no new result. Future domain list wraps `{"cache":{"generated_at":"<UTC RFC3339>","path_present":true,"enrollment_state":"known","failed_courses":[]},"assignments":[]}` in that envelope (plan: lines 44–72). **Existing human output sample** for empty lecture list begins:

```text
0 lectures

Nothing unfinished.
To include completed or recorded lectures: campusctl lectures list --all
To refresh: campusctl sync
```

Actual wrapping is width-dependent (`src/campusctl/presentation.py:380-404`); this is illustrative, not a new PR1 renderer. Human mode is TTY default; `--json` overrides (`src/campusctl/cli.py:300-308`).

No new domain errors are emitted in PR1. Future modules use `CampusError(code, safe_message, remediation, status)` and existing `{code,message,remediation}`. `ok`/0, `partial` or `error`/1, `user-action` or usage/2, `busy`/75 (`src/campusctl/envelope.py:11-47`; `src/campusctl/lock.py:36-81`). Reserve `catalog-missing`, `catalog-invalid`, `catalog-schema-unsupported`, `catalog-write-failed`, `course-discovery-failed`, `course-sync-failed`, `item-identity-missing`, `entity-unknown`, `unsupported-media-type`, `policy-blocked`, `file-too-large`, `download-failed`, `output-path-conflict`, `headless-unavailable`. Policy block/transfer failure are `error`/1; unsupported media, oversize, conflict, unverified headless are `user-action`/2. A `headless-unavailable` remediation directs a visible browser or separately configured browser endpoint. Never expose raw exception text, URL/query, cookies, credentials or file bytes (`src/campusctl/cli.py:541-546`; plan: lines 36–42).

## IDs, catalog, stale storage and lock (FR-001–FR-007)

PR1 owns `src/campusctl/identity.py` with these exact pure functions (plan: lines 61–78, 163–164):

```python
def assignment_entity_id(course_id: str, task_id: str) -> str: ...
# cnu_assignment:<course_id>:<task_id>
def notice_entity_id(course_id: str, displayed_date_time: str, number: str) -> str: ...
# cnu_notice:<course_id>:<displayed-date-time>:<number>
def material_entity_id(course_id: str, file_id: str) -> str: ...


# cnu_lms_material:<course_id>:<file_id>
```

Reject empty components; before joining an ID, encode each component reversibly by replacing `%` with `%25` and then `:` with `%3A`. Components without `%` or `:` remain byte-identical, and the notice row separately retains exactly `<course-label>_<displayed-date-time>_<number>` as its `legacy_key` **without encoding**, pinned against legacy notifier parser expectations; do not normalize displayed date/number or replace the legacy ledger identity with the new ID. Preserve `task_id` and `file_id` separately. Same native ID in two courses differs; duplicate IDs within one course are rejected. Actual provider delimiter characters remain unverified.

PR1 owns `src/campusctl/domain_catalog.py`:

```python
from typing import Any, Literal
from pathlib import Path

Domain = Literal["assignments", "notices", "materials"]


def domain_catalog_path(domain: Domain, root: Path | None = None) -> Path: ...
def read_domain_catalog(domain: Domain, path: Path | None = None) -> dict[str, Any]: ...
def write_domain_catalog(domain: Domain, value: dict[str, Any], path: Path | None = None) -> Path: ...
def merge_domain_catalog(
    domain: Domain,
    previous: dict[str, Any] | None,
    roster: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    *,
    successful_course_ids: set[str],
    failed_courses: list[dict[str, str]],
    selected_course_id: str | None = None,
) -> dict[str, Any]: ...
def mark_enrollment_unknown(domain: Domain, root: Path) -> None: ...
```

Each domain has a distinct `<data-dir>/catalog/<domain>.json` alongside unchanged `lectures.json` (`src/campusctl/catalog.py:14-18,27-55`; plan: lines 16, 150). **Full sample domain catalog**; switch only the domain key/row schema for notices/materials:

```json
{"schema_version":1,"generated_at":"2026-01-02T03:04:05Z","enrollment_state":"known","courses":[{"course_id":"C1","label":"Course 1","class_no":null}],"failed_courses":[{"course_id":"C2","label":"Course 2","reason":"course-sync-failed"}],"assignments":[{"entity_id":"cnu_assignment:C1:T1","task_id":"T1","course":{"id":"C1","label":"Course 1"},"kind":"assignment","title":"Brief","due_date":null,"is_submitted":false}]}
```

`courses` use existing normalized `{course_id,label,class_no}` (`src/campusctl/providers/cnu/courses.py:29-41`); foundation validates metadata/list/version, domain PR validates its row shape. Failure `reason` values: `course-sync-failed`, `item-identity-missing`, `removal-deferred`; never raw exception text. Reject duplicate course/row IDs. `generated_at` is UTC RFC3339 merge time, not response time. Full sync with complete roster sets `known`, replaces complete/addressable course rows including empty, retains failed/unaddressable rows with markers, and if partial retains absent old courses with `removal-deferred`; only full success drops unenrolled courses. Filtered sync changes its target alone and preserves other markers/enrollment state. Discovery failure calls `mark_enrollment_unknown` on an **existing** catalog, preserves rows/courses/markers/timestamp and returns `course-discovery-failed`/error 1; absent catalog stays absent. Lists expose all retained rows and `cache:{generated_at,path_present,enrollment_state,failed_courses}`; unknown enrollment makes all rows stale, a failed marker makes that course stale. A missing catalog returns `catalog-missing`/user-action 2 with `sync --only <domain>` remediation (plan: lines 44–49, 162, 180–186).

`write_domain_catalog` follows existing `ensure_private_dir`, sibling `mkstemp`, flush/fsync, `os.replace`, directory fsync and temp cleanup (`src/campusctl/catalog.py:58-115`). Readers take no lock. Network operations use the existing `open_session` nonblocking session lock for their whole operation (default `<data-dir>/session.lock`, configurable `browser.lock_path`), no second catalog lock or legacy notifier browser lock (`src/campusctl/browser.py:286-300`; `src/campusctl/lock.py:36-81`; `docs/contracts/cli.md:92-98`). Pure merge never writes; a course is committed only after full addressability.

## 2026-09-26 reviewed side-request design delta

The guard checks redirected requests and Range first, then parses the URL and matches exact, operation-scoped reviewed suppressions before enforcing the policy-origin gate. `suppress` retains `{name,origin,path_template,operation,methods,reason}`; a reviewed suppress origin need not belong to `origins`. The fixed suppress registry includes LMS GET `/js/common/panoptoSaml-{hash}.js` (script), LMS GET `/upload/dunetadmin/college/{hash}.png` (image only), LMS GET `/assets/images/favicon-{hash}.ico` (other only), and POST `/v1/events` (fetch) at the observed external origin. Existing Panopto entries remain. No query, redirect, or Range is accepted for a named suppression. If no named pin matches, unknown-origin GET passive assets (`script`, `stylesheet`, `font`, `image`, `media`) are aborted as `third-party-asset`; other unknown-origin traffic remains fatal. The interceptor counts the matched reason without reconstructing a suppress entry with `next(...)`; no URL or real ID enters diagnostics. Owner decision 3 permits only an exact LMS POST `/api/v1/week/getStdActivityStatus` XHR, scoped to each reviewed operation, to continue as a read-only route; aborting this request triggers a blocking `/std/lecture` server-communication modal. Keep `_is_logging_path` unchanged and deny all other logging paths unless separately suppressed.

The owner additionally reviewed the SSO popup observed during headless archive work: LMS `/std/myLecture` runs `panoptoSamlLogin_object`, which opens LMS `/SSOServiceLogin` in a popup; its top frame attempts the external document POST. The four non-playback operations abort that exact request, count `panopto-sso-popup`, and close the popup; lecture playback retains its original SSO behavior. The request interceptor checks popup frame URL and roster opener when Playwright exposes them; if frame context is unavailable it uses the exact five-field pin.

## Course navigation and request policy (FR-008–FR-009, FR-013)

PR1 owns `src/campusctl/providers/cnu/course_context.py`:

```python
async def enter_course_section(
    page: Any, config: dict[str, Any], course_id: str, section: Literal["course", "task", "archive"]
) -> None: ...
```

PR1.1 makes `enter_course_section(page, config, course_id, section)` **navigation-only on an already-authenticated page**. Remove its internal `ensure_logged_in` call (`src/campusctl/providers/cnu/course_context.py:21-38`), with no new flag: domains authenticate once before installing the guard, then under the guard navigate to pinned GET `/std/myLecture` for the roster and each course re-entry, activate exact `[data-act="moveLecture"][data-courseid=<CSS-safe quoted id>]`, wait for the ordinary menu, and click the observed section link (`course_context.py:39-50`). PR1.1 updates existing fake-page tests accordingly. Reviewed evidence pins POST `/api/v1/course/addSessionCourseInfo` from row entry (decision file lines 44–47). Never forge that call or navigate directly to `/std/task`/`/std/archive`. Global notice to-do follows its own reviewed route.

PR1.1 extends PR1-owned `src/campusctl/providers/cnu/ui_policy.py`. Current `UiRequestPolicy.from_reviewed_config` validates origins and exact `{origin,path,operation,methods}` routes (`src/campusctl/providers/cnu/ui_policy.py:63-121`); `guard_ui_request` raises `UiRequestDenied("logging"|"range"|"origin"|"route"|"method"|"media"|"redirect")`, with safe `policy-blocked`/error/1 (`ui_policy.py:13-60,124-163`). Existing `_request_parts` currently discards the query/fragment (`ui_policy.py:181-198`), so PR1.1 must check both before allowing a request; do not treat a token-bearing query as safe just because its path is pinned.

**Reviewed policy keys (FR-009, FR-018–FR-020).** Domain-owned `CAPABILITY["policy"]` is the single reviewed config passed to `UiRequestPolicy.from_reviewed_config`, never `load_config()` or page content. Existing `approved`, `read_only_evidence`, `origins`, `routes`, `allowed_media`, `max_bytes` gain `suppress`, `static_asset_origins`, `static_resource_types`, `selected_file_routes` (empty defaults). Malformed entries disable approval. Data routes have `{origin,path,operation,methods:[...],query?:{<name>:<validator>}}`: absent `query` means no query allowed; each listed query name is optional-but-allowed, at most once, and must pass its kind. The sole exceptional logging-token data route requires `logging_token_reviewed:true`, `resource_type:"xhr"`, exact LMS origin and POST `/api/v1/week/getStdActivityStatus`, no query, and an operation in `assignments.sync`, `materials.sync`, `materials.download`, `notices.sync`; validation pins this entire tuple in code. The guard matches origin, path, operation, method and resource type before the generic logging denial; absent or mismatched exemption remains denied as logging. The request was observed from `/std/lecture`; route schema has no Referer constraint. Kinds: `_:"cachebuster"` (1–20 ASCII digits on the two messages properties routes), `e:"encrypted"` (1–4096 ASCII base64/url-safe characters on `getAttachFileList`), `curPage:"page"` (ASCII digits or `undefined` on `taskView` and `noticeDetail`), and `no:"board-item-id"` (anchored `TB_L_BOARDITEM[0-9]+` on notices.fetch GET `/std/noticeDetail` only). Pin only observed names for each operation/path. Deny unlisted/repeated data query names, invalid values and fragments; never log URLs or query values. `static_asset_origins` is a subset of `origins`, fixed to L for current domains; only GET of Playwright `script|stylesheet|font|image` qualifies. Approved-origin static assets may carry queries; XHR/fetch/document still need exact data routes. Range and redirects fail before suppression. Reviewed suppressions precede the origin gate; unknown-origin passive GET assets are aborted nonfatally, while all other unknown-origin traffic fails. Logging/media denial remains unchanged for approved-origin requests outside exact suppress pins and the one reviewed data route.

**Named suppression data (FR-018).** Each `suppress[]` entry is `{name,origin,path_template,operation,methods,reason}`. The origin is L except for the two exact reviewed external origins: telemetry `http://0.0.0.0:3000` and Panopto SSO `https://cnu.ap.panopto.com`, neither of which is added to `origins`. The fixed registry below restricts methods, paths and safe reasons per operation:

| `name` | Method + anchored `path_template` | `reason` | Resource type |
| --- | --- | --- | --- |
| `panopto-script` | GET `/js/common/panopto-{hash}.js` | `media-integration` | Existing reviewed suppression |
| `panopto-saml-script` | GET `/js/common/panoptoSaml-{hash}.js` | `media-integration` | script |
| `panopto-sso-popup` | POST `https://cnu.ap.panopto.com/Panopto/Pages/Auth/Login.aspx` | `panopto-sso-popup` | document; only assignments.sync, notices.sync, materials.sync, materials.download |
| `course-roster-image` | GET `/upload/dunetadmin/college/{hash}.png` | `course-roster-image` | image |
| `favicon-icon` | GET `/assets/images/favicon-{hash}.ico` | `favicon` | other |
| `external-telemetry` | POST `http://0.0.0.0:3000/v1/events` | `telemetry` | fetch |
| `panopto-disconnection-log` | POST `/api/v1/panopto/addInternetDisconnectionLog` | `logging` | Existing reviewed suppression |
| `panopto-connectivity-check` | GET `/api/v1/panopto/checkInternetConnection` | `logging` | Existing reviewed suppression |

`{hash}` is one nonempty `[A-Za-z0-9_-]+` component, matched as a full path without query or fragment. A redirect or Range is denied before any suppression. Reject duplicate names, broad globs, unreviewed origins/methods/reasons and overlap with data routes. `_is_logging_path` remains unchanged; the narrowly reviewed activity-status data route continues rather than being aborted.

```python
def match_selected_file_path(path_template: str, path: str) -> dict[str, str] | None: ...
def bind_selected_file_request(
    policy: UiRequestPolicy,
    *,
    selected_file_id: str,
    resolved_url: str,
    source: Literal["official-control", "fileDownload-response"],
    operation: str = "materials.download",
) -> SelectedFileRequest: ...
# SelectedFileRequest: frozen(selected_file_id, origin, path, source, operation)
def guard_ui_request(
    policy: UiRequestPolicy,
    url: str,
    method: str,
    headers: Mapping[str, str],
    *,
    operation: str,
    resource_type: str,
    redirected_from: str | None = None,
    selected_file: SelectedFileRequest | None = None,
    selected_file_id: str | None = None,
) -> Literal["allow", "suppress"]: ...
```

`selected_file_routes[]` entries are `{origin,path_template,operation,methods}`. For `materials.download` pin **only** C GET `/upload/{storage-id}/{encoded-filename}` and L GET `/file/{term}/{course}/board/{board-manager}/{board-item}/{stored-filename}` (L = `https://dcs-learning.cnu.ac.kr`, C = `https://dcs-lcms.cnu.ac.kr`; decision file lines 37–60). Compile templates as anchored, segment-bounded matches: placeholders capture one **nonempty** segment; decode each exactly once, reject decoded `/` or `\\`, `.`/`..`, empty segment, malformed `%` escape, residual `%HH` (double encoding), and path normalization or prefix matching. No URL query/fragment, userinfo, redirect, or case-variant Range on either selected-file URL; reject unexpected query/fragment on every ordinary route. `bind_selected_file_request` validates selected ID, claimed source, reviewed origin/template, and exact origin/path, rejecting any other URL; the provider MUST separately prove the official control or `fileDownload` response belongs to the chosen row (a pure URL function cannot authenticate a DOM control). Caller supplies that row's file ID independently as `selected_file_id`; guard compares it with binding ID and matches only the bound path, not another file matching the template. PR5 instead uses the attachment-native ID observed in the selected parent detail's official control; it pins its own selected-file routes only after evidence. Neither binder nor diagnostics persists a query/token. PR4's `providers/cnu/request_policy.py` and `attachment_transfer.py` own response MIME/signature/size and 200_000_000-byte bound; PR4 consumes rather than edits `ui_policy.py`.

**Shared installed interceptor (FR-021).** The same module exports:

```python
class UiRequestDiagnostics:
    suppressed_count: int  # starts at 0
    suppressed_reasons: dict[str, int]  # safe reviewed reason -> count; starts empty


def install_ui_request_interceptor(
    page_or_context: Any,
    policy: UiRequestPolicy,
    *,
    operation: str,
    diagnostics: UiRequestDiagnostics,
    selected_file: SelectedFileRequest | None = None,
    selected_file_id: str | None = None,
) -> UiRequestInterceptor: ...


# interceptor.raise_if_denied() -> None; await interceptor.close() removes its route
```

Create `UiRequestDiagnostics()` for each network operation. Run existing `ensure_logged_in(page, config, ...)` once **before** the interceptor: authentication is a trusted pre-operation flow, not per-operation pinned; `src/campusctl/providers/cnu/login.py:130-159` already handles its own login response. Then install on the operation's Playwright Page or BrowserContext before the first guarded roster/course/detail navigation, including reused CDP contexts (`src/campusctl/browser.py:337-351,375-394`). GET `/std/myLecture` for roster and course re-entry remains guarded and exactly pinned. The callback forwards URL, method, complete headers, resource type, and predecessor URL from `request.redirected_from`; `"suppress"` aborts and increments only safe count/reason, `"allow"` continues, other denials abort and retain the first safe `UiRequestDenied`. Enforce `interceptor.raise_if_denied()` after each navigation/async action and before catalog/package commit or successful result (Playwright may isolate callback exceptions); `await interceptor.close()` unregisters its own handler in `finally`, with no stray CDP handler. If authentication expires mid-operation, deny the redirect/login navigation (`redirect`/`route`), return `login-action-required` or an existing login error, **no** automatic re-login/retry within the guarded operation; user reruns. Domains attach safe diagnostics to their own results without changing the PR1.1 public envelope.

## Shared hooks, capability and FILE OWNERSHIP (FR-010–FR-012, FR-015)

| PR | Owned new files | Shared edit boundary |
| --- | --- | --- |
| PR1 | `identity.py`, `domain_catalog.py`, `providers/cnu/course_context.py`, `providers/cnu/ui_policy.py`, `commands/__init__.py`, sanitized `tests/fixtures/lms_sources/`, targeted tests | Merged private baseline: discovery/dispatch/capability composition in `cli.py`, renderer routing in `presentation.py`, internal headless in `browser.py`; released command inventory unchanged. |
| PR1.1 | `src/campusctl/providers/cnu/ui_policy.py`, `src/campusctl/providers/cnu/course_context.py`, `tests/test_cnu_ui_policy.py`, `tests/test_cnu_course_context.py`, own synthetic fixtures | Shared policy/interceptor and navigation-only course entry; no CLI, renderer, contract inventory, provider-domain or lecture playback edits. Lands before PR2/3/4. |
| PR2 | `commands/assignments.py`, `providers/cnu/assignments.py`, `docs/contracts/assignments.md`, own fixtures/tests | **No** PR1.1 shared files, `cli.py`, `presentation.py` or `docs/contracts/cli.md` edits; consume shared interceptor. |
| PR3 | `commands/notices.py`, `providers/cnu/notices.py`, `docs/contracts/notices.md`, own fixtures/tests | Same zero shared-file edits; consume shared interceptor. |
| PR4 | `commands/materials.py`, `providers/cnu/materials.py`, `providers/cnu/request_policy.py`, `providers/cnu/attachment_transfer.py`, `material_files.py`, `docs/contracts/materials.md`, own fixtures/tests | Same zero shared-file edits; consume PR1.1 selected-file binder/interceptor and own response-policy layer. |
| PR5 | Its source-package/detail modules plus sequential additions to PR2/3 command modules and PR4 guard/transfer interfaces | Extend `docs/contracts/assignments.md` and `notices.md` after fetch gate; no CLI/renderer/central-contract edit. |

PR1 added `src/campusctl/commands/__init__.py` with `discover_domain_modules() -> dict[str, ModuleType]`: sorted submodule imports skip helpers with no domain hooks and complete unapproved modules, while partial exports/malformed capabilities fail visibly. Domain modules export the existing `register`, `dispatch`, `sync`, `render`, and `CAPABILITY` hooks (`src/campusctl/commands/__init__.py:1-103`). PR1.1 does not change discovery or released `CAPABILITIES`; PR2/3/4 own only their independent new command modules, providers, tests and contracts.

```python
CAPABILITY: dict[str, Any]


def register(subparsers: argparse._SubParsersAction) -> None: ...
def dispatch(args: argparse.Namespace) -> tuple[dict[str, Any], CampusError | list[CampusError] | None]: ...
async def sync(
    config: dict[str, Any], root: Path, course_id: str | None, *, headless: bool = False
) -> tuple[dict[str, Any], list[CampusError]]: ...
def render(command: str, result: dict[str, Any], width: int) -> list[str]: ...
```

`register` adds domain/leaf `--json` with `default=argparse.SUPPRESS`, nested `EnvelopeArgumentParser` imported lazily from `campusctl.cli`, and `<domain>_command` dest. `build_parser` calls `module.register(commands)` for discovered modules in sorted order after built-ins. `_dispatch` routes discovered nonlecture sync via `asyncio.run(module.sync(load_config(), data_dir(), args.course, headless=args.headless))`; domain command via `.dispatch(args)`; unknown sync domain retains `unsupported-domain`. `_command_key` emits `sync.<domain>` or `<domain>.<leaf>` for internal human rendering. `presentation.py` derives `_DOMAIN_RENDERERS: dict[str, Callable[[str, dict[str, Any], int], list[str]]] = {name: module.render for name, module in discover_domain_modules().items()}` and routes those keys to the matching renderer, then applies central errors. This avoids lecture-only `_sync` showing wrong counts (`src/campusctl/presentation.py:507-523`). Every domain contributes **only its own new module file**; no CLI/renderer hunk, no second `SYNC_HANDLERS` map. PR1 adds a single line to `docs/contracts/cli.md` pointing readers to `docs/contracts/<domain>.md` for separately released domains; domain PRs create only their own contract files, never edit the shared inventory. Release integration serializes any CHANGELOG or version bump.

Compose public `CAPABILITIES` from `_DOMAIN_MODULES`: with no domains registered in PR1, value is **exactly** `{"sync":["lectures"],"lectures":["list","play"]}` (`src/campusctl/cli.py:32-33,144-228`). When a domain registers, `sync` automatically adds its name and the new domain key lists only its commands, `list(module.CAPABILITY["commands"])`; the reviewed request policy stays internal and is not advertised by `doctor`. Do not edit the shared `sync` list in PR2/3/4. New domain shape (example placeholders are **not** approvals):

```json
{"commands":["list"],"policy":{"approved":true,"read_only_evidence":"<reviewed-artifact-ref>","origins":["https://dcs-learning.cnu.ac.kr"],"routes":[{"origin":"https://dcs-learning.cnu.ac.kr","path":"/std/myLecture","operation":"assignments.sync","methods":["GET"]}],"suppress":[{"name":"panopto-script","origin":"https://dcs-learning.cnu.ac.kr","path_template":"/js/common/panopto-{hash}.js","operation":"assignments.sync","methods":["GET"],"reason":"media-integration"}],"static_asset_origins":["https://dcs-learning.cnu.ac.kr"],"static_resource_types":["script","stylesheet","font","image"],"selected_file_routes":[],"allowed_media":[],"max_bytes":null}}
```

This object is the module-internal `CAPABILITY`; discovery validates it, and `doctor` advertises only its `commands` list, so every public capability value, lecture or domain, is a list of command names. A provider-owned reviewed constant may be imported by its command module as `CAPABILITY[\"policy\"]` to avoid cycles/duplicate values; alternatively the command module passes its single reviewed policy to the provider. PR4 adds `download` to commands only with its separate type/size/effects gate, PR5 adds `fetch` after detail gate. Unreviewed complete modules use `approved:false` and are intentionally skipped; incomplete or malformed modules raise instead. `browser.open_session(config: dict[str, Any], *, data_dir: Path | None = None, headless: bool = False)` retains headed default, profile, lock and CDP semantics (`src/campusctl/browser.py:286-385`). Only reviewed non-playback domain calls may opt into local headless; skip local display check then launch local persistent Chromium `headless=True`. A CDP endpoint cannot be made headless by this flag; reject incompatible use. Until CNU compatibility is established, a domain request with `headless=True` returns `headless-unavailable`/2 before session or catalog mutation. Lecture play never passes true.

## Source-package and filename design fixtures (FR-013–FR-014)

PR1 pins future `package.json` v1 schema with `entity_id`, `kind`, `course`, sanitized `source_ref:{origin,page_path,provider_native_id}`, `retrieved_at`, `content_path:"content.md"`, `completeness:"complete"|"policy-filtered"`, `resources[]` and `omitted_resources[]`. Included resources carry full SHA-256, media type, original name, relative POSIX path and normalized source reference; omissions carry reason `external-origin|video|unknown-type` and make a published package `partial`. Resource ID is lowercase SHA-256 of canonical JSON `['file',course_id,provider_file_id]` or `['inline',entity_id,kind,one_based_reference_order]`, counting omitted references in traversal order. Normalize source origin to lowercase scheme/host/default port omitted, and parsed page path with dot segments resolved and uppercase percent escapes, no query/fragment/credentials or persisted signed URLs (plan: lines 80–90).

Canonical JSON is sorted keys, compact separators, UTF-8, `ensure_ascii=False`. Hash manifest with `retrieved_at` omitted (published manifest retains it), then `content.md`, then included resource POSIX paths lexically; each record is `U64BE(name-byte-length)||name-UTF8||U64BE(content-byte-length)||content`. Default private package location `<data-dir>/sources/<assignment|notice>/<SHA256(UTF8(full entity_id))>/<package-digest>/`; sibling-temp build, atomic publish, reuse identical bytes; explicit `--out` must be a nonexistent exact directory (plan: lines 92–125). PR1 fixtures only; PR5 owns publication.

PR4 implements PR1 safe-name fixture in `material_files.py`: NFC, basename after both slash styles, invalid/control chars replaced, trailing dots/spaces trimmed, empty/dot/Windows-device names become `resource`. Process page-reference order; NFC+casefold collision key **after** final truncation. Add `~<full resource_id>` or material `~<full content SHA-256>` before extension on collision. Keep at least one UTF-8 stem codepoint, trim extension then stem at codepoint boundaries to a **200-byte final component** including suffix. A remaining different-byte/symlink collision fails `output-path-conflict` without overwrite; identical bytes may be reused, never follow symlinks. Preserve material raw `display_name` separately and strip trailing `바로보기` only into `filename` before type/safe-name classification (plan: lines 78, 89–90, 124–127). No video bytes, transfer or package build in PR1.

## Fixture strategy and acceptance (FR-001–FR-015)

PR1 design data lives in `tests/fixtures/lms_sources/`: `policy.json` above, `identity.json` (native components + expected full/legacy IDs), `catalog_transitions.json` (full, partial, discovery failure, filtered, empty), `course_navigation.json` (sanitized selector/event ordering), `request_headers.json` (two otherwise-identical reviewed GET cases with `{}` versus `{'rAnGe':'bytes=0-1023'}`), `package_schema.json` (canonical manifest/resource IDs/omissions/digest), `safe_names.json` (NFC, casefold, traversal, device names and 200-byte suffix boundaries). These are synthetic design fixtures until owner supplies representative sanitized provider examples. Redact usernames/student IDs, cookies, credentials, signed query/fragment, raw body and video payload; retain only selector attributes and route method/path needed to test policy. Never capture live data during implementation.

Targeted tests: `tests/test_identity.py`, `tests/test_domain_catalog.py`, `tests/test_cnu_course_context.py`, `tests/test_cnu_ui_policy.py`, `tests/test_command_discovery.py`; existing `tests/test_sync.py`, `tests/test_cli_output.py`, `tests/test_list_commands.py`, `tests/test_presentation.py` defend lecture compatibility. Owner-held sanitized legacy notifier evidence supplies read-only expectations for notice-key parsing, modal/inline/post-detail file handling, assignment mapping and attachment safety; translate those into synthetic domain fixture tests without importing or editing the external notifier. PR1 acceptance: catalog isolation, merge staleness, authenticated navigation, policy denial, header/redirect handling and unchanged lecture behavior.

## Live verification gate (FR-009, FR-013, FR-015)

**No live LMS in implementation.** Owner-held sanitized LMS policy decisions dated 2026-09-25 pin operation routes, accepted notice-detail view accounting, attachment extension policy and 200_000_000-byte maximum; observed MIME/signature pairs cover PDF, PPTX and ZIP only. Other extensions require a known extension/signature table, not MIME alone. PR5 still gates representative notice attachment behavior, unobserved MIME pairs, to-do `read_yn` change and per-course coverage, source-package detail completeness, and headless compatibility check approved for after PR2–4. Never silently enable unsupported headless. If a proof is unavailable, leave that specific command unregistered/unadvertised; a busy lock never becomes a successful sync.
