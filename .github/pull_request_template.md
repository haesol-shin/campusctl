## Purpose / outcome

<!-- Describe the user-visible result and link the issue. Keep one outcome per PR. -->

## Verification

<!-- List the commands you ran and their results. CI is not a substitute for local focused proof. -->

| Command or check | Result |
| --- | --- |

## Live-run status

<!-- Choose one. Never include unsanitized LMS output, raw traffic, or private course data. -->
- [ ] Not applicable (no LMS interaction changes)
- [ ] Pending (explain what remains)
- [ ] Sanitized evidence (summarize the live check or link reviewed evidence)

## Checklist

- [ ] Tests and required CI gates pass
- [ ] The spec, implementation, relevant tests and docs are in this outcome PR
- [ ] CLI/domain contract docs are updated if behavior changes
- [ ] A `changelog.d/` fragment is included for user-facing changes, or the reason it is not needed is stated
- [ ] LMS interaction changes have sanitized live-run evidence, or live-run status is marked not applicable
- [ ] Examples and evidence contain no real course IDs or names, personal data, local developer paths, private infrastructure, binaries or unsanitized LMS output
