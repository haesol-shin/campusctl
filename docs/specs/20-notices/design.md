# Notice collection design

[Requirements](spec.md) · [Constitution](../constitution.md)

The shared pass reads `/std/todo` once for optional read-state/legacy-key enrichment. A verified course-row selection commits `/std/lecture`; the rendered notice menu opens `/std/notice`. Capture the first post-commit `/api/v1/board/notice/list/top` and `/api/v1/board/notice/list` responses, verifying successful headers, board context, selected course IDs and consistency with the rendered first page. Never treat an empty to-do grid as an empty board.

Deduplicate top/ordinary items by native board ID; check ordinary total and rendered count. Numeric page 2+, enabled Next or total beyond supported first page returns `notice-board-paginated` for that course. Match a to-do row by native board ID when available or a unique course/title/date-day tuple; ambiguous matches return `notice-identity-ambiguous`. Preserve matched displayed legacy timestamp and ordinal. Board-only identity prefers valid `insert_dt_addtime` and falls back to displayed `insert_dt` day. Nullable `is_unread` remains null without a match; `writeruser_name`, `boarditem_viewcnt` and numeric `file_yn` supply author, view count and attachment indicator.

Complete rows replace that course's catalog rows; failures retain old rows marked stale. Other courses/domains continue. The board menu is clicked rather than navigating directly to a board URL. No request interceptor or detail view is part of sync.
