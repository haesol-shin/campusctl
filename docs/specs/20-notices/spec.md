# Course notice metadata

[Constitution](../constitution.md) · [Design](design.md) · [Tasks](tasks.md)

`sync` includes notices in its combined pass; `sync --only notices` narrows the request. The selected course's notice board is the primary source, not the global to-do list. The shared course selection is verified once, then the board opens through its rendered menu. Board responses must belong to the committed page and selected course. Global to-do rows only enrich matching board rows with legacy identity and read state; unmatched board notices remain present with unknown read state.

Include every nondeleted notice from the first complete board page. A second page or inconsistent count fails that course as `notice-board-paginated`, preserving its previous rows. A verified empty board replaces old rows only after successful top and ordinary list responses and zero rendered rows. Ambiguous historical identity fails as `notice-identity-ambiguous` rather than inventing a key. Keep `legacy_key` from exact course label, displayed date/time and ordinal where matched; board-only items use valid `insert_dt_addtime` or displayed day. Rows include title, dates, nullable read state/status, author, view count and nullable attachment presence without storing contact identifiers.

A course failure reports the domain and step and retains stale rows while other domains continue. `notices list` is local; `--refresh` syncs notices first. Sync never opens notice detail or transfers attachments. Pages issue their ordinary requests unfiltered. Selected notice detail fetch is specified in [fetch](../40-fetch/spec.md).
