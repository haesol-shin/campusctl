[English](README.md) | **한국어**
# campusctl

**충남대 LMS 강의와 과제, 공지, 자료를 터미널이나 AI 에이전트에서 확인하세요.**

`campusctl`은 일반 브라우저로 LMS에 로그인해 강의·과제·공지·자료 목록을 로컬에 받아 두는 CLI입니다. 현재 충남대 LMS만 지원합니다.

## 30초 미리보기: `campusctl status`

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
## 빠른 시작

1. **설치.** 먼저 [uv](https://docs.astral.sh/uv/getting-started/installation/)와 [Git](https://git-scm.com/downloads)을 설치하세요(Python 3.11 이상). Windows, macOS, Linux에서 다음 명령 하나로 설치합니다:

   ```sh
   uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
   ```

   `uv tool update-shell`을 실행하고 새 터미널을 여세요. 그래야 `campusctl`을 `PATH`에서 찾을 수 있습니다.

   <details>
   <summary>Linux 브라우저 시스템 라이브러리</summary>

   ```sh
   uv tool run --from playwright playwright install-deps chromium
   ```

   </details>

2. **설정.** 대화형 터미널에서 `campusctl setup`을 실행하세요. 계정과 Chromium 설정을 안내하고, 비밀번호는 화면에 보이지 않게 입력해 키링에 저장하도록 도와줍니다. 첫 동기화는 선택 사항입니다.
3. **확인.** `campusctl status`로 확인하세요. 설정 중 동기화를 건너뛰었다면 먼저 `campusctl sync`를 실행하세요.

## 자주 쓰는 작업

| 작업 | 명령 |
| --- | --- |
| 네 가지 목록 새로 고침 | `campusctl sync` |
| 남은 강의 보기 | `campusctl lectures list` |
| 선택한 강의 재생 | `campusctl lectures play ID` |
| 과제 목록 보기 | `campusctl assignments list` |
| 선택한 과제 자세히 보기 | `campusctl assignments fetch ID` |
| 공지 목록 보기 | `campusctl notices list` |
| 선택한 공지 자세히 보기 | `campusctl notices fetch ID` |
| 자료 목록 보기 | `campusctl materials list` |
| 자료 하나 내려받기 | `campusctl materials download ID` |

## AI 에이전트

스킬을 전역 설치하려면 `npx skills add https://github.com/haesol-shin/campusctl -g` 또는 `bunx skills add https://github.com/haesol-shin/campusctl -g`를 실행하세요. 자세한 방법은 [에이전트 스킬 설치 안내](docs/agent-skill.ko.md)를 참고하세요.

## 알아두면 좋아요

- 목록과 `status`는 로컬에 저장된 정보를 읽어 오프라인에서도 볼 수 있습니다. 새 정보가 필요하면 `--refresh` 또는 `campusctl sync`를 사용하세요.
- 강의 재생은 화면에 보이는 공식 플레이어에서 한 번에 하나씩만 진행합니다. 명령이 끝나면 로컬 브라우저 세션이 닫힙니다.
- 비밀번호는 운영체제 키링에, 목록은 로컬 데이터 폴더에 저장됩니다. 자세한 내용은 [설정 안내](docs/configuration.ko.md)를 보세요.
- 공지 상세를 열면 조회수가 늘거나 읽음 상태가 바뀔 수 있습니다.

## 자주 묻는 질문

- **공식 도구인가요?** 아니요. 충남대와 제휴하지 않은 독립 프로젝트입니다.
- **출석이 처리되나요?** 공식 플레이어로 재생한 뒤 LMS 상태를 확인하지만, 무엇이 출석으로 인정되는지는 LMS에 달려 있습니다. [사용 안내](docs/usage.ko.md)를 보세요.
- **비밀번호와 데이터는 어디에 저장되나요?** 운영체제 키링과 campusctl의 로컬 데이터 폴더에 저장됩니다. [설정 안내](docs/configuration.ko.md#paths)를 보세요.
- **다운로드한 자료는 어디에 있나요?** 운영체제 다운로드 폴더의 `campusctl/<course label>/` 아래에 저장되며, `--out DIR`로 다른 위치를 고를 수 있습니다. [사용 안내](docs/usage.ko.md)를 보세요.
- **과제나 공지 본문도 볼 수 있나요?** 네, 목록에서 선택한 전체 ID를 `fetch`로 가져올 수 있습니다. [사용 안내](docs/usage.ko.md)를 보세요.

## 문서
[설치](docs/installation.ko.md) · [사용 안내](docs/usage.ko.md) · [설정](docs/configuration.ko.md) · [문제 해결](docs/troubleshooting.ko.md) · [에이전트 스킬](docs/agent-skill.ko.md) · [CLI 계약](docs/contracts/cli.md) · [MIT 라이선스](LICENSE)
