# Agent skill setup

```sh
# Choose one:
npx skills add https://github.com/haesol-shin/campusctl -g
bunx skills add https://github.com/haesol-shin/campusctl -g
```

This installs the repository's `campusctl` skill to your global agent skills directory. The command requires Node.js or Bun; the `skills` CLI supports selecting an agent and skill explicitly:

```sh
npx skills add https://github.com/haesol-shin/campusctl -g --skill campusctl -a codex
npx skills add https://github.com/haesol-shin/campusctl -g --skill campusctl -a claude-code
bunx skills add https://github.com/haesol-shin/campusctl -g --skill campusctl -a codex
bunx skills add https://github.com/haesol-shin/campusctl -g --skill campusctl -a claude-code
```

Update or remove it with:

```sh
npx skills update campusctl -g
npx skills remove campusctl -g
```

The guided setup in the skill requires campusctl 0.5.0 or newer; installing the skill does not install or update campusctl. The skill teaches agents to use campusctl's JSON CLI for lectures, courses, assignments, notices, and materials. Keep a manual clone at a stable path if you use the symlink or copy instructions below. Codex uses `~/.agents/skills/`; standalone Claude Code uses `~/.claude/skills/`; other agents use their documented user-level skills directory.

## Manual setup

On Windows, open PowerShell and use a stable clone path. A copy avoids Windows symlink permission requirements:

```powershell
New-Item -ItemType Directory -Force "$HOME\src" | Out-Null
git clone https://github.com/haesol-shin/campusctl.git "$HOME\src\campusctl"
New-Item -ItemType Directory -Force "$HOME\.agents\skills\campusctl" | Out-Null
Copy-Item -Path "$HOME\src\campusctl\skills\campusctl\*" -Destination "$HOME\.agents\skills\campusctl" -Recurse -Force
```

Use `$HOME\.claude\skills\campusctl` as the destination for standalone Claude Code. Copies do not update when the clone changes: after `git -C "$HOME\src\campusctl" pull`, repeat the `Copy-Item` command. Restart the agent after adding or updating the skill so it discovers the new files.

On macOS or Linux, clone the repository to a stable location, then symlink the complete skill directory. For Codex:

```bash
mkdir -p "$HOME/src"
git clone https://github.com/haesol-shin/campusctl.git "$HOME/src/campusctl"
mkdir -p "$HOME/.agents/skills"
ln -s "$HOME/src/campusctl/skills/campusctl" "$HOME/.agents/skills/campusctl"
```

For standalone Claude Code, use `~/.claude/skills` instead:

```bash
mkdir -p "$HOME/.claude/skills"
ln -s "$HOME/src/campusctl/skills/campusctl" "$HOME/.claude/skills/campusctl"
```

A symlink reflects updates after you run `git -C "$HOME/src/campusctl" pull`; if you move the clone, recreate the symlink.

## Ask your agent to install the skill

Give your coding agent this prompt. It installs only the skill; it does not install campusctl or configure account credentials.

```text
Install only the campusctl agent skill for my user account.
Clone https://github.com/haesol-shin/campusctl.git to a stable location such as ~/src/campusctl, or update that clone if it already exists.
Put the complete skills/campusctl directory, including its agents subdirectory, in this harness's documented user-level skills directory; prefer a symlink where supported and otherwise copy it.
Do not change unrelated files or global shell configuration.
Check whether `campusctl --version --json` runs and report its result; if campusctl is not already installed or not on PATH, report that instead of installing it.
Do NOT run `campusctl auth set`, ask for or handle passwords, run `campusctl sync`, or run `campusctl lectures play`.
Report the clone path, skill destination, link/copy choice, and verification result, then tell me to restart the agent.
```
