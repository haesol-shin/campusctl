# Shared source design

[Requirements](spec.md) · [Constitution](../constitution.md)

`identity.py` builds opaque assignment, notice and material IDs from separate course/native components. `domain_catalog.py` maintains versioned domain-specific JSON catalogs with atomic sibling-temporary writes and per-course merge/stale metadata. Readers use no browser lock. Browser operations use the shared non-blocking session lock and return `busy`/75 on contention.

The course roster establishes candidates, a roster-row click establishes session context, and a committed course page plus topbar verifies the selected course. The section menu opens lecture, task, notice or archive pages. Response capture starts with the relevant section and verifies page/response identity before rows are merged. Combined collection stages per-domain rows for each course and commits each domain's successful courses independently; a failed domain retains its previous rows without blocking another domain. The global to-do view supports identity/read-state enrichment and does not replace a course's own section.

No page request interceptor belongs in sync or fetch. The attachment transfer is separate: resolve exactly one selected official file control, bind the transfer to its file identity and URL, check successful response/type/signature/size, and publish through safe atomic file output. Consult [materials](../30-materials/design.md) for response and naming checks.
