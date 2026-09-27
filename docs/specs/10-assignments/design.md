# Assignment collection design

[Requirements](spec.md) · [Constitution](../constitution.md)

After the shared roster and course selection, click the task menu on the committed course page. Capture the course-specific `/api/v1/task/stdList` response and verify its relation to the task page and selected topbar course. Read `#table_list` rows, including attached hidden rows, and resolve each `a[data-act="detail"]` native ID separately from title, due text and submission badge. A missing/duplicate/unaddressable ID fails this course. Confirm a zero-row task list only after the successful response and an idle zero-row `tbody#tbody`; an unrelated alarm widget is not evidence.

Construct `cnu_assignment:<course-id>:<task-id>` using the shared identity builder. Store both task and course IDs separately; preserve displayed due text without timezone inference. The domain catalog merge replaces only successful courses, retains failed courses and exposes stale markers. Errors name `assignments` and the failing step (`item-identity-missing`, `course-sync-failed`, or discovery/catalog errors). Page requests are not intercepted. The operation never opens a detail/submission control. Local list and status consume only catalogs; selected-detail fetch remains unregistered.
