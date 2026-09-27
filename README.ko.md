<h1 align="center">campusctl</h1>

<p align="center">
  <a href="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-%3E%3D3.11%20%7C%20Windows%20%7C%20macOS%20%7C%20Linux-3776AB" alt="Python >=3.11 · Windows · macOS · Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>
<p align="center"><a href="README.md">English</a> | <b>한국어</b></p>
<p align="center">자세한 문서는 영어로 제공됩니다.</p>

<p align="center"><em>터미널이나 AI 에이전트를 통해 캠퍼스 강의 진행 상황을 확인하세요.</em></p>

`campusctl`은 충남대학교(Chungnam National University, CNU) LMS의 강의, 과제, 과목 공지, 자료를 확인하는 로컬 CLI입니다. 현재 CNU LMS만 지원합니다. 목록은 로컬에 보관하고 LMS 작업에는 Chromium을 사용합니다.

## 주요 기능

- `campusctl sync` 한 번으로 네 영역의 자료를 함께 동기화하며 과목당 한 번 선택합니다.
- `campusctl lectures list`로 미완료 강의를, `campusctl status`로 과제·공지·강의 현황을 확인합니다.
- `sync --only DOMAIN`으로 영역을 좁히거나 목록의 `--refresh`로 해당 영역만 새로 고칩니다.
- 전체 ID로 선택한 과제 또는 공지 하나의 본문을 읽기 쉬운 패키지로 가져옵니다. 공지를 열면 조회수와 읽음 상태가 바뀔 수 있습니다.
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

설치 후 새 터미널을 열고 `campusctl setup`으로 설정, Chromium 설치, 자격 증명, 선택적 첫 동기화를 순서대로 진행하세요. 비대화형 또는 JSON 모드에서는 브라우저만 확인·설치하므로 `campusctl config init --username ID`와 `campusctl auth set`을 별도로 사용하세요.

Linux에서는 필요할 경우 다음 명령으로 브라우저 시스템 라이브러리를 설치합니다:

```bash
uv tool run --from playwright playwright install-deps chromium
```

이 명령은 관리자 권한을 요구할 수 있습니다. PATH 설정, Linux 시스템 라이브러리, 브라우저 캐시 위치, 버전 고정, 업데이트, 제거 또는 체크아웃 설치는 [설치 안내](docs/installation.md)를 참조하세요. 명령을 찾을 수 없으면 `uv tool update-shell`을 실행하고 새 터미널을 여세요.

## 빠른 시작

터미널에서 안내에 따라 설정하세요. 비밀번호 입력은 화면에 표시되지 않으며 기본적으로 운영체제 키링에 저장됩니다. 첫 전체 동기화는 선택 사항입니다.

```console
$ campusctl setup
$ campusctl sync
$ campusctl status
$ campusctl lectures list
$ campusctl courses list
$ campusctl assignments list
$ campusctl notices list
$ campusctl assignments fetch <ASSIGNMENT_ENTITY_ID>
$ campusctl notices fetch <NOTICE_ENTITY_ID>
$ campusctl materials list
$ campusctl materials download 1
```

`sync`는 한 브라우저 세션에서 강의·과제·공지·자료를 모두 갱신하고 과목당 한 번 선택합니다. `--only lectures,notices`로 영역을 제한할 수 있습니다. 과목이나 영역이 실패하면 이전 목록은 유지하고 부분 실패를 표시합니다. 목록은 `--refresh`를 지정하지 않으면 네트워크를 사용하지 않습니다. `campusctl --profile sync`는 단계별 시간을 표준 오류 출력으로 보냅니다.

사람이 읽는 출력에서 `courses list`의 번호나 고유한 과목 이름 일부를 `--course`에 지정할 수 있습니다. `materials list`의 번호는 `materials download 번호`에 쓰며, 인자 없이 실행하면 대화형 터미널에서 파일 한 개를 선택합니다. 번호는 마지막으로 출력한 목록에 묶여 있어 카탈로그가 바뀌면 무효가 됩니다. 전체 ID는 목록 기록 없이 사용할 수 있으며 `--json`에서는 전체 ID가 필요합니다. 선택한 공식 첨부파일 한 개는 운영체제 다운로드 폴더의 `campusctl/<과목 이름>/`에 저장됩니다. 다른 위치는 `--out DIR`을 사용하세요.

전역 `--headless`와 `--headed`는 명령 앞에 지정하며 `browser.headless` 설정보다 우선합니다. 기본값은 화면 표시입니다. `campusctl --headless sync`와 `campusctl --headless materials download <ENTITY_ID>`는 로컬 Chromium 프로필에서 실행합니다. CDP 세션에는 headless 모드를 사용할 수 없고 공식 플레이어 재생은 화면 표시 모드만 지원합니다.

스크립트나 에이전트에서는 `--json`을 추가하세요. 터미널에서는 기본적으로 사람이 읽기 쉬운 형식으로 출력합니다.

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

업데이트하려면 `uv tool upgrade campusctl`을 실행하세요. `campusctl sync`는 네 영역을 갱신하며 `--only`로 범위를 좁힐 수 있습니다.

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

네. `assignments list` 또는 `notices list`에서 전체 ID를 고른 뒤 `campusctl assignments fetch <ENTITY_ID>`나 `campusctl notices fetch <ENTITY_ID>`를 실행하세요. 로컬 데이터 디렉터리의 `sources/` 아래에 `content.md`와 `package.json`이 생성됩니다. `--out DIR`은 아직 존재하지 않는 패키지 디렉터리를 지정합니다. `--json`으로 경로, 완전성 및 생략된 리소스를 확인할 수 있습니다. 공지 상세를 열면 조회수가 한 번 증가하거나 읽음 상태가 바뀔 수 있지만, fetch는 읽음 처리 버튼을 누르지 않습니다. 공지 첨부파일은 내려받지 않고 생략합니다. Fetch는 화면 표시 브라우저가 필요하며 과제를 제출하지 않습니다. 자세한 내용은 [과제](docs/contracts/assignments.md#selected-detail-fetch) 및 [공지](docs/contracts/notices.md#selected-detail-fetch) 계약을 참조하세요. 공지는 각 과목 게시판에서 가져오고, 읽음 여부는 할 일 목록과 일치할 때만 표시합니다. 게시판에 다음 페이지가 있으면 `notice-board-paginated` 오류와 함께 그 과목의 이전 목록이 유지됩니다. 해당 과목은 LMS에서 확인하세요.

## 라이선스

MIT — [LICENSE](LICENSE)를 참조하세요.
