# Notice implementation acceptance (owner decision 2026-09-26)

- [x] Make per-course `/std/notice` boards authoritative; use global `/std/todo` only for matching read state and historical identity, never follow notice details
- [x] Bind settled roster selection, session-course identity, board page course identity, main-frame commit, ordered top/list requests and exact Referer; fail stale or duplicate responses
- [x] Fail multi-page boards with `notice-board-paginated`, retain stale rows and require header 200, zero ordinary total, empty top/ordinary arrays and zero DOM rows for verified-empty
- [x] Pin observed shared-page routes and suppress exact optional Panopto SAML, telemetry and college thumbnail requests; continue only the exact reviewed activity-status XHR
- [x] Preserve legacy label/date/number keys where to-do matches; document board `insert_dt_addtime`, author, integer view count, nullable attachment indicator and nullable unread state
- [x] Exercise two-course boards, empty, pagination rollback, todo merge, detail-free navigation and cached human/JSON CLI with fixture-only tests

The external legacy notifier is read-only provenance; no live LMS calls or scheduled notifier changes belong in this feature.
