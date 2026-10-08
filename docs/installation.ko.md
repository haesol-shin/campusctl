[English](installation.md) | **한국어**

# 설치

`campusctl`은 Windows, macOS, Linux에서 사용할 수 있습니다. Python 3.11 이상, [uv](https://docs.astral.sh/uv/), Git이 필요합니다. 알맞은 Python이 아직 없다면 uv가 설치해 관리할 수 있습니다.

에이전트에게 설치와 설정을 맡기려면 [README의 AI 에이전트 안내](../README.ko.md#agent-setup)에 있는 한 줄을 붙여 넣습니다.

## 준비물

- **Windows (PowerShell):** uv는 `winget install --id astral-sh.uv -e`, Git은 `winget install --id Git.Git -e`로 설치하세요. 또는 [공식 uv PowerShell 설치 프로그램](https://docs.astral.sh/uv/getting-started/installation/)을 사용할 수 있습니다: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`.
- **macOS:** uv는 `curl -LsSf https://astral.sh/uv/install.sh | sh`, Git은 Homebrew의 `brew install git`으로 설치하세요.
- **Linux (Debian/Ubuntu):** uv는 `curl -LsSf https://astral.sh/uv/install.sh | sh`, Git은 `sudo apt-get install git`으로 설치하세요. 다른 배포판에서는 해당 패키지 관리자를 사용하세요.

새 셸을 열고 `uv --version`과 `git --version`으로 설치를 확인하세요.

## campusctl과 브라우저 설치

버전을 지정하지 않은 Git URL은 저장소의 기본 브랜치를 설치합니다. 이 설치 방법은 uv 명령 연결도 확인하는 저장소의 [Windows 설치 검사](../.github/workflows/ci.yml#L212-L247)와 같습니다. `campusctl setup`을 실행하면 campusctl과 같은 환경에서 Chromium을 설치하므로 이를 먼저 사용하세요. Playwright 수동 설치 명령은 대안입니다.

비대화형 또는 JSON 모드의 `setup`은 Chromium만 확인하거나 설치합니다. 안내형 설정을 사용할 수 없다면 `campusctl config init --username ID`와 `campusctl auth set`을 따로 실행하세요.

```powershell
# On Windows.
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

새 PowerShell 창을 열어 setup을 실행하고 명령을 찾을 수 있는지 확인하세요:

```powershell
campusctl setup
Get-Command campusctl
```

setup으로 Chromium을 설치할 수 없다면 수동으로 설치하세요:

```powershell
playwright install chromium
```

```bash
# On macOS.
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

새 터미널을 열어 setup을 실행하고 `campusctl`이 `PATH`에 있는지 확인하세요:

```bash
campusctl setup
command -v campusctl
```

setup으로 Chromium을 설치할 수 없다면 수동으로 설치하세요:

```bash
playwright install chromium
```

```bash
# On Linux.
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl
uv tool update-shell
```

새 터미널을 열어 setup을 실행하고 필요하다면 Linux 시스템 라이브러리를 설치하세요:

```bash
campusctl setup
uv tool run --from playwright playwright install-deps chromium
command -v campusctl
```

setup으로 Chromium을 설치할 수 없다면 `install-deps`를 실행하기 전에 `playwright install chromium`으로 수동 설치하세요.

`install-deps`는 운영체제 패키지를 설치하므로 관리자 권한이 필요할 수 있습니다. Windows에는 추가 시스템 패키지가 필요하지 않습니다. 화면에 보이는 강의 재생에는 Windows와 macOS에서 로그인된 데스크톱 세션이 필요하며, Windows의 `doctor`는 데스크톱 사용 가능 여부를 검사하지 않습니다. Linux에서는 데스크톱 세션이나 Xvfb를 사용하세요.

## PATH와 브라우저 캐시

`campusctl`이나 `playwright` 명령을 찾을 수 없다면 `uv tool update-shell`을 실행하고 새 셸을 연 뒤 도구의 실행 파일 폴더가 `PATH`에 있는지 확인하세요. Windows에서는 `Get-Command campusctl, playwright`, macOS와 Linux에서는 `command -v campusctl` 및 `command -v playwright`로 확인할 수 있습니다.

Playwright 브라우저 캐시의 기본 위치는 다음과 같습니다:

| 운영체제 | 기본 캐시 위치 |
| --- | --- |
| Windows | `%USERPROFILE%\AppData\Local\ms-playwright` |
| macOS | `~/Library/Caches/ms-playwright` |
| Linux | `~/.cache/ms-playwright` |

`PLAYWRIGHT_BROWSERS_PATH`를 설정했다면 Chromium을 설치할 때와 campusctl을 실행할 때 같은 값을 사용하세요.

## 버전 고정

`v0.6.3` 릴리스를 고정해 설치하려면:

```bash
uv tool install --with-executables-from playwright git+https://github.com/haesol-shin/campusctl@v0.6.3
```

## 업데이트 또는 제거

CLI를 업데이트하고 브라우저를 확인하거나 설치하려면:

```bash
uv tool upgrade campusctl
campusctl setup
```

setup으로 Chromium을 설치할 수 없다면 `playwright install chromium`을 수동 대안으로 사용하세요.

campusctl과 `campusctl` / `playwright` 명령 연결을 제거하려면:

```bash
uv tool uninstall campusctl
```

도구를 제거해도 Playwright가 다운로드한 브라우저 캐시는 남습니다.

## 체크아웃에서 설치

복제본을 계속 둘 상위 폴더를 고르세요. 그 상위 폴더에서 터미널을 열고:

```bash
git clone https://github.com/haesol-shin/campusctl.git
cd campusctl
uv sync
uv run campusctl setup
uv run campusctl --help
```

체크아웃에서 명령을 실행할 때는 `uv run campusctl <command>`를 사용하고, Chromium 설치·업데이트에는 `uv run campusctl setup`을 사용하세요. setup으로 설치하지 못한다면 `uv run playwright install chromium`을 수동 대안으로 사용하세요. 브라우저 캐시와 Linux 시스템 라이브러리 요구 사항은 uv 도구 설치와 같습니다.
