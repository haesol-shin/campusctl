[English](agent-skill.md) | **한국어**

# 에이전트 스킬 설치

```sh
# Choose one:
npx skills add https://github.com/haesol-shin/campusctl -g
bunx skills add https://github.com/haesol-shin/campusctl -g
```

이 명령은 저장소의 `campusctl` 스킬을 에이전트의 전역 스킬 폴더에 설치합니다. Node.js 또는 Bun이 필요하며, `skills` CLI에서 에이전트와 스킬을 직접 지정할 수도 있습니다:

```sh
npx skills add https://github.com/haesol-shin/campusctl -g --skill campusctl -a codex
npx skills add https://github.com/haesol-shin/campusctl -g --skill campusctl -a claude-code
bunx skills add https://github.com/haesol-shin/campusctl -g --skill campusctl -a codex
bunx skills add https://github.com/haesol-shin/campusctl -g --skill campusctl -a claude-code
```

업데이트하거나 제거하려면 다음 명령을 사용하세요:

```sh
npx skills update campusctl -g
npx skills remove campusctl -g
```

스킬의 안내형 설정에는 campusctl 0.6.0 이상이 필요합니다. 스킬을 설치해도 campusctl 자체가 설치되거나 업데이트되지는 않습니다. 이 스킬은 강의, 과목, 과제, 공지, 자료를 campusctl의 JSON CLI로 다루도록 에이전트에 안내합니다. 아래의 링크나 복사 방법을 쓴다면 저장소 복제본을 옮기지 않을 위치에 보관하세요. Codex는 `~/.agents/skills/`, 단독 Claude Code는 `~/.claude/skills/`를 사용하며 다른 에이전트는 각자의 문서에 나온 사용자 스킬 폴더를 사용합니다.

처음 사용한다면 [README 빠른 시작](../README.ko.md#quick-start)을 먼저 보세요.

## 수동 설치

Windows에서는 PowerShell을 열고 복제본을 오래 사용할 위치에 두세요. 복사 방식은 Windows의 심볼릭 링크 권한 문제를 피할 수 있습니다:

```powershell
New-Item -ItemType Directory -Force "$HOME\src" | Out-Null
git clone https://github.com/haesol-shin/campusctl.git "$HOME\src\campusctl"
New-Item -ItemType Directory -Force "$HOME\.agents\skills\campusctl" | Out-Null
Copy-Item -Path "$HOME\src\campusctl\skills\campusctl\*" -Destination "$HOME\.agents\skills\campusctl" -Recurse -Force
```

단독 Claude Code에서는 설치 위치를 `$HOME\.claude\skills\campusctl`로 바꾸세요. 복사본은 저장소가 바뀌어도 자동으로 업데이트되지 않습니다. `git -C "$HOME\src\campusctl" pull`을 실행한 뒤 `Copy-Item` 명령을 다시 실행하세요. 스킬을 추가하거나 업데이트한 뒤에는 에이전트를 재시작해야 새 파일을 찾습니다.

macOS나 Linux에서는 저장소를 안정적인 위치에 복제한 뒤 스킬 폴더 전체를 심볼릭 링크로 연결하세요. Codex에서는:

```bash
mkdir -p "$HOME/src"
git clone https://github.com/haesol-shin/campusctl.git "$HOME/src/campusctl"
mkdir -p "$HOME/.agents/skills"
ln -s "$HOME/src/campusctl/skills/campusctl" "$HOME/.agents/skills/campusctl"
```

단독 Claude Code에서는 대신 `~/.claude/skills`를 사용하세요:

```bash
mkdir -p "$HOME/.claude/skills"
ln -s "$HOME/src/campusctl/skills/campusctl" "$HOME/.claude/skills/campusctl"
```

`git -C "$HOME/src/campusctl" pull`로 저장소를 업데이트하면 심볼릭 링크에도 반영됩니다. 복제본을 옮겼다면 링크를 다시 만드세요.

## 에이전트에게 스킬 설치 요청하기

코딩 에이전트에게 아래 문구를 전달할 수 있습니다. 스킬만 설치하며 campusctl이나 계정 자격 증명은 설정하지 않습니다.

```text
Install only the campusctl agent skill for my user account.
Clone https://github.com/haesol-shin/campusctl.git to a stable location such as ~/src/campusctl, or update that clone if it already exists.
Put the complete skills/campusctl directory, including its agents subdirectory, in this harness's documented user-level skills directory; prefer a symlink where supported and otherwise copy it.
Do not change unrelated files or global shell configuration.
Check whether `campusctl --version --json` runs and report its result; if campusctl is not already installed or not on PATH, report that instead of installing it.
Do NOT run `campusctl auth set`, ask for or handle passwords, run `campusctl sync`, or run `campusctl lectures play`.
Report the clone path, skill destination, link/copy choice, and verification result, then tell me to restart the agent.
```
