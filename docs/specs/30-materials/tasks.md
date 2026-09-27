# Materials verification

[Requirements](spec.md) · [Design](design.md)

- Verify modal and inline archive targets yield distinct metadata rows; unresolved posts retain stale course rows
- Verify human number and picker choose only one last-printed file, fail after catalog change, and JSON requires a full ID
- Check selected-file ID, board and actual URL binding plus response status, type, signature, size and digest before publication
- Check no symlink/traversal overwrite, deterministic collisions, redirected Windows Downloads, no-range restart and verified receipt skip
- Use the [live run protocol](../live-run.md) when attachment or LMS navigation behavior changes
