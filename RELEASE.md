# Releasing campusctl

Before 1.0, a minor release adds features or changes CLI/JSON contracts (including breaking changes); a patch release contains fixes only. This follows the pre-1.0 release practice of tools such as ruff and uv, rather than promising stable major-version compatibility prematurely. Release dates follow the local date convention (KST, matching `--date YYYY-MM-DD` / `dt.date.today()`).

1. Ensure user-facing changes have fragments in `changelog.d/` (`direct-<slug>.md`, or `<issue-number>-<slug>.md` if linked to an issue; see `changelog.d/README.md`). Run `uv run --locked python scripts/release.py --check` to verify version references and lockfile consistency without mutating `uv.lock`.
2. Run `uv run python scripts/release.py patch` (or `minor`, `major`, or an explicit `X.Y.Z`; use `--date YYYY-MM-DD` if needed). This bumps project, lockfile, installation and skill references, folds fragments into `CHANGELOG.md`, and removes consumed fragments. Review the diff.
3. Open the release PR; obtain reviewer approval and green required CI (Ubuntu lint; pytest on Windows 3.11/3.12/3.13, macOS 3.12, and Ubuntu 3.12; Windows installation and runtime smoke), then squash-merge it to protected main.
4. Confirm the same hosted matrix runs green on main before tagging. CI also runs on `v*` tags.
5. From the merged commit, push tag `vX.Y.Z`. The tag workflow checks versions and the changelog entry and creates the GitHub Release with the installation instructions.
6. Afterwards, bump the agent-skills pin to the new tag.

Do not change `CHANGELOG.md` or remove fragments in ordinary PRs. A release requires at least one valid fragment.
