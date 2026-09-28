<h1 align="center">campusctl</h1>

<p align="center">
  <a href="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-%3E%3D3.11%20%7C%20Windows%20%7C%20macOS%20%7C%20Linux-3776AB" alt="Python >=3.11 · Windows · macOS · Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>
<p align="center"><a href="README.md">English</a> | <b>한국어</b></p>

<p align="center"><em>충남대 LMS의 강의·과제·공지·자료를 터미널이나 AI 에이전트에서 확인하세요.</em></p>
<p align="center"><a href="docs/installation.ko.md">설치</a> · <a href="docs/usage.ko.md">사용 안내</a> · <a href="docs/agent-skill.ko.md">AI 스킬</a> · <a href="docs/troubleshooting.ko.md">문제 해결</a></p>

## 주요 기능

- **할 일을 한눈에.** 미완료 강의와 곧 마감되는 과제, 읽지 않은 공지를 한 화면에서 확인합니다.
- **한 번 받아 두고 계속 보기.** 동기화한 목록은 LMS에 다시 접속하지 않고 열 수 있습니다.
- **공식 방식 그대로.** 공식 플레이어로 재생하고 공식 파일만 저장하며 앞으로 건너뛰거나 진도를 위조하지 않습니다.
- **AI 에이전트와 함께.** 에이전트 스킬과 JSON 출력으로 결과를 읽고, 재생할 강의는 사용자가 고릅니다.
- **내 컴퓨터에만 보관.** 비밀번호는 운영체제 키링에, 목록은 로컬 폴더에 저장합니다.
- **가볍게 실행.** Windows, macOS, Linux에서 백그라운드 서비스 없이 필요할 때만 실행합니다.

## AI 에이전트에서 사용하기

코딩 에이전트에게 campusctl 스킬 설치를 요청합니다. 다음 문구를 붙여 넣습니다:

```text
Install only the campusctl agent skill for my user account.
Clone https://github.com/haesol-shin/campusctl.git to a stable location such as ~/src/campusctl, or update that clone if it already exists.
Put the complete skills/campusctl directory, including its agents subdirectory, in this harness's documented user-level skills directory; prefer a symlink where supported and otherwise copy it.
Do not change unrelated files or global shell configuration.
Check whether `campusctl --version --json` runs and report its result; if campusctl is not already installed or not on PATH, report that instead of installing it.
Do NOT run `campusctl auth set`, ask for or handle passwords, run `campusctl sync`, or run `campusctl lectures play`.
Report the clone path, skill destination, link/copy choice, and verification result, then tell me to restart the agent.
```

직접 설치하려면 다음 명령 중 하나를 실행합니다:

```sh
npx skills add https://github.com/haesol-shin/campusctl -g
# or
bunx skills add https://github.com/haesol-shin/campusctl -g
```

스킬은 campusctl 자체를 설치하지 않습니다. 아래 [빠른 시작](#quick-start)을 따릅니다. 수동 설치 방법은 [에이전트 스킬 안내](docs/agent-skill.ko.md)에 있습니다.

## 필요 조건

- [uv](https://docs.astral.sh/uv/getting-started/installation/): campusctl을 설치하고 실행할 때 사용합니다.
- [Git](https://git-scm.com/downloads): 저장소에서 설치할 때 필요합니다.
- Python 3.11 이상: 아직 없다면 uv가 설치할 수 있습니다.

## 설치

Windows(PowerShell), macOS, Linux에서 같은 명령으로 설치합니다:

```sh
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

새 터미널을 열면 `campusctl`을 `PATH`에서 찾을 수 있습니다.

<details>
<summary>Linux 브라우저 시스템 라이브러리</summary>

```bash
uv tool run --from playwright playwright install-deps chromium
```

운영체제 패키지를 설치하므로 관리자 권한이 필요할 수 있습니다.

</details>

PATH 문제, 브라우저 라이브러리, 버전 고정, 업데이트, 체크아웃 설치는 [설치 안내](docs/installation.ko.md)를 참고합니다.

<a id="quick-start"></a>
## 빠른 시작

### 1. 첫 설정

대화형 터미널에서 설정합니다. 비밀번호는 화면에 표시되지 않고 운영체제 키링에 저장하며 첫 동기화는 선택 사항입니다.

```sh
campusctl setup
```

### 2. 동기화와 확인

설정 중 동기화하지 않았다면 목록을 새로 고친 뒤 할 일을 확인합니다.

```sh
campusctl sync
campusctl status
```

예시 과목에서 `campusctl lectures list`를 실행한 결과입니다. 마감일은 CNU 현지 시간입니다. `-`는 마감일 정보가 없다는 뜻이고, `opens MM-DD`는 아직 열리지 않은 강의를 뜻합니다:

```text
2 lectures (updated 2026-09-28 18:09 UTC)

Course: Example Course
  Welcome lecture  -    unfinished
    cnu_lecture:example-course:welcome-01

Course: Practice Course
  Later lecture  2026-10-20 23:59  opens 10-15
    cnu_lecture:practice-course:later-01

To play one: campusctl lectures play cnu_lecture:example-course:welcome-01
To refresh: campusctl sync
Catalog generated 1 second ago.
```

### 3. 자세히 보기와 다운로드

선택한 공지의 전체 ID로 본문을 가져오거나 자료 목록의 번호로 파일을 내려받습니다.

```sh
campusctl notices fetch ID
campusctl materials download 1
```

`1`은 마지막 `materials list`의 번호이며 전체 ID는 목록 없이도 사용할 수 있습니다. 자세한 내용은 [사용 안내](docs/usage.ko.md)를 참고합니다.

## 자주 쓰는 명령

| 작업 | 명령 |
| --- | --- |
| 네 가지 목록 새로 고침 | `campusctl sync` |
| 학습 현황 확인 | `campusctl status` |
| 남은 강의 보기 | `campusctl lectures list` |
| 선택한 강의 재생 | `campusctl lectures play ID` |
| 과목 목록 보기 | `campusctl courses list` |
| 과제 목록 보기 | `campusctl assignments list` |
| 선택한 과제 읽기 | `campusctl assignments fetch ID` |
| 공지 목록 보기 | `campusctl notices list` |
| 선택한 공지 읽기 | `campusctl notices fetch ID` |
| 자료 목록 보기 | `campusctl materials list` |
| 선택한 자료 내려받기 | `campusctl materials download ID` |

<details>
<summary>브라우저 및 로컬 데이터</summary>

강의 목록과 브라우저 프로필은 campusctl의 로컬 데이터 폴더에 보관합니다. 명령이 끝나면 로컬 브라우저 세션이 닫힙니다. 경로와 공유 브라우저 설정은 [설정 안내](docs/configuration.ko.md)를 참고합니다.

</details>

## 한계

- 앞으로 건너뛰거나 진도를 위조하거나 백그라운드에서 재생하지 않습니다. 화면에 보이는 공식 플레이어에서 한 번에 하나씩 재생합니다.
- CNU LMS는 계정당 로그인 세션 하나만 허용하는 것으로 관찰되었습니다. 같은 계정의 다른 로그인 자동화와 campusctl을 함께 실행하면 안 됩니다.
- campusctl은 충남대와 제휴하지 않은 독립 프로젝트로 충남대 LMS만 지원합니다. LMS가 바뀌면 작동하지 않을 수 있습니다.

## 업그레이드

업데이트할 때는 `uv tool upgrade campusctl`을 실행합니다. `campusctl sync`는 네 영역을 갱신하며 `--only`로 범위를 좁힐 수 있습니다.

## 문서

- [설치 안내](docs/installation.ko.md)
- [사용법과 명령](docs/usage.ko.md)
- [설정 및 브라우저 세션](docs/configuration.ko.md)
- [무인 Linux 및 서버 설정](docs/configuration.ko.md#unattended-linux)
- [에이전트 스킬 설정](docs/agent-skill.ko.md)
- [문제 해결](docs/troubleshooting.ko.md)
- [CLI 계약](docs/contracts/cli.md)
- [과제](docs/contracts/assignments.md), [공지](docs/contracts/notices.md), [자료](docs/contracts/materials.md) 계약
- [기여 안내](CONTRIBUTING.md)

## 자주 묻는 질문

#### campusctl은 공식 도구인가요?

공식 도구가 아닙니다. 충남대와 제휴하지 않은 독립 프로젝트입니다.

#### 출석을 처리하나요?

campusctl은 공식 플레이어로 강의를 재생한 뒤 LMS 강의 상태를 읽으며, 진도나 출석을 별도로 요청하지 않습니다. 플레이어가 기록하는 내용은 LMS에 달려 있습니다.

#### 비밀번호와 데이터는 어디에 저장되나요?

기본적으로 비밀번호는 운영체제 키링에 저장합니다. 강의 목록과 브라우저 프로필은 campusctl의 로컬 데이터 폴더에 보관합니다. 경로는 [설정 안내](docs/configuration.ko.md#paths)를 참고합니다.

#### 내려받은 자료는 어디에 저장되나요?

운영체제의 다운로드 폴더 아래 `campusctl/<과목 이름>/`에 저장됩니다. Windows에서 다운로드 폴더 위치를 변경했다면 변경된 위치를 따릅니다. 다른 폴더에 저장하려면 `campusctl materials download <ENTITY_ID> --out DIR`을 사용합니다.

#### 과제나 공지의 본문도 볼 수 있나요?

네, `assignments list`나 `notices list`에서 전체 ID를 골라 하나 이상 가져올 수 있습니다. 패키지 저장, JSON 결과, 공지 읽음 상태는 [사용 안내](docs/usage.ko.md)를 참고합니다.

## 라이선스

MIT — [LICENSE](LICENSE)를 참고합니다.
