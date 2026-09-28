<h1 align="center">campusctl</h1>

<p align="center">
  <a href="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/haesol-shin/campusctl/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
  <img src="https://img.shields.io/badge/Python-%3E%3D3.11%20%7C%20Windows%20%7C%20macOS%20%7C%20Linux-3776AB" alt="Python >=3.11 · Windows · macOS · Linux">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
</p>
<p align="center"><a href="README.md">English</a> | <b>한국어</b></p>

<p align="center"><em>터미널이나 AI 에이전트에서 강의·과제·공지·자료를 확인하세요.</em></p>

`campusctl`로 강의와 과제, 공지, 자료를 한곳에서 확인할 수 있습니다. 현재 충남대 LMS만 지원합니다.

## 주요 기능

- **학습 정보를 새로 고칩니다.** `campusctl sync`로 강의·과제·공지·자료 목록을 한 번에 갱신합니다.
- **지금 할 일을 확인합니다.** `campusctl status`로 현황을 보고 `campusctl lectures list`로 남은 강의를 찾습니다.
- **필요한 목록만 갱신합니다.** `campusctl sync --only lectures`로 강의만 새로 고치거나 목록 명령에 `--refresh`를 붙입니다. 다른 영역은 [사용 안내](docs/usage.ko.md)를 참고합니다.
- **선택한 내용을 읽습니다.** `campusctl assignments fetch ID`나 `campusctl notices fetch ID`로 전체 ID 하나 이상을 가져옵니다. 읽기 쉬운 패키지로 저장합니다. 공지를 열면 조회수나 읽음 상태가 바뀔 수 있습니다.
- **자료를 저장합니다.** `campusctl materials download ID`로 파일 하나를 다운로드 폴더의 `campusctl/<과목 이름>/` 아래에 저장합니다.
- **강의를 재생합니다.** `campusctl lectures play ID`로 화면에 보이는 공식 플레이어에서 한 번에 하나씩 재생합니다. 종료 후 LMS 상태를 확인합니다.
- **스크립트와 에이전트에서 사용합니다.** `--json`을 붙이면 구조화된 결과를 받습니다.
- **비밀번호를 안전하게 보관합니다.** `campusctl auth set`으로 운영체제 키링에 저장합니다.
- **필요할 때 실행합니다.** Windows, macOS, Linux에서 백그라운드 서비스나 상주 브라우저 없이 사용합니다.

## 설치

먼저 [uv](https://docs.astral.sh/uv/getting-started/installation/)와 [Git](https://git-scm.com/downloads)을 설치합니다. campusctl에는 Python 3.11 이상이 필요합니다.

Windows(PowerShell), macOS, Linux에서 같은 명령으로 설치합니다:

```sh
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

새 터미널을 열고 `campusctl setup`을 실행합니다. 계정과 브라우저를 설정하고 비밀번호를 화면에 표시하지 않은 채 입력할 수 있습니다. 첫 동기화는 선택 사항입니다.

Linux에서는 필요할 경우 다음 명령으로 브라우저 시스템 라이브러리를 설치합니다:

```bash
uv tool run --from playwright playwright install-deps chromium
```

이 명령은 관리자 권한이 필요할 수 있습니다. PATH 설정, Linux 시스템 라이브러리, 브라우저 캐시, 버전 고정, 업데이트, 제거, 체크아웃 설치는 [설치 안내](docs/installation.ko.md)를 참고합니다. `campusctl` 명령을 찾지 못한다면 `uv tool update-shell`을 실행하고 새 터미널을 엽니다.

<a id="quick-start"></a>
## 빠른 시작

터미널에서 안내에 따라 설정합니다. 비밀번호 입력은 화면에 표시되지 않고 운영체제 키링에 저장합니다. 첫 동기화는 선택 사항입니다.

```console
# First-time setup
$ campusctl setup
$ campusctl sync
# Check your coursework
$ campusctl status
$ campusctl lectures list
$ campusctl courses list
$ campusctl assignments list
$ campusctl notices list
$ campusctl materials list
# Details and downloads
$ campusctl assignments fetch <ASSIGNMENT_ENTITY_ID>
$ campusctl notices fetch <NOTICE_ENTITY_ID>
$ campusctl materials download 1
```

설정 중 이미 동기화했다면 `campusctl sync`는 건너뜁니다. 다운로드 명령의 `1`은 `campusctl materials list`에 표시된 번호입니다. 원하는 자료의 번호로 바꿔 실행합니다.

다음은 예시 과목으로 `CAMPUSCTL_OUTPUT=human`을 설정하고 너비 80열에서 `campusctl lectures list`를 실행해 캡처한 결과입니다. 마감일은 CNU 현지 시간입니다. `-`는 마감일 정보가 없다는 뜻이고, `opens MM-DD`는 아직 열리지 않은 강의를 뜻합니다:

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
Catalog generated 1 seconds ago.
```

<details>
<summary>브라우저 및 로컬 데이터</summary>

강의 목록과 브라우저 프로필은 campusctl의 로컬 데이터 폴더에 보관합니다. 명령이 끝나면 로컬 브라우저 세션이 닫힙니다. 경로와 공유 브라우저 설정은 [설정 안내](docs/configuration.ko.md)를 참고합니다.

</details>

## AI 에이전트에서 사용하기

다음 명령 중 하나로 스킬을 전역 설치합니다:

```sh
npx skills add https://github.com/haesol-shin/campusctl -g
# or
bunx skills add https://github.com/haesol-shin/campusctl -g
```

수동 설치 방법과 복사해 쓸 문구는 [에이전트 스킬 안내](docs/agent-skill.ko.md)에 있습니다.

## campusctl을 사용하는 이유

- 각 강의를 열기 전에 미완료이거나 기한이 다가오는 강의를 확인할 수 있습니다.
- 브라우저 프로세스를 계속 실행하지 않고 로컬 강의 목록을 새로 고치고 확인할 수 있습니다.
- 재생할 강의는 직접 선택하면서 에이전트가 구조화된 결과를 읽게 할 수 있습니다.
- 재생 결과는 공식 LMS의 강의 상태를 따릅니다. 시청 시간이 기준에 도달해야 `watched (not counted)`로 표시되며, 이전에 시청한 시간도 포함될 수 있습니다. 출석 인정은 보장하지 않습니다.

## campusctl을 사용하지 말아야 할 때

- campusctl은 재생 중 앞으로 건너뛰거나, 지원되지 않는 속도를 강제로 적용하거나, 진도를 위조하거나, 백그라운드에서 재생하지 않습니다.
- 재생은 화면이 표시되는 브라우저에서 한 번에 하나씩 공식 플레이어를 사용합니다. YouTube는 기본 자동 재생으로 1배속만 사용하며, 다른 미디어는 해당 플레이어가 지원하는 속도로만 재생합니다.
- CNU LMS는 계정당 로그인 세션 하나만 허용하는 것으로 관찰되었습니다. 같은 계정의 다른 로그인 자동화와 campusctl을 함께 실행하면 안 됩니다.
- campusctl은 CNU와 제휴하지 않으며 LMS가 변경되면 작동하지 않을 수 있습니다.

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

## 기여: 스캔 예외

스캔 예외를 신청하려면 [기여 안내](CONTRIBUTING.md#scan-exceptions)를 참고합니다.

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

MIT — [LICENSE](LICENSE)를 참조하세요.
