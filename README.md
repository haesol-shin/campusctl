**English** | [한국어](README.ko.md)

Check your CNU coursework from the terminal or your AI agent.

`campusctl` shows CNU students their lectures, assignments, notices and materials in a local CLI or an AI agent, syncing through the normal LMS browser; the Chungnam National University (CNU) LMS is its only supported provider.

## 30-second demo: `campusctl status`

```text
Coursework status (local catalogs)
Assignments due soon: 1; unknown: 0
  Practice assignment
  See: campusctl assignments list
Unread notices: 1; unknown: 0
  Course update
  See: campusctl notices list
Open incomplete lectures: 1; unknown: 0
  Welcome lecture
  See: campusctl lectures list
```

<a id="quick-start"></a>
## Quick start

1. **Install.** Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Git](https://git-scm.com/downloads) (Python 3.11+), then run this on Windows, macOS or Linux:

   ```sh
   uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
   ```

   Run `uv tool update-shell` and open a new terminal so `campusctl` is on `PATH`.

   <details>
   <summary>Linux browser system libraries</summary>

   ```sh
   uv tool run --from playwright playwright install-deps chromium
   ```

   </details>

2. **Set up.** In an interactive terminal, run `campusctl setup` for guided configuration, Chromium and hidden keyring password entry; it offers an optional first sync.
3. **Check.** Run `campusctl status`. If setup skipped the sync, run `campusctl sync` first.

## Common tasks

| Task | Command |
| --- | --- |
| Refresh all four catalogs | `campusctl sync` |
| List unfinished lectures | `campusctl lectures list` |
| Play a lecture you chose | `campusctl lectures play ID` |
| List assignments | `campusctl assignments list` |
| Read an assignment you chose | `campusctl assignments fetch ID` |
| List course notices | `campusctl notices list` |
| Read a notice you chose | `campusctl notices fetch ID` |
| List material files | `campusctl materials list` |
| Download one material | `campusctl materials download ID` |

## AI agents

Install the skill globally with `npx skills add https://github.com/haesol-shin/campusctl -g` (or `bunx skills add https://github.com/haesol-shin/campusctl -g`); see [agent skill setup](docs/agent-skill.md).

## Good to know

- Lists and `status` read cached catalogs offline; pass `--refresh` or run `campusctl sync` for new data.
- Playback uses one visible official player at a time; the local browser context closes when the command ends.
- Your password stays in the OS keyring and catalogs stay in a local data directory; see [configuration](docs/configuration.md).
- Opening a notice detail may add a view and change its read state.

## FAQ

- **Is campusctl official?** No; it is independent and not affiliated with CNU. See [usage](docs/usage.md).
- **Does it mark attendance?** It plays through the official player and reads LMS state afterward; the LMS decides what counts. See [usage](docs/usage.md).
- **Where are password and data stored?** In your OS keyring and campusctl's local data directory. See [configuration](docs/configuration.md#paths).
- **Where are downloads saved?** In your OS Downloads folder under `campusctl/<course label>/`, or elsewhere with `--out DIR`. See [usage](docs/usage.md).
- **Can I read assignment or notice details?** Yes, fetch explicitly selected full IDs. See [usage](docs/usage.md).

## Documentation

[Installation](docs/installation.md) · [Usage](docs/usage.md) · [Configuration](docs/configuration.md) · [Troubleshooting](docs/troubleshooting.md) · [Agent skill](docs/agent-skill.md) · [CLI contract](docs/contracts/cli.md) · [MIT license](LICENSE)
