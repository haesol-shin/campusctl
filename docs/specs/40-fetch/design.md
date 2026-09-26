# PR5 — selected LMS source packages: design

The [project constitution](../constitution.md) and [behavior specification](spec.md) govern this design. Owner-approved pins and sanitized observations permit implementation now; fixture proof is not publication proof [FR-001, FR-014–015; owner-held sanitized policy decision (2026-09-25, lines 8–35,41–72); owner-held sanitized observation report (2026-09-25, lines 61–75,103–133)]. PR1 already provides domain discovery, course navigation and exact-route `UiRequestPolicy` [`src/campusctl/commands/__init__.py:27–110`; `src/campusctl/providers/cnu/course_context.py:14–50`; `src/campusctl/providers/cnu/ui_policy.py:63–163`]; PR1.1 extends its policy/interceptor for reviewed suppression, static resources and selected files before PR5 implementation. PR2–4 supply list catalogs and official-file transfer; none of the current PR1 code supplies a fetch detail adapter.

## CLI and presentation [FR-002, FR-011–012, FR-015]

```console
campusctl [--headless|--headed] assignments fetch <entity-id> [--out <new-package-root>] [--json]
campusctl [--headless|--headed] notices fetch <entity-id> [--out <new-package-root>] [--json]
```

Exactly one full opaque `entity_id` from the matching domain list; check its catalog membership before browser startup, then independently bind opened detail to the selected ID **before** transferring bytes. Do not parse/reconstruct IDs in callers or match by title, row position or guessed URL. If a stale catalog row's detail no longer matches, safe `entity-unknown` and no package. `--out` names the exact nonexistent package directory; default is private content-addressed cache. Headed is default. `--headless` is a proposed opt-in only if independently proved compatible, otherwise reject it and omit it from the *published* command listing. `--json` is explicit for agents; current CLI selects human output on a TTY unless overridden [`src/campusctl/cli.py:300–308`; `docs/contracts/cli.md:22–26`; owner-held sanitized planning notes (2026-09-25, lines 23–36,76,80,124,176–177)].

v0.4.0 precedence: browser choice is global and resolved by 50/60, not a fetch-local parser flag. Global headless does not clear this operation's independent compatibility gate. Materials numbers/picker do not apply to fetch; full ID selects its course without adding `--course`. Implement command registration only after 50's integration lane hands over these shared domain files.

Full JSON success example; angle-bracket values illustrate fixture-bound names/approved origins, **not** claimed real LMS routes or published allowlist values [FR-006, FR-011–012]:

```json
{
  "schema_version": 1,
  "tool": "campusctl",
  "tool_version": "<released-version>",
  "status": "ok",
  "result": {
    "source_package": {
      "entity_id": "cnu_assignment:<course-id>:<task-id>",
      "completeness": "complete",
      "path": "/data/sources/assignment/<entity-key-hash>/<package-digest>",
      "manifest_path": "/data/sources/assignment/<entity-key-hash>/<package-digest>/package.json",
      "content_path": "/data/sources/assignment/<entity-key-hash>/<package-digest>/content.md",
      "resources": [
        {
          "resource_id": "<full-sha256-image-id>",
          "kind": "image",
          "path": "/data/sources/assignment/<entity-key-hash>/<package-digest>/images/diagram.png",
          "source_ref": {"origin": "<approved-origin>", "page_path": "<normalized-image-path>", "provider_native_id": null},
          "original_name": "diagram.png",
          "media_type": "image/png",
          "size_bytes": 123,
          "sha256": "<full-sha256-of-image-bytes>"
        },
        {
          "resource_id": "<full-sha256-file-id>",
          "kind": "attachment",
          "path": "/data/sources/assignment/<entity-key-hash>/<package-digest>/attachments/brief.pdf",
          "source_ref": {"origin": "<approved-origin>", "page_path": "<normalized-file-path>", "provider_native_id": "<file-id>"},
          "original_name": "brief.pdf",
          "media_type": "application/pdf",
          "size_bytes": 456,
          "provider_file_id": "<file-id>",
          "sha256": "<full-sha256-of-file-bytes>"
        }
      ],
      "omitted_resources": [],
      "provenance": {
        "provider": "cnu",
        "course_id": "<course-id>",
        "source_ref": {"origin": "<approved-origin>", "page_path": "<normalized-detail-path>", "provider_native_id": "<task-id>"},
        "retrieved_at": "2026-09-25T12:00:00Z"
      }
    }
  },
  "errors": [],
  "generated_at": "2026-09-25T12:00:00Z"
}
```

For notice fetch, the ID is the full `cnu_notice:<course-id>:<displayed-date-time>:<number>`; manifest kind is `notice` and `source_ref.provider_native_id` remains `null` unless verified by real fixture. `result.source_package` resource `path`s are absolute; manifest resource paths are relative POSIX [owner-held sanitized planning notes (2026-09-25, lines 76,80–92)]. Policy exclusion gives `status:"partial"`, exit 1, `completeness:"policy-filtered"`, `omitted_resources` in result and manifest, and safe `resource-omitted` error; transfer/response mismatch never publishes partial output [owner-held sanitized planning notes (2026-09-25, lines 36,89–92,124)].

Human example (policy-filtered case):

```text
Assignment source: cnu_assignment:<course-id>:<task-id>
Package: /data/sources/assignment/<entity-key-hash>/<package-digest>
Content: /data/sources/assignment/<entity-key-hash>/<package-digest>/content.md
Completeness: policy-filtered
Omitted: video — lecture.mp4
```

Notice view says `Notice source:`. Preserve every full selectable ID and path, wrapping rather than truncating even in narrow terminals; central presentation retains safe error rendering [`src/campusctl/presentation.py:119–167,650–688`; owner-held sanitized planning notes (2026-09-25, lines 172)].

| Case | Code | Status / exit | Publication |
| --- | --- | --- | --- |
| Complete | none | `ok` / 0 | one full package |
| Only excluded references | `resource-omitted` | `partial` / 1 | one filtered package with explicit omissions |
| No catalog; wrong/missing selected ID | `catalog-missing`; `entity-unknown` | `user-action` / 2 | none; missing catalog remediation `campusctl sync --only <domain>` |
| Existing `--out`, symlink/unsafe path or unresolved collision | `output-path-conflict` | `user-action` / 2 | none |
| Selected type/signature mismatch; exceeded reviewed size cap | `unsupported-media-type`; `file-too-large` | `user-action` / 2 | none |
| Unapproved request, redirect, logging or mutating endpoint | `policy-blocked` | `error` / 1 | none |
| Failed/empty/interrupted transfer; detail extraction/integrity failure | `download-failed`; `fetch-failed` | `error` / 1 | none |
| Browser session busy | `session-busy` | `busy` / 75 | none |
| Invalid arguments | `usage-error` | `user-action` / 2 | none |

Errors are safe `{code,message,remediation}` with nullable remediation and no raw exception, query-bearing URL, credentials or signed link; use the existing schema-1 envelope, UTC RFC3339 timestamps and exit map [`src/campusctl/envelope.py:11–47`; `src/campusctl/cli.py:257–281,541–546`; [Materials design](../30-materials/design.md); owner-held sanitized planning notes (2026-09-25, lines 36–42)].

## Selected-page extraction and resource guard [FR-001–005, FR-007]

The assignment adapter enters the selected course with `enter_course_section(page,config,course_id,'task')`, binds `a[data-act="detail"][data-id]` to the selected catalog task ID, and opens only that row's ordinary detail action. The observed destination is L `GET /std/taskView` (query `curPage=undefined` or page index), followed by L `POST /api/v1/task/detail` and `/api/v1/task/stdDetail` with encrypted `e`. Both responses must bind `body.report_no` to the selected native task ID and `body.course_id` to the selected course before any brief extraction; the two report IDs must agree. The detail response also carries `body.contents_id` and `body.report_nm`, but neither substitutes for `report_no`. Observed readable DOM: title `.card-body h4` (`과제명`), prompt/instructions in `.card-body`, maximum-points label (e.g. `만점 <points>점`), and separate submission UI (`#uploadFile`, file-upload modal). Extract readable brief and references only; **never** interact with upload controls or invoke submission methods. Opening the observed assignment detail left the submission badge and `badgeComplete` unchanged [`course_context.py:14–50`; owner-held sanitized observation report (2026-09-25, lines 61–67,118–133,183–187); owner-held sanitized evidence (2026-09-27)].

The notice adapter authenticates through the trusted login path, then enters the selected course via the observed course-row UI under the installed interceptor and clicks the `/std/notice` menu in its own provider adapter; PR1 `enter_course_section` has no `"notice"` section, so do not call it with one or edit PR1's shared file [`src/campusctl/providers/cnu/course_context.py:14–50`]. Resolve the per-course board rows against the selected catalog row with sync's `parse_board_rows` rules: retain the legacy global to-do number on a native-ID match, or on a unique title/day match when the catalog has no native ID. The board `row_idx` is not necessarily that global number. Bind the resulting catalog ID to exactly one observed native board-item link before opening L `GET /std/noticeDetail?no=<board-item-id>&curPage=1`; L `POST /api/v1/board/notice/info` loads its detail, and `/api/v1/board/cmt/list` loads comments. Never reconstruct the native ID from the composite ID. If matching is absent or ambiguous, or the opened detail cannot prove the same board item, fail before transfer. The per-course list contains `NO`, `제목`, attachment indicator, author, date and `조회수`, but **no `read_yn` column**. Loading notice info caused `조회수` +1 with no explicit mark-read request; this automatic count increment is approved. `/std/todo` has `read_yn` labels `읽지않음`/`읽음`; its transition remains a release check [owner-held sanitized observation report (2026-09-25, lines 69–75,103–116,183–187); owner-held sanitized policy decision (2026-09-25, lines 21–22,64–71); owner-held sanitized evidence (2026-09-27)].

**Exact fetch-operation data pins.** Each table row is a separate route object `{origin,path,operation,methods:[method],query?}`; an omitted query cell means no query allowed. PR1.1 provides the route schema/interceptor, **not** shared route literals. The rows are the union of guarded `/std/myLecture` roster/re-entry, `/std/lecture` course entry and each operation's section/detail requests [owner-held sanitized courses_and_lists_evidence trace (2026-09-25, lines 2–5340); owner-held sanitized assignment_detail_evidence trace (2026-09-25, lines 269–814); owner-held sanitized notice_detail_evidence trace (2026-09-25, lines 87–435); owner-held sanitized policy decision (2026-09-25, lines 41–66)]. Auth-flow requests remain outside operation policy.

| Operation | Origin | Method | Exact path | Optional query kinds |
| --- | --- | --- | --- | --- |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/std/myLecture` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/std/lecture` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/std/task` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/std/taskView` | `{"curPage":"page"}` |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/properties/messages.properties` | `{"_":"cachebuster"}` |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/properties/messages_ko.properties` | `{"_":"cachebuster"}` |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/course/addSessionCourseInfo` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/user/getUserInfo` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/user/getMenuList` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/alarm/getAlarmListByDate` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/course/getCeShortcuts` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/common/checkEnableUrl` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/boardM/getBoardItemList` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/term/getYearTermList` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/course/getStdMyCourseList` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/course/get` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/board/courseNotice/list` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/week/getStdWeekList` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/week/getStdEtcList` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/week/getStdActivityStatus` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/survey/getApplyPopList` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/board/popup/noticeList` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/task/stdList` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/task/detail` | — |
| `assignments.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/task/stdDetail` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/std/myLecture` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/std/lecture` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/std/notice` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/std/noticeDetail` | `{"no":"board-item-id","curPage":"page"}` |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/properties/messages.properties` | `{"_":"cachebuster"}` |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | GET | `/properties/messages_ko.properties` | `{"_":"cachebuster"}` |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/course/addSessionCourseInfo` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/user/getUserInfo` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/user/getMenuList` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/alarm/getAlarmListByDate` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/course/getCeShortcuts` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/common/checkEnableUrl` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/boardM/getBoardItemList` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/term/getYearTermList` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/course/getStdMyCourseList` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/course/get` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/board/courseNotice/list` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/week/getStdWeekList` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/week/getStdEtcList` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/week/getStdActivityStatus` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/survey/getApplyPopList` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/board/popup/noticeList` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/board/notice/list` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/board/notice/list/top` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/board/notice/info` | — |
| `notices.fetch` | `https://dcs-learning.cnu.ac.kr` | POST | `/api/v1/board/cmt/list` | — |

The `getStdActivityStatus` rows passed operation-scoped owner review on 2026-09-27 (owner-held sanitized evidence (2026-09-27): denying the read during guarded course entry left the section menus unrendered); both fetch operations now carry the same exact LMS POST XHR pin with `logging_token_reviewed:true` as the sync operations and materials download. The request originates from `/std/lecture`; the route schema has no Referer constraint. All other unsuppressed logging-token paths remain denied.

`page` accepts digits or literal `undefined`; `board-item-id` is anchored `TB_L_BOARDITEM[0-9]+`, and the adapter separately compares `no` with the selected native board-item ID (the guard validates shape, not identity). `_:"cachebuster"` accepts only 1–20 ASCII digits; no query key is required merely because permitted. Reject unknown/repeated keys, missing detail identity, redirects and signed/queried selected-file URLs [`docs/specs/00-foundation/design.md:93–132`; owner-held sanitized notice_detail_evidence trace (2026-09-25, lines 87–105); FR-002, FR-005].

The assignment route union includes its `assignments.sync` section pins and detail pins. Notice fetch uses the per-course list/detail, **not** a copy of global `notices.sync`: `/api/v1/board/std/qna/list` belongs only to `/std/todo` and is absent from **both** fetch tables. For the separately approved `read_yn` check use the reviewed `notices.sync` route set, not a fetch exemption. Static scripts/styles/fonts/images are GET from L only by PR1.1 resource type, with Panopto suppressions and all media/video excluded. Data XHR/fetch/documents remain exact path/method/query pinned; there are no prefix allowances [owner-held sanitized policy decision (2026-09-25, lines 15–20,33–35,41–66); owner-held sanitized courses_and_lists_evidence trace (2026-09-25, lines 2–5340)].

Authentication is outside operation policy: invoke existing trusted `ensure_logged_in` **once before** installing PR1.1 `install_ui_request_interceptor(page_or_context,policy,*,operation,diagnostics:UiRequestDiagnostics(),selected_file=None,selected_file_id=None)`; login-flow requests are not per-operation route pins. Install the interceptor before any roster/course/section/detail navigation; L GET `/std/myLecture` **remains guarded and exactly pinned** for roster and course re-entry. PR1.1 removes `ensure_logged_in` from `enter_course_section`; it is navigation-only on an already-authenticated page. If auth expires mid-operation, deny login redirect/navigation (`redirect` or `route`) and fail `login-action-required` or existing login error with **no re-login/retry** under guard; user reruns the command. `UiRequestPolicy.from_reviewed_config(FETCH_POLICY)` powers guarded fetch operations; `guard_ui_request` returns `allow`/`suppress` for reviewed requests and `raise_if_denied()`/`close()` fail closed on unexpected requests, CDP reuse, redirects and auto-subresources. Fetch aborts only the three reviewed Panopto script/connection-logging classes and the owner-approved (2026-09-27) sync-equivalent course-roster-image, favicon-icon, external-telemetry and panopto-sso-popup requests without failing the operation. `panoptoSaml-<hash>.js` is not suppressed and follows the ordinary same-origin static script guard. Any OTHER unexpected page request, including uploads/mark-read/logging, is hard `policy-blocked` [owner-held sanitized policy decision (2026-09-25, lines 10–20,33–35); owner-held sanitized evidence (2026-09-27)].

For an official attachment, verify that the official control's attachment-native `file_id`, `parent_kind` and `parent_id` belong to the selected detail before any URL binding or byte reads: `(assignment, <selected task_id>)` or `(notice, <selected board-item-id>)`. Construct PR1.1 `SelectedFileRequest` **only** through `bind_selected_file_request(policy, *, selected_file_id, resolved_url, source='official-control'|'fileDownload-response', operation='assignments.fetch'|'notices.fetch')`; pass the attachment-native `selected_file_id` independently alongside `selected_file=selection` to the PR1.1 interceptor/`guard_ui_request`. Neither ID is the composite catalog entity ID. A mismatch or sibling URL on the same template denies. PR4 provides `guard_response` for headers/MIME/signature/size and `fetch_official_attachment` for transfer; **PR1.1 alone guards requests**, including case-insensitive `Range` headers. Possible reviewed resolved URL templates are C = `https://dcs-lcms.cnu.ac.kr` GET `/upload/{storage-id}/{encoded-filename}` or L GET `/file/{term}/{course}/board/{board-manager}/{board-item}/{stored-filename}`, only when bound to the selected attachment ID/control. Reject decoded `/`, `..`, double encoding, unexpected query, redirects and `Range`; no prefix allowance. PR1.1 pins only `materials.download` selected-file templates; PR5 adds operation-scoped fetch entries only as verified. Notice official-control mapping and transfer remain deferred until an attachment-bearing notice can be observed; meanwhile its attachments are unapproved-route omissions. Preclassify same-origin approved image extensions before transfer; verify known signature after bounded retrieval. Unknown/external/video references are omissions without byte reads; redirect/header/signature/technical failure aborts the package [owner-held sanitized policy decision (2026-09-25, lines 24–35,57–60,68–72); [Materials design](../30-materials/design.md:87–100); FR-004–005].

The notice attachment initiation request is **not observed**: do not assume that its control invokes the archive-only L POST `/api/v1/archive/fileDownload`. Any such fetch-operation data route and file target binding must be independently observed and approved before notice attachment transfer; until then record attachments as unapproved-route omissions, not as successful downloads [owner-held sanitized policy decision (2026-09-25, lines 57–69); owner-held sanitized observation report (2026-09-25, lines 243–245); owner-held sanitized evidence (2026-09-27)].
Allowed non-video extensions (case-insensitive) are `pdf`, `ppt/pptx`, `doc/docx`, `xls/xlsx`, `hwp/hwpx`, `txt/md`, `png/jpg`, `zip`, `ipynb/py/c/cpp/java/js/sql`; `max_bytes=200_000_000`, ZIP is stored and never extracted, video always blocked. Observed compatible type/signature pairs: PDF `application/x-pdf` + `%PDF-`, PPTX `application/vnd.openxmlformats-officedocument.presentationml.presentation` + `PK\x03\x04`, ZIP `application/zip` + `PK\x03\x04`. Unobserved types require a known per-extension signature table; MIME alone never suffices. No `Content-Disposition` was observed: derive filename from official control display text or `getAttachFileList` name, strip trailing `바로보기`, then sanitize [owner-held sanitized policy decision (2026-09-25, lines 23–32); owner-held sanitized observation report (2026-09-25, lines 193–238)].

Preserve readable detail text in DOM order as UTF-8 Markdown: headings/paragraphs/lists and visible ordinary link labels; no active HTML/scripts. Included image/file references become relative local links and omitted references are explicit markers at their original positions. Ordinary hyperlinks are escaped visible text followed by `[link URL omitted]`, **not** clickable remote URLs: silently dropping a signed query would change its target. The local package never persists cookies, credentials, signed URLs, queries, fragments or logging pixels [owner-held sanitized planning notes (2026-09-25, lines 89)].

## Package schema, storage and lock [FR-006–010, FR-013]

Read PR2/3 schema-1 catalogs with `campusctl.domain_catalog.domain_catalog_path(domain,root)` and `read_domain_catalog(domain,path)`; do not merge/write either catalog during fetch. Each has `schema_version`, `generated_at`, `enrollment_state`, `courses`, `failed_courses` and its domain rows; row fields include `entity_id`, `course`, assignment `task_id` or notice legacy identity. Staleness alone does not establish selected-detail correctness [[Foundation design](../00-foundation/design.md); owner-held sanitized planning notes (2026-09-25, lines 44–46,74,76,150)]. Keep the lecture catalog and the legacy notifier's stored state untouched. The downstream consumer runtime owns job-local `source.json` with role, selection timestamp, authorization/attestation, retention and media presence; it consumes CLI JSON and references this campusctl package path rather than reading private catalogs [owner-held sanitized source-package specification (2026-09-25, lines 297–308); owner-held sanitized planning notes (2026-09-25, lines 150)].

Package layout:

```text
package.json
content.md
images/<safe-original-name>
attachments/<safe-original-name>
```

Manifest `schema_version:1`; `entity_id`, `kind:"assignment"|"notice"`, `course:{id,label}`, normalized `source_ref:{origin,page_path,provider_native_id}`, UTC `retrieved_at`, `content_path:"content.md"`, `completeness:"complete"|"policy-filtered"`, `resources[]`, `omitted_resources[]`. Included resources carry full lowercase SHA-256 `resource_id`, kind, relative POSIX path, normalized source reference, original name, media type, size and byte `sha256`; omitted references carry ID, source reference, name/type and reason. Normalize origin/path without credentials, query or fragment. Manifest paths are relative; CLI result paths are absolute [FR-006; owner-held sanitized planning notes (2026-09-25, lines 80–92)].

Resource ID is SHA-256 over canonical UTF-8 JSON array: `['file',course_id,provider_file_id]` for official files; `['inline',entity_id,kind,one_based_reference_order]` for inline references. Order counts *every* reference, including omissions. Canonical encoding uses sorted keys, compact separators, `ensure_ascii=False`, UTF-8 without BOM/trailing newline. **Distinct inline positions retain distinct IDs even when URL/bytes match**; do not coalesce their manifest entries. Only a repeated *same* resource ID reuses first local bytes if its selected control and declared source metadata agree; repeated omissions retain each content marker but one omitted record; conflicting identity/bytes fail package [FR-007; owner-held sanitized planning notes (2026-09-25, lines 89–90)].

Reuse PR4 `campusctl.material_files.safe_component(name, *, suffix='')`: NFC; basename after both slash styles; replace controls and `<>:"|?*`; trim trailing dots/spaces; map empty, dot/dotdot and case-insensitive Windows device names (even with extension) to `resource`. Collision key uses final NFC+casefold name after truncation. Preserve original safe name first; if collision, append `~<full-resource-id>` before valid extension (last dot only when neither leading nor trailing). Enforce final 200 UTF-8 bytes including suffix and extension, preserving one stem codepoint and truncating overlong extension/stem at UTF-8 boundaries. Recompute collision key; unresolved collision fails atomically, never loops, follows a symlink or overwrites [owner-held sanitized planning notes (2026-09-25, lines 89–90); [Materials design](../30-materials/design.md)].

Default private location is `<data-dir>/sources/<assignment|notice>/<SHA256(UTF8(full entity_id))>/<package-digest>/`. Hash a canonical manifest excluding only `retrieved_at`, plus `content.md` and resources in lexical path order, with length-framed names/bytes. Validate existing digest paths against actual bytes before reuse, retaining original retrieval time; changed bytes receive a new digest. Reject existing explicit `--out`; stage privately under the same parent and atomically publish only after all checks pass. No published output on failed transfer [FR-009; owner-held sanitized planning notes (2026-09-25, lines 89–92)].

Hold one browser session lock across authentication, detail, transfer and publication via PR1 `browser.open_session(config, data_dir=root, headless=approved_opt_in)`; default `<data-dir>/session.lock`, optional `browser.lock_path`, busy/75. Do not acquire legacy notifier's browser lock or a second catalog lock [`src/campusctl/browser.py:286–296,385–400`; `src/campusctl/lock.py:36–81`; owner-held sanitized planning notes (2026-09-25, lines 16,178)].

## Module layout and FILE OWNERSHIP [FR-002–016]

Foundation owns `src/campusctl/domain_catalog.py`, `src/campusctl/providers/cnu/course_context.py`, `src/campusctl/providers/cnu/ui_policy.py`, and `src/campusctl/commands/__init__.py:discover_domain_modules()`. Current discovery skips an explicit `CAPABILITY.policy.approved=False` **domain**, not an individual fetch subcommand; PR2/3 domain modules must remain approved for their released list/sync commands [`src/campusctl/commands/__init__.py:27–110`]. PR1.1 adds `suppress`, `static_asset_origins`, `static_resource_types`, `selected_file_routes`, `SelectedFileRequest`/`bind_selected_file_request`, `UiRequestDiagnostics` and `install_ui_request_interceptor` to Foundation's policy. PR4 owns `request_policy.py`, `attachment_transfer.py`, `material_files.py`; PR5 consumes these interfaces, never edits their files [FR-004–005, FR-015; [Materials design](../30-materials/design.md:89–95)].

**Frozen PR5 cross-lane contract** (proposed, not existing code): `src/campusctl/source_package.py` owns immutable `ResourceReference(kind: str, source_url: str, original_name: str|None, media_type_hint: str|None, label: str, provider_file_id: str|None, official_target: OfficialAttachmentTarget|None)` and `DetailSnapshot(source_url: str, provider_native_id: str|None, parts: tuple[str|ResourceReference,...])`. String parts are escaped Markdown fragments with **no raw query-bearing links**; resource parts occur at their exact inline locations and include every omitted candidate. The builder concatenates fragments; renders included images as `![escaped label](escaped relative path)`, attachments as `[escaped label](escaped relative path)`, excluded resources as `[Omitted: reason — escaped label]`. The adapters do not read resource bytes or persist raw source URLs. Builder signature: `async build_source_package(page, row: dict, snapshot: DetailSnapshot, policy: RequestPolicy, root: Path, out: Path|None) -> dict` returning the CLI `source_package` object. It classifies/fetches resources, builds manifest/content and publishes atomically. `providers/cnu/assignment_detail.py` exports `async extract_assignment_detail(page, config, row, policy) -> DetailSnapshot`, and `providers/cnu/notice_detail.py` exports `async extract_notice_detail(page, config, row, policy) -> DetailSnapshot`. Each adapter navigates only its approved detail action and emits all ordered references [FR-002–009; owner-held sanitized planning notes (2026-09-25, lines 89–90,172)].

For an official control with no observable candidate URL before transfer, the adapter sets `ResourceReference.source_url` to its **selected detail page URL** (in memory), `provider_file_id` to the verified file ID, and `official_target.candidate_url=None`; the builder never guesses a file URL. It uses the normalized observed candidate URL when one exists; otherwise the normalized detail-page reference plus native file ID. The PR4 transfer result does not expose a final response URL. The same fallback applies to omitted official controls. Inline references carry their observed URL in memory; all persisted references still omit query/fragment [FR-004, FR-006; owner-held sanitized planning notes (2026-09-25, lines 40,89); [Materials design](../30-materials/design.md)].

For an observed official file control, construct PR4 `OfficialAttachmentTarget(file_id, parent_kind, parent_id, control_locator, candidate_url)` with `parent_kind:"assignment"|"notice"` and selected detail's verified `task_id` or `board_item_id` as `parent_id`, then call `providers/cnu/attachment_transfer.py` `async fetch_official_attachment(page, target, policy, temp_dir, *, max_bytes) -> FetchedAttachment(temp_path, sha256, media_type: str|None, size_bytes)`; PR5 cleans and moves its temporary file to package staging. An adapter that cannot establish the selected parent ID does not construct a target. Never subprocess `materials download` or write its cache. PR1.1 `UiRequestDiagnostics()` stores only `suppressed_count` and `suppressed_reasons:dict[str,int]` (`media-integration`, `logging`), never URLs. PR1.1 `guard_ui_request` permits `allow`/`suppress` and rejects all other request paths/headers; PR4 `guard_response` alone validates the selected file response. Fixture an allowed GET against identical GET with `rAnGe: bytes=0-`: the latter blocks before body [FR-002, FR-005; [Materials design](../30-materials/design.md:100–126)].

**Minimal shared hooks:** PR5 edits neither `src/campusctl/cli.py` nor `src/campusctl/presentation.py` nor Foundation's discovery/policy. PR2/3 domain modules already participate, so PR5 extends only their own `register/dispatch/render` paths. During implementation keep `fetch` out of registered parser commands and published `CAPABILITY.commands`/contract until release gates clear; exercise fixture-only adapters/package/dispatch directly. Do not set a whole PR2/3 domain's `policy.approved=False`. At release register `fetch`, append to its domain's `CAPABILITY.commands`, merge exact fetch operation routes with existing list/sync routes and set `CAPABILITY.policy` `{approved:true,read_only_evidence:<sanitized-reviewed-reference>,origins:[L,C-if-selected-file],routes:[{origin,path,operation,methods}],allowed_media:[{mime,extensions}],max_bytes:200000000,suppress:[{name,origin,path_template,operation,methods,reason}],static_asset_origins:[L],static_resource_types:['script','stylesheet','font','image'],selected_file_routes:[{origin,path_template,operation,methods}]}`. PR1.1 owns validation of additional keys and selected-attachment binding; PR4 owns MIME/signature checks. The `suppress` entries are only three named Panopto requests, not `panoptoSaml`; PR5 adds `selected_file_routes` only for verified fetch-operation bound templates, never inherits `materials.download` automatically. Neither exact-route pins nor static-resource allowance accepts unexpected data requests. Domain docs owner edits only its own contract at publication [FR-001, FR-005, FR-015; owner-held sanitized policy decision (2026-09-25, lines 8–35,41–66); `src/campusctl/commands/__init__.py:27–110`].

| PR5-owned path / implementation lane | Responsibility |
| --- | --- |
| NEW `src/campusctl/source_package.py`; `tests/test_source_package.py` | One package/manifest/hashing/name/atomic algorithm; no provider detail navigation. |
| NEW `src/campusctl/providers/cnu/assignment_detail.py`; `tests/test_assignment_detail.py` | Assignment selected-detail open, extraction and references; no submission controls. |
| NEW `src/campusctl/providers/cnu/notice_detail.py`; `tests/test_notice_detail.py` | Notice per-course selected-detail open, extraction and references. |
| `src/campusctl/commands/assignments.py`; `tests/test_assignment_fetch_commands.py` | Assignment-only fetch command, gated registration and local fixture proof. |
| `src/campusctl/commands/notices.py`; `tests/test_notice_fetch_commands.py` | Notice-only fetch command, gated registration and local fixture proof. |
| `tests/test_fetch_commands.py` | One integration owner for both CLI/envelope/human fixture paths. |
| `docs/contracts/assignments.md`; `docs/contracts/notices.md` | Each command owner updates its own canonical contract after gates; the integration owner owns shared index/version. |

## Fixture strategy and evidence [FR-001–016]

The legacy notifier checkout is **READ-ONLY**. Use owner-supplied sanitized report observations (owner-held sanitized observation report (2026-09-25, lines 61–75,103–133,183–187)) as trace provenance and build deterministic **synthetic** detail fixtures under `tests/fixtures/lms_sources/`; never label invented DOM as a captured real page. The assignment fixture covers `.card-body h4`, prompt/instructions, points, unchanged submission state and zero interaction with `#uploadFile`/upload modal. The notice fixture covers the per-course selected board ID, view count +1 and no unsupported mutation claim. Simulate both `read_yn` outcomes, without presenting simulations as the unperformed live check. No notice attachment was present in observed notices (owner-held sanitized observation report, 2026-09-25, lines 243–245); synthetic notice-file fixtures may exercise the algorithm, but live notice attachment transfer is deferred until an attachment-bearing notice exists. Do not fabricate `detail_read_state_sanitized.json` [FR-001, FR-014].

Fixture acceptance: each synthetic detail extracts its represented text/order and binds selected identity; assert assignment upload controls are never invoked, notice view-count increment is accepted, and suppression aborts the three reviewed Panopto script/connection-logging classes plus the owner-approved (2026-09-27) roster-image, favicon, external-telemetry and Panopto SSO popup suppressions matching sync without failing the operation. `panoptoSaml` remains ordinary; other unlisted requests fail closed. Test attachments through PR4 transfer, selected file-ID mismatch, sibling-template/redirect/range denial before bytes, known/unknown type classification, zero output after technical failure and policy-only omission; test manifest IDs/digest/reuse, link redaction, safe names and CLI statuses. No project-wide test runs in parallel implementation branches [FR-001–015; owner-held sanitized policy decision (2026-09-25, lines 10–35,61–72); owner-held sanitized evidence (2026-09-27)].

## Live verification gate [FR-001, FR-004–005, FR-014–016]

**Implementation is unblocked:** Owner-approved assignment and notice request pins, assignment detail DOM/submission observation and notice view-count effect are in owner-held sanitized policy decision (2026-09-25, lines 21–35,61–66) and owner-held sanitized observation report (2026-09-25, lines 61–75,103–133). Build fixture-only adapters/package/CLI in separate PR5 lanes now; do not claim real HTML captures, per-course to-do coverage or notice file controls exist when not observed.

**Release remains blocked:** A separately authorized owner observation must compare `/std/todo` `read_yn` before/after the precise notice detail open, recording either unchanged or flipped (both accepted); check selected identity, text/images, attachment references as unapproved-route omissions and all state effects. `조회수` +1 is accepted and recorded; any **other** submission/attendance/grading/enrollment effect stops that path. A changed `read_yn` is accepted once recorded, not an excuse to suppress a request. Keep fetch capabilities/contracts unpublished until fixture acceptance and these checks pass. Headless stays unavailable pending independent confirmation. Notice attachment transfer requires later observation of an attachment-bearing notice, official-control selection, selected file ID, bounded download/response validation and separate approval; its absence does not block publishing fetch with explicit omissions [owner-held sanitized policy decision (2026-09-25, lines 21–22,68–72); owner-held sanitized observation report (2026-09-25, lines 103–116,243–245); owner-held sanitized evidence (2026-09-27); FR-001, FR-014–015].

Record sanitized release observation separately as `tests/fixtures/lms_sources/detail_live_observation_sanitized.json` only if authorized; preserve any original sanitized owner baseline unchanged. Compare results without treating synthetic fixtures as live evidence [FR-014].

[NEEDS CLARIFICATION: Before release, observe `/std/todo` `read_yn` before/after. Notice provider-native ID mapping, detail-file final URL availability, unobserved MIME/signatures and headless compatibility must not be guessed; defer notice attachment transfer until an attachment-bearing notice exists.]

