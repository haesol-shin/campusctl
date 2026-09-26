# Changelog fragments

Pull requests changing user or operator behavior add one fragment under `changelog.d/`.

Filename format:
- `<issue-number>-<slug>.md` for issue-backed changes; `direct-` is forbidden when an issue exists.
- `direct-<slug>.md` for pull requests without a linked issue.

Use lowercase alphanumeric words separated by hyphens for the slug (`README.md` is policy documentation, not a fragment). Ordinary pull requests must not edit `CHANGELOG.md` or delete existing fragments.

Use only level-two headings: `## Added`, `## Changed`, `## Deprecated`, `## Removed`, `## Fixed`, or `## Security`, each followed by at least one nonempty `- ` bullet. No other prose belongs in fragments. Release preparation folds fragments in stable filename order and removes them; behavior-neutral changes need no fragment.
