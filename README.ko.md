<h1 align="center">campusctl</h1>

<p align="center">
  <a href="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-%3E%3D3.11%20%7C%20Windows%20%7C%20macOS%20%7C%20Linux-3776AB" alt="Python >=3.11 · Windows · macOS · Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>
<p align="center"><a href="README.md">English</a> | <b>한국어</b></p>
<p align="center">자세한 문서는 영어로 제공됩니다.</p>

<p align="center"><em>터미널이나 AI 에이전트를 통해 캠퍼스 강의 진행 상황을 확인하세요.</em></p>

`campusctl`은 충남대학교(Chungnam National University, CNU) LMS의 강의, 과제, 과목 공지, 자료를 확인하는 로컬 CLI입니다. 현재 CNU LMS만 지원합니다. 목록은 로컬에 보관하며 LMS 작업에는 화면이 표시되는 브라우저를 사용합니다.

## 주요 기능

- 명령 하나로 수강 중인 강의와 강의 항목을 동기화합니다: `campusctl sync`.
- 미완료 강의를 상태 및 마감일과 함께 표시합니다: `campusctl lectures list`.
- 각 항목을 동기화한 뒤 과제 마감일과 제출 여부, 과목 게시판 공지, 내려받을 수 있는 자료를 확인합니다.
- 선택한 자료 하나를 운영체제의 다운로드 폴더 아래 `campusctl/<과목 이름>/`에 저장합니다.
- 한 번에 하나씩 화면이 표시되는 브라우저에서 CNU 공식 플레이어로 강의를 재생하고, 이후 LMS 상태를 확인합니다.
- 스크립트와 에이전트에서 사용할 수 있는 JSON 출력을 제공합니다.
- 기본적으로 운영체제 키링에 비밀번호를 보관합니다.
- 백그라운드 서비스나 상주 브라우저 없이 Windows, macOS, Linux에서 실행합니다.

## 설치

먼저 [uv](https://docs.astral.sh/uv/getting-started/installation/)와 [Git](https://git-scm.com/downloads)을 설치합니다. campusctl에는 Python 3.11 이상이 필요합니다.

### Windows (PowerShell)

```powershell
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

### macOS

```bash
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

### Linux

```bash
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

설치 후 새 터미널을 엽니다. `campusctl setup`을 실행해 Chromium을 설치하거나 설치 여부를 확인합니다. 브라우저가 없으면 다운로드 전에 다음과 같이 묻습니다: “campusctl needs a Chromium browser for playback and sync. Download and install it now? [Y/n]” (재생 및 동기화에 Chromium 브라우저가 필요합니다. 지금 다운로드하여 설치할까요? [Y/n])

Linux에서는 필요할 경우 다음 명령으로 브라우저 시스템 라이브러리를 설치합니다:

```bash
uv tool run --from playwright playwright install-deps chromium
```

이 명령은 관리자 권한을 요구할 수 있습니다. PATH 설정, Linux 시스템 라이브러리, 브라우저 캐시 위치, 버전 고정, 업데이트, 제거 또는 체크아웃 설치는 [설치 안내](docs/installation.md)를 참조하세요. 명령을 찾을 수 없으면 `uv tool update-shell`을 실행하고 새 터미널을 여세요.

## 빠른 시작

campusctl 프롬프트는 영어로 표시되며, 괄호 안 한국어는 설명용 번역입니다. 새 설정을 만들려면 터미널에서 `campusctl config init`을 실행합니다. CNU 로그인 ID(`CNU login ID:`; 한국어 번역: CNU 로그인 ID:)를 묻고, 이어서 비밀번호 저장 여부(`Save your password now? [Y/n]`; 한국어 번역: 비밀번호를 지금 저장할까요? [Y/n]; 기본값 Yes)를 묻습니다. 저장에 동의하면 `LMS password:`(한국어 번역: LMS 비밀번호:)에 입력하며, 입력 내용은 화면에 표시되지 않습니다. 저장하지 않으면 동기화 전에 터미널에서 직접 `campusctl auth set`을 실행하세요. Chromium이 없으면 다음과 같이 묻습니다: `campusctl needs a Chromium browser for playback and sync. Download and install it now? [Y/n]` (한국어 번역: 재생 및 동기화에 Chromium 브라우저가 필요합니다. 지금 다운로드하여 설치할까요? [Y/n]; 기본값 Yes). 설치를 거절하면 동기화 전에 `campusctl setup`을 실행하세요.

```console
$ campusctl config init
$ campusctl sync
$ campusctl lectures list
```

과제·공지·자료는 항목별로 동기화한 다음 목록을 확인하세요. 자료 목록에서 전체 ID를 복사해 원하는 파일 하나만 내려받을 수 있습니다:

```console
$ campusctl sync --only assignments
$ campusctl assignments list
$ campusctl sync --only notices
$ campusctl notices list
$ campusctl sync --only materials
$ campusctl materials list
$ campusctl materials download <ENTITY_ID>
```

파일은 기본적으로 운영체제의 다운로드 폴더 아래 `campusctl/<과목 이름>/`에 저장됩니다. 다른 폴더에 저장하려면 `--out DIR`을 지정하세요. 동기화와 자료 다운로드는 전역 옵션으로 화면 없이 실행할 수 있습니다. 예: `campusctl --headless sync`, `campusctl --headless materials download <ENTITY_ID>`. 로컬 Chromium 프로필에서만 가능하며 CDP 브라우저 세션과 강의 재생에는 사용할 수 없습니다.

스크립트나 에이전트에서 JSON이 필요하면 `--json`을 추가하세요. 터미널에서는 기본적으로 사람이 읽기 쉬운 형식으로 출력합니다.

영어 가짜 카탈로그로 너비 80열에서 `CAMPUSCTL_OUTPUT=human`을 설정해 CLI를 실행하고 캡처한 출력 예시입니다. 마감일은 CNU 현지 시간이며, `-`는 마감일 정보가 없음을 뜻하고 `opens MM-DD`는 아직 열리지 않은 강의를 나타냅니다:

```text
2 lectures (updated 2026-09-24 12:00 UTC)

Course: Introduction to Biology
  Cell Structure  2026-10-15 23:59  opens 10-15
    cnu_lecture:biology-101:lecture-01

Course: World History
  The Roman Republic  -    unfinished
    cnu_lecture:history-201:lecture-07

To play one: campusctl lectures play cnu_lecture:history-201:lecture-07
To refresh: campusctl sync
```

<details>
<summary>브라우저 및 로컬 데이터</summary>

강의 목록과 브라우저 프로필은 campusctl의 로컬 데이터 디렉터리에 보관되며, 명령이 끝나면 로컬 브라우저 컨텍스트가 닫힙니다. 경로와 공유 브라우저 옵션은 [설정 안내](docs/configuration.md)를 참조하세요.

</details>

## AI 에이전트에서 사용하기

`npx skills add https://github.com/haesol-shin/campusctl -g` 또는 `bunx skills add https://github.com/haesol-shin/campusctl -g`를 실행하세요([자세한 내용, 수동 설치 및 복사해 사용할 프롬프트](docs/agent-skill.md)).

## campusctl을 사용하는 이유

- 각 강의를 열기 전에 미완료이거나 기한이 다가오는 강의를 확인할 수 있습니다.
- 브라우저 프로세스를 계속 실행하지 않고 로컬 강의 목록을 새로 고치고 확인할 수 있습니다.
- 재생할 강의는 직접 선택하면서 에이전트가 구조화된 결과를 읽게 할 수 있습니다.
- 재생 결과는 공식 LMS 강의 행에 표시된 상태를 따릅니다. 출석 미반영으로 표시된 강의는 LMS에 표시된 시청 진도가 필요한 전체 시간에 도달한 경우에만 “watched (not counted)”로 표시되며, 이 상태는 현재 재생 명령 이전에 기록된 진도일 수도 있습니다.

## campusctl을 사용하지 말아야 할 때

- campusctl은 재생 중 앞으로 건너뛰거나, 지원되지 않는 속도를 강제로 적용하거나, 진도를 위조하거나, 백그라운드에서 재생하지 않습니다.
- 재생은 화면이 표시되는 브라우저에서 한 번에 하나씩 공식 플레이어를 사용합니다. YouTube는 기본 자동 재생으로 1배속만 사용하며, 다른 미디어는 해당 플레이어가 지원하는 속도로만 재생합니다.
- CNU LMS는 계정당 로그인 세션 하나만 허용하는 것으로 관찰되었습니다. 해당 계정으로 로그인된 다른 자동화와 campusctl을 동시에 실행하지 마세요.
- campusctl은 CNU와 제휴하지 않으며 LMS가 변경되면 작동하지 않을 수 있습니다.

## 업그레이드

v0.2.1에서 업그레이드하려면 `uv tool upgrade campusctl`을 실행하세요. 새 목록을 보기 전에 해당 항목을 `sync --only`로 동기화해야 합니다. v0.1과 달리 v0.2부터 터미널 출력은 사람이 읽기 쉬운 형식이 기본이며, 스크립트나 에이전트에는 `--json`을 추가하세요.

## 문서

- [설정 및 브라우저 세션](docs/configuration.md)
- [무인 Linux 및 서버 설정](docs/configuration.md#unattended-linux)
- [에이전트 스킬 설정](docs/agent-skill.md)
- [문제 해결](docs/troubleshooting.md)
- [CLI 계약](docs/contracts/cli.md)
- [과제](docs/contracts/assignments.md), [공지](docs/contracts/notices.md), [자료](docs/contracts/materials.md) 계약

## 자주 묻는 질문

#### campusctl은 공식 도구인가요?

아니요. 독립 프로젝트이며 CNU와 제휴하지 않습니다.

#### 출석을 처리하나요?

campusctl은 공식 플레이어로 강의를 재생한 뒤 LMS 강의 상태를 읽으며, 진도나 출석을 별도로 요청하지 않습니다. 플레이어가 기록하는 내용은 LMS에 달려 있습니다.

#### 비밀번호와 데이터는 어디에 저장되나요?

기본 비밀번호 제공자는 운영체제 키링입니다. 강의 목록과 브라우저 프로필은 campusctl의 로컬 데이터 디렉터리에 보관됩니다. 경로는 [설정 안내](docs/configuration.md#paths)를 참조하세요.

#### 내려받은 자료는 어디에 저장되나요?

운영체제의 다운로드 폴더 아래 `campusctl/<과목 이름>/`에 저장됩니다. Windows에서 다운로드 폴더 위치를 변경했다면 변경된 위치를 따릅니다. 다른 폴더에 저장하려면 `materials download <ENTITY_ID> --out DIR`을 사용하세요.

#### 과제나 공지의 본문도 볼 수 있나요?

아직은 제목과 상태 등 목록 정보만 제공합니다. 본문 읽기는 v0.4.0에서 제공할 예정입니다. 공지는 각 과목 게시판에서 가져오며, 읽음 여부는 할 일 목록에서 확인된 경우에만 표시합니다. 한 과목의 공지가 10개를 넘어 다음 페이지가 있으면 그 과목의 동기화는 `notice-board-paginated` 오류가 나고 이전 목록이 유지됩니다. 해당 과목은 LMS에서 확인하세요.

## 라이선스

MIT — [LICENSE](LICENSE)를 참조하세요.
