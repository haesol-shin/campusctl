# Contributing

## Plan an outcome, not a file type

Open one pull request per user-visible outcome. Keep its specification, implementation, relevant tests, user docs and `changelog.d/` fragment together; do not split an outcome into separate spec, code, test or documentation PRs. Batch CI- or test-infrastructure-only work rather than opening a PR for every small adjustment. Track work with GitHub issues and milestones, and aim for about 5–10 PRs per release as a planning guide, not a hard limit.

If a live run finds a problem, add the follow-up fix as another commit on the same issue PR. Include sanitized evidence when LMS interactions change; otherwise mark the live-run status not applicable. Follow the [live-run protocol](docs/specs/live-run.md), and never paste unsanitized LMS output into a PR.

Keep each outcome small enough to review. Stage only intended paths with `git add <path>`. Commit and PR titles use `type(scope): summary` in English; commit bodies have one blank line followed by at most four adjacent imperative `- ` bullets without trailing periods or trailers. Verify the final message with `git log -1 --format=%B | cat -A`. The [constitution](docs/specs/constitution.md) governs these review rules and public-data limits.

Ordinary PRs add fragments but do not edit `CHANGELOG.md` or versions. Release integration serializes the changelog and version changes in its own PR; see the [fragment policy](changelog.d/README.md) and [release process](RELEASE.md).

## Scan exceptions

CI scans commits and the current tree with `.gitleaks.toml` for default secret detectors and `.gitleaks-public.toml` for local paths and emails, then checks tracked files for binary content. If a safe fixture or example needs an exception, request it in a PR for review. A narrowly scoped path regex in `.gitleaks-public.toml` `[allowlist] paths` exempts that path from the public path/email rules and the binary check only; a default secret-detector finding needs its specific fingerprint in `.gitleaksignore`. Do not add private data to justify an exception.
