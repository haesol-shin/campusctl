[English](troubleshooting.md) | **한국어**

# 문제 해결

JSON의 `errors` 코드나 사람이 읽는 출력의 메시지에 맞춰 다음 단계를 찾으세요. 출력 형식, 응답 필드, 종료 코드는 [CLI 계약](contracts/cli.md)에 정리되어 있습니다.

| 오류 코드 | 해결 방법 |
| --- | --- |
| `config-missing` | `campusctl config init`으로 `doctor`가 알려준 위치에 설정 파일을 만드세요. 폴더를 바꾸려면 `CAMPUSCTL_CONFIG_DIR`을 사용하세요. |
| `config-exists` | 오류에 나온 기존 설정 파일을 수정하거나 옮긴 뒤 `campusctl config init`을 다시 실행하세요. |
| `credentials-not-configured` | 터미널에서 `campusctl auth set`을 실행해 비밀번호를 안전한 운영체제 키링에 저장하세요. |
| `credential-backend-insecure` | 안전한 운영체제 키링을 선택하세요. 평문 저장소나 파일 기반 저장소로 바꾸지 마세요. |
| `credential-backend-unavailable` | 사용자 세션에서 안전한 키링을 사용할 수 있게 한 뒤 `campusctl auth status --check`를 실행하세요. |
| `credential-helper-failed` | 비밀값을 출력하지 말고 도우미의 인자, 인터프리터, JSON 표준 출력, 사용자 이름 일치 여부, 30초 제한을 확인하세요. |
| `display-unavailable` | 로그인된 데스크톱 세션을 사용하거나 Xvfb 아래에서 브라우저 명령을 실행하세요. |
| `browser-not-installed` | `campusctl setup`으로 브라우저를 설치하세요. |
| `browser-install-failed` | `campusctl setup`을 다시 실행하고 네트워크와 바이러스 백신 설정을 확인하세요. 수동 대안은 `playwright install chromium`입니다(체크아웃에서는 `uv run playwright install chromium`). |
| `browser-endpoint-unreachable` | 설정한 브라우저를 시작하고 `browser.cdp_endpoint`를 확인하세요. CDP 모드는 로컬 브라우저로 자동 전환하지 않습니다. |
| `catalog-missing` | `campusctl sync --only lectures`로 목록을 만드세요. |
| `catalog-outdated` | `campusctl sync --only lectures`로 목록을 새로 고친 뒤 다시 시도하세요. |
| `login-failed` | 계정 이름과 자격 증명을 확인하세요. `auth status --check`는 자격 증명 사용 가능 여부만 확인하며 CNU 로그인이 되는지는 확인하지 않습니다. |
| `login-action-required` | 일반 브라우저에서 로그인해 약관에 동의하거나 필요한 비밀번호 변경을 마친 뒤 다시 시도하세요. |
| `lms-unavailable` | LMS와 네트워크 연결을 확인하고 서비스가 돌아오면 다시 시도하세요. |
| `course-sync-failed` | 선택한 과목의 할 일, 자료실 또는 공지 게시판이 로드된 뒤 다시 시도하세요. 동기화는 이동 후 목록 응답과 현재 과목을 확인한 다음 항목을 반영합니다. |
| `course-discovery-failed` | LMS에서 수강 과목 목록이 로드되는지 확인한 뒤 다시 시도하세요. 설정한 데이터 폴더의 `diagnostics/roster-*.json` 중 가장 최신 비공개 구조 진단을 확인하세요. 과목 이름이나 자격 증명은 없지만, 공유하기 전에 URL 경로를 검토하세요. |
| `session-busy` (상태 `busy`, 종료 75) | 다른 작업이 브라우저 잠금을 사용 중입니다. 끝날 때까지 기다리고 자동으로 재시도하거나 사용 중인 잠금을 지우지 마세요. |

Windows에서 Playwright 브라우저가 격리되었다면 Windows 보안에서 해당 브라우저만 허용하세요. Defender나 SmartScreen을 끄지 마세요. `.ps1` 도우미가 실행 정책에 막히면 그 도우미를 실행하는 PowerShell 인자에만 `-ExecutionPolicy Bypass`를 넣고 전역 설정은 바꾸지 마세요. 도우미가 길거나 비ASCII 문자가 들어간 경로에서 실패하면 더 짧은 경로를 시도하고, 실행 파일과 각 인자를 별도의 TOML 배열 항목으로 유지하세요.
