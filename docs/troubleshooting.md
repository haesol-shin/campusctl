# Troubleshooting

Use the code in JSON `errors` or the message in human output to choose a next step; the [CLI contract](contracts/cli.md) documents output modes, response fields, and exit codes.

| Error code | What to do |
| --- | --- |
| `config-missing` | Run `campusctl config init` to create the file at the path reported by `doctor`; use `CAMPUSCTL_CONFIG_DIR` to change its directory. |
| `config-exists` | Edit the existing configuration at the path in the error, or move it before running `campusctl config init` again. |
| `credentials-not-configured` | In a terminal, run `campusctl auth set` to save the password to a secure OS keyring. |
| `credential-backend-insecure` | Select a secure OS keyring backend; do not switch to plaintext or file-based storage. |
| `credential-backend-unavailable` | Make the secure keyring available to your user session, then run `campusctl auth status --check`. |
| `credential-helper-failed` | Check the helper argv, interpreter, JSON stdout, username match, and its 30-second limit without printing secrets. |
| `display-unavailable` | Use a logged-in desktop session or run the browser command under Xvfb. |
| `browser-not-installed` | Run `campusctl setup` to install the browser. |
| `browser-install-failed` | Rerun `campusctl setup` and check your network connection and antivirus settings. The manual fallback is `playwright install chromium` (or `uv run playwright install chromium` from a checkout). |
| `browser-endpoint-unreachable` | Start the configured browser and check `browser.cdp_endpoint`; CDP mode does not fall back to a local browser. |
| `catalog-missing` | Run `campusctl sync --only lectures` to create the catalog. |
| `catalog-outdated` | Run `campusctl sync --only lectures`, then retry with the refreshed catalog. |
| `login-failed` | Check the account name and credentials. `auth status --check` checks credential availability, not whether CNU accepts the login. |
| `login-action-required` | Sign in through a normal browser to accept terms or complete the required password change, then retry. |
| `lms-unavailable` | Check the LMS and network connection, then retry when the service is available. |
| `course-sync-failed` | Retry once the selected course's task, archive, or notice board has loaded; the sync checks the post-navigation list response and the active course before publishing rows. |
| `course-discovery-failed` | Retry once the enrolled-course list loads in the LMS. Check the newest private structural diagnostic under the configured data directory's `diagnostics/roster-*.json`. It contains no course names or credentials; review URL paths before sharing it. |
| `session-busy` (status `busy`, exit 75) | Another operation owns the browser lock. Let it finish; do not automatically retry or remove an active lock. |

On Windows, allow a quarantined Playwright browser through Windows Security; do not disable Defender or SmartScreen. If a `.ps1` helper is blocked by execution policy, put `-ExecutionPolicy Bypass` only on that helper's explicit PowerShell argv, never set a global bypass. If a helper fails on a long or non-ASCII path, try a shorter path and keep each executable and argument as its own TOML array item.
