[English](configuration.md) | **한국어**

# 설정

`campusctl`은 계정 이름과 실행 설정을 로컬 TOML 파일에 저장합니다. 비밀번호는 기본적으로 운영체제 키링에 보관합니다. `config.toml`, 명령 인자, 환경 변수에는 비밀번호를 넣지 마세요.

<a id="paths"></a>
## 경로

| 운영체제 | 설정 폴더 | 데이터 폴더 |
| --- | --- | --- |
| Windows | `%APPDATA%\campusctl` | `%LOCALAPPDATA%\campusctl` |
| macOS | `~/Library/Application Support/campusctl` | `~/Library/Application Support/campusctl` |
| Linux | `$XDG_CONFIG_HOME/campusctl` 또는 `~/.config/campusctl` | `$XDG_DATA_HOME/campusctl` 또는 `~/.local/share/campusctl` |

설정 파일은 설정 폴더 안의 `config.toml`입니다. 폴더를 바꾸려면 각각 `CAMPUSCTL_CONFIG_DIR` 또는 `CAMPUSCTL_DATA_DIR`을 설정하세요. `XDG_CONFIG_HOME`이나 `XDG_DATA_HOME`에 상대 경로를 넣으면 운영체제 기본 경로를 사용합니다. 가능한 곳에서는 폴더를 POSIX 모드 `0700`으로 만들지만, Windows ACL은 설정하거나 검사하지 않습니다.

## `config.toml`

처음 설정 파일을 만들려면:

```sh
campusctl config init
```

`config init`은 결정된 위치에 `config.toml`을 만들며 기존 파일을 덮어쓰지 않습니다. 입력과 출력이 모두 터미널이고 사람이 읽는 출력 형식일 때만 CNU 로그인 ID와 비밀번호 저장 여부(기본값 예)를 물으며 비밀번호 입력은 화면에 보이지 않습니다. 이 대화형 과정에서 로컬 관리 브라우저가 없으면 Chromium 설치도 제안합니다(기본값 예). 그 외에는 `campusctl config init --username YOUR_LOGIN_ID --json`처럼 로그인 ID를 직접 전달하세요. 새 파일을 만들 때 ID를 빼면 잘못된 사용으로 종료 코드 2가 반환됩니다. 기존 파일은 먼저 확인하며, 파일을 다시 쓰지 않고 비밀번호 저장을 제안할 수도 있습니다.

터미널 출력은 기본적으로 사람이 읽기 쉽고, 출력을 다른 곳으로 보내면 JSON이 됩니다. `--json`은 언제나 JSON을 선택합니다. `CAMPUSCTL_OUTPUT=json`이나 `CAMPUSCTL_OUTPUT=human`은 자동 선택을 덮어쓸 수 있으며 다른 값은 무시됩니다. 환경 변수만으로 질문이 나타나지는 않습니다. 질문에는 입력과 출력이 모두 터미널이어야 합니다.

설정을 직접 수정하거나 `config init`을 사용할 수 없다면 위 표의 운영체제별 경로를 사용하세요:

- **Windows (PowerShell):** `New-Item -ItemType Directory -Force "$env:APPDATA\campusctl"`을 실행한 뒤 `notepad "$env:APPDATA\campusctl\config.toml"`로 여세요.
- **macOS:** `mkdir -p "$HOME/Library/Application Support/campusctl"`을 실행한 뒤 `${EDITOR:-vi} "$HOME/Library/Application Support/campusctl/config.toml"`로 여세요.
- **Linux (기본 경로):** `mkdir -p "$HOME/.config/campusctl"`을 실행한 뒤 `${EDITOR:-vi} "$HOME/.config/campusctl/config.toml"`로 여세요.

`CAMPUSCTL_CONFIG_DIR`이나 `XDG_CONFIG_HOME`을 따로 설정했다면 표를 참고해 실제 경로의 파일을 여세요. TOML 문자열에는 기본적으로 큰따옴표를 쓰고, PowerShell에서 Windows 경로를 적을 때는 슬래시(`/`)나 TOML 작은따옴표 리터럴 문자열을 사용하세요.

저장하기 전에 사용자 이름 자리표시자를 자신의 로그인 ID로 바꾸세요.

```toml
provider = "cnu"

[account]
username = "<login-id>"

[credentials]
provider = "keyring" # or "command"
# command = ["/absolute/path/to/credential-helper", "--profile", "<profile>"]

[browser]
# cdp_endpoint = "http://127.0.0.1:9223/json/version"
# lock_path = "/absolute/path/to/shared-browser.lock"
# executable_path = "/absolute/path/to/chromium"

[playback]
default_speed = 1.0
```

자료 다운로드 위치를 과목별로 정할 수 있습니다. 아래 경로 템플릿은 각 사용자가 자신의 환경에 맞게 정해야 합니다:

```toml
[materials]
semester = "2026-2"
download_dir = "~/School/{semester}/{course}/01_materials"
adopt_existing = true
```

`download_dir`은 선택 사항입니다. 지정하지 않으면 운영체제 다운로드 폴더의 `<Downloads>/campusctl/<safe course label>/` 아래에 저장합니다. 템플릿을 확장한 최종 폴더가 저장 위치입니다. 한 번만 다른 위치를 쓰려면 `--out DIRECTORY`를 사용하세요. 이때 하위 폴더의 기존 파일 재사용은 꺼집니다. 템플릿에서는 경로 한 구성 요소 전체에만 `{course}`(목록의 과목 이름), `{course_id}`(목록의 과목 ID), `{semester}`(사용자가 직접 입력한 값)를 쓸 수 있습니다. `{semester}`를 쓰려면 `semester`가 필요하며 LMS에서 학기를 추측하지 않습니다. 앞의 `~/`는 홈 폴더로 확장됩니다. 값이 있고 비어 있지 않은 `$NAME`/`${NAME}`은 모든 운영체제에서, `%NAME%`은 Windows에서 사용할 수 있습니다. 경로는 절대 경로여야 하고, 원본과 확장된 각 구성 요소에는 상위 폴더 이동, 심볼릭 링크, Windows 예약 이름처럼 위험한 값이 없어야 합니다.

`adopt_existing`의 기본값은 `false`입니다. 켜려면 `{course}` 또는 `{course_id}` 구성 요소가 정확히 하나 있어야 하며 설정한 저장 위치는 그 과목 폴더 아래 최대 네 단계까지만 들어갈 수 있습니다. 공식 파일 전송을 처음 검증해 마친 뒤 campusctl은 최대 네 단계까지 살펴보고 이름이 같은 기존 파일의 크기, 앞뒤 일부, 전체 바이트를 확인합니다. 일부만 일치해서는 재사용하지 않습니다. 검사 한도는 항목 10,000개와 후보 파일 읽기 1,000,000,000바이트입니다. 저장 위치에 같은 파일이 있으면 그대로 재사용하고, 과목 폴더의 다른 위치에 있으면 그 위치의 파일을 채택합니다. 비공개 영수증은 이후 재시도 때 정확한 출력 폴더와 파일 바이트를 다시 확인한 뒤에만 전송을 건너뛰게 합니다. 채택한 파일의 영수증은 같은 과목 폴더인지도 확인합니다. `doctor`는 폴더를 만들거나 파일을 훑지 않고 설정과 환경 변수 참조를 확인합니다. JSON의 `result.materials`에는 설정 원문이, 사람이 읽는 출력에는 기본/설정 위치와 재사용 켜짐/꺼짐만 나타납니다.

Windows에서는 스캔한 모든 폴더와 재분석 지점의 대상을 아직 안전하게 고정할 수 없습니다. 따라서 기존 파일 채택을 켜도 확인되지 않은 파일을 쓰지 않고 `output-path-conflict`로 중단합니다. Windows에서 보통의 지정 폴더 다운로드를 쓴다면 `adopt_existing = false`로 두세요.

`playback.default_speed`에는 `1.0`, `1.25`, `1.5`를 사용할 수 있습니다. Windows의 절대 TOML 경로는 `C:/path/to/file`처럼 슬래시를 쓰거나 작은따옴표 리터럴 문자열로 적으세요.

## 인증과 첫 실행 확인

`config init`이 비밀번호나 로컬 브라우저가 아직 없다고 알리면 터미널에서 해당 명령을 실행하세요:

```sh
campusctl auth set   # only if the password was not saved
campusctl setup     # only if the local browser is missing
campusctl doctor
```

설정의 `browser.executable_path`가 찾을 수 없는 브라우저를 가리키면 `config.toml`에서 그 경로를 고치거나 지우세요. `campusctl setup`은 사용자 지정 브라우저 경로를 바꾸지 않습니다.

`auth set`은 터미널 입력이 필요하고 비밀번호를 화면에 표시하지 않은 채 물어보며, 안전한 운영체제 키링에 저장합니다. `--json`을 쓰거나 출력을 다른 곳으로 보내도 입력이 터미널이면 질문할 수 있고 응답은 선택한 출력 형식을 따릅니다. `setup`은 로컬 Chromium을 설치하거나 확인하며 브라우저 세션 잠금을 잡지 않습니다. 지원되는 Windows 설치에서는 Windows Credential Manager를 사용합니다. `doctor`는 읽기 전용으로 로컬 설정, 자격 증명 사용 가능 여부, 브라우저·화면 준비 상태, 목록 존재 여부를 확인합니다. 로그인하거나 브라우저를 열거나 자격 증명 도우미를 실행하지는 않습니다. `auth status --check`는 키링 저장 여부를 확인하며 명령 도우미를 설정했다면 그 도우미를 실행합니다.

처음 설치했다면 다른 설정 검사가 모두 통과해도 `catalog-missing`과 종료 코드 2가 나올 수 있습니다. 첫 동기화 전에는 정상입니다. 앞서 나온 설정 오류를 해결한 뒤 다음을 실행하세요:

```sh
campusctl sync --only lectures
```

`doctor`는 먼저 해결할 문제를 알려줍니다. 종료 코드와 응답 형식은 [CLI 계약](contracts/cli.md#response-envelope-and-exit-codes)을 보세요.

## 브라우저 세션과 잠금

로컬 강의 재생은 `<data-dir>/profile/cnu`의 지속 프로필을 쓰는 화면 표시 Chromium에서 진행되며, 명령이 끝나면 브라우저 세션을 닫습니다. 브라우저를 변경하는 명령은 작업 내내 배타적·비차단 잠금을 잡습니다. 기본 잠금 파일은 `<data-dir>/session.lock`입니다.

로컬 과제·공지 fetch를 화면 없이 실행하려면 `[browser]` 아래에 `browser.headless = true`를 설정하거나 명령 앞에 전역 `--headless`를 붙이세요. 전역 `--headed`는 설정을 덮어씁니다. 기본값은 화면 표시이며 두 fetch 영역 모두 로컬 Chromium에서 headless를 지원합니다. CDP 브라우저에는 headless를 사용할 수 없고, 공식 플레이어 재생은 항상 화면 표시 모드입니다.

CNU에서는 계정당 활성 LMS 로그인 세션이 하나인 것으로 관찰되었습니다. 다른 브라우저나 기기에서 로그인하면 현재 세션이 끝날 수 있으니, 같은 계정의 다른 로그인 자동화와 campusctl을 함께 실행하지 마세요.

이미 실행 중인 브라우저를 공유하려면 직접 브라우저를 시작해 로그인하고 CDP 엔드포인트와 브라우저 세션을 변경하는 모든 클라이언트가 공유할 잠금 파일을 설정하세요:

```toml
[browser]
cdp_endpoint = "http://127.0.0.1:9223/json/version"
lock_path = "/absolute/path/to/shared-browser.lock"
```

현재 운영체제에서 유효한 절대 경로를 사용하세요. Windows에서는 `C:/path/to/shared-browser.lock` 같은 경로를 쓰면 됩니다. CDP 모드는 외부 브라우저에 연결하며 로컬 브라우저로 자동 전환하지 않습니다. 디버깅 엔드포인트는 로컬에서 비공개로 유지하고, 함께 사용하는 모든 클라이언트에 같은 배타적 잠금을 적용하세요.

## 자격 증명 도우미

사람이 지켜보지 않는 환경에서는 `credentials.provider = "command"`로 설정하고 `credentials.command`에 절대 실행 파일 경로를 포함한 인자 배열을 넣으세요. campusctl은 셸을 거치지 않고 도우미를 실행하며 표준 입력을 보내지 않습니다. 표준 출력에 `username`과 `password`가 들어 있는 JSON 객체를 기대하고 30초 제한을 적용합니다. 비밀값을 명령 인자나 로그에 넣지 마세요. 도우미 출력은 다시 표시되지 않습니다.

예를 들어 도우미는 실행되었을 때 다음 JSON 객체를 출력할 수 있습니다:

```json
{"username":"<login-id>","password":"<secret>"}
```

### Windows 도우미

Windows에서는 `.ps1`, `.cmd`, `.bat` 파일을 실행 파일로 바로 지정하지 말고 명시적인 인터프리터로 PowerShell 스크립트를 실행하세요. 배열의 첫 항목에 있는 인터프리터 경로는 절대 경로여야 합니다. 예시 경로를 본인 시스템의 절대 경로로 바꾸세요.

```toml
[credentials]
provider = "command"
command = ["C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe", "-NoProfile", "-File", "C:/path/helper.ps1"]
```

실행 파일과 각 인자를 TOML 배열의 별도 항목으로 적으세요. 공백이 포함된 경로도 하나의 항목으로 유지합니다. 전체 도우미 계약은 [자격 증명 공급자](contracts/cli.md#credential-providers)를 보세요.

## 무인 Linux

배포판 패키지 관리자로 Xvfb를 설치하세요. Xvfb는 화면 표시 브라우저용 가상 화면을 제공합니다. 강의 재생은 여전히 공식 플레이어에서 진행되며 건너뛰거나 숨겨지지 않습니다. 예:

```sh
xvfb-run -a campusctl sync --only lectures --json
```
