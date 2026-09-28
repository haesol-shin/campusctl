[English](usage.md) | **한국어**

# 사용 안내

`campusctl`은 필요할 때 실행하는 도구로, 백그라운드 서비스가 없습니다. 충남대와 제휴하지 않았으며 LMS가 바뀌면 작동하지 않을 수 있습니다. 설치와 첫 대화형 설정은 [설치 안내](installation.ko.md)와 [설정 안내](configuration.ko.md)를 보세요.

## 첫 설정과 준비 상태

터미널에서 `campusctl setup`을 실행하면 계정 설정, Chromium 준비, 화면에 보이지 않는 비밀번호 입력과 운영체제 키링 저장, 선택적인 첫 동기화를 차례로 안내합니다. 비대화형 또는 JSON 모드의 setup은 Chromium만 확인하거나 설치합니다. 이때는 `campusctl config init --username ID`와 `campusctl auth set`을 따로 실행하세요. `campusctl doctor`는 로그인하거나 브라우저를 열지 않고 로컬 준비 상태만 확인합니다. 비밀번호를 명령 인자나 환경 변수에 넣지 마세요.

## 출력 형식

터미널에서는 사람이 읽기 쉬운 형식이 기본입니다. 출력을 다른 곳으로 보내면 자동으로 JSON이 선택되고, 명령 어디에든 `--json`을 넣으면 JSON을 강제로 사용합니다. `CAMPUSCTL_OUTPUT=human` 또는 `CAMPUSCTL_OUTPUT=json`은 자동 선택을 덮어쓰지만 `--json`보다 우선하지 않습니다. 다른 값은 무시됩니다. 설정 및 setup의 질문은 입력과 출력이 모두 터미널이고 사람이 읽는 형식일 때만 나타납니다. `auth set`은 출력이 JSON이어도 입력이 터미널이면 비밀번호를 물을 수 있습니다.

JSON 응답에는 버전이 있는 형식으로 `status`, `result`, `errors`가 담깁니다. 스크립트와 에이전트는 화면에 표시된 번호 대신 JSON과 전체 ID를 사용하세요. 필드, 종료 코드, 출력 형식의 우선순위는 [CLI 계약](contracts/cli.md)에 정리되어 있습니다.

## 동기화와 로컬 목록

`campusctl sync`는 한 브라우저 세션에서 강의, 과제, 공지, 자료를 새로 고치고 과목마다 한 번씩 선택합니다. `campusctl sync --only lectures,notices`로 범위를 좁힐 수 있습니다. 과목이나 영역에서 실패해도 이전 목록은 남으며, 결과에 일부 실패나 오래된 데이터라고 표시됩니다. 남아 있는 항목을 방금 확인한 정보로 여기지 마세요.

`status`와 목록 명령은 네트워크 없이 로컬 목록을 읽습니다. 최신 정보가 필요하면 목록에 `--refresh`를 붙이거나 `campusctl sync`를 실행하세요. 오래된 목록 경고, 확인되지 않은 수강 상태, 실패한 과목, 누락된 영역이 있으면 비어 있거나 필터링된 목록도 불완전할 수 있습니다. 새로 고칠 수 없는 영역은 LMS에서 직접 확인하세요. `campusctl status`는 로컬 목록에서 곧 마감되는 과제, 읽지 않은 공지, 열려 있는 미완료 강의를 요약합니다.

첫 동기화 전 사람이 읽는 `campusctl status` 출력은 0개라는 요약 대신 “No coursework has been synced yet. Next: campusctl sync”라고 안내합니다. 로컬 목록 파일이 있지만 아무것도 읽을 수 없을 때는 다시 동기화하라고 안내합니다. 누락된 목록 오류에는 로컬 경로 대신 해당 영역과 동기화 명령이 표시됩니다. JSON 오류와 종료 코드는 그대로입니다.

## 과목 번호와 전체 ID

사람이 읽는 출력에서 `campusctl courses list`가 매긴 번호를 `--course NUMBER`에 쓸 수 있습니다. 과목 이름의 고유한 일부도 사용할 수 있습니다. `campusctl materials list`가 매긴 번호는 `campusctl materials download NUMBER`에 씁니다. ID 없이 자료 다운로드를 실행하면 대화형 터미널에서 파일 하나를 고를 수 있습니다.

번호는 마지막으로 출력한 목록에 묶여 있어 목록이 바뀌면 사용할 수 없습니다. 전체 ID는 목록을 먼저 출력하지 않아도 쓸 수 있고, `--json`에서는 전체 ID가 필요합니다. JSON 모드의 `--course`에도 정확한 과목 ID를 쓰세요. 제목만 보고 ID를 추측하지 마세요.

## 강의 목록과 재생

`campusctl lectures list`는 기본적으로 미완료 강의를 보여주며, `--all`을 붙이면 완료 및 `recorded` 항목도 포함합니다. 사람이 읽는 출력의 마감일은 CNU 현지 시간입니다. `-`는 마감일 정보가 없다는 뜻이고, `opens MM-DD`는 아직 열리지 않은 강의를 뜻합니다.

### 강의 목록 예시

다음은 예시 과목으로 `CAMPUSCTL_OUTPUT=human`을 설정하고 너비 80열에서 `campusctl lectures list`를 실행해 캡처한 결과입니다:

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

선택한 강의의 전체 ID로 `campusctl lectures play ID`를 실행하세요. 화면에 보이는 공식 플레이어에서 한 번에 하나씩 재생하고, 끝난 뒤 LMS 행의 상태를 확인합니다. 진도나 출석을 별도로 요청하지 않습니다. 앞으로 건너뛰거나 진도를 위조하거나 지원되지 않는 속도를 강제하거나 백그라운드에서 재생하지 않습니다. YouTube는 기본 자동 재생으로 1배속만 사용하고, 다른 미디어는 해당 플레이어가 지원하는 속도만 사용합니다.

출석 미반영으로 표시된 행은 화면의 시청 진도가 필요한 전체 시간에 도달했을 때만 `watched (not counted)`로 표시됩니다. 그 진도는 이번 재생 전에 기록된 것일 수도 있으며, 출석 인정 보장은 아닙니다. CNU는 계정당 LMS 로그인 세션 하나만 허용하는 것으로 관찰되어, 같은 계정으로 로그인한 다른 자동화와 campusctl을 함께 실행하지 않는 편이 안전합니다. 브라우저 세션은 [설정 안내](configuration.ko.md)를 보세요.

## 과제와 공지 상세

`assignments list`와 `notices list`는 목록 정보를 보여줍니다. 자세한 내용을 읽으려면 전체 ID 하나 이상을 고른 뒤 `campusctl assignments fetch ID1 ID2` 또는 `campusctl notices fetch ID1 ID2`를 실행하세요. ID 하나도 가능합니다. 선택한 상세 항목은 한 브라우저 세션에서 가져옵니다. 가져온 내용은 로컬 데이터 폴더의 `sources/` 아래에 `content.md`와 `package.json`으로 저장됩니다. 서로 다른 ID 하나만 가져올 때 `--out DIR`은 아직 존재하지 않는 새 패키지 폴더를 가리켜야 합니다. JSON의 `result.source_package`에서 경로, 완전성, 생략된 리소스를 볼 수 있습니다.

서로 다른 ID 여러 개를 가져올 때는 `--out`을 빼세요. JSON의 `result.items`는 요청 순서대로 나오며, 각 항목에 `outcome`이 있습니다. 일부만 완료되거나 실패한 항목에는 `reason_code`가, 완료되거나 일부만 완료된 항목에는 `source_package`가 있습니다. fetch는 로컬 Chromium의 화면 표시·headless 모드를 지원하며 과제를 제출하지 않습니다. 공지 상세를 열면 조회수가 늘거나 읽음 상태가 바뀔 수 있지만, fetch가 읽음 처리 버튼을 누르지는 않습니다. 공지 첨부파일은 다운로드하지 않고 생략합니다.

공지는 과목별 게시판에서 가져옵니다. 할 일 목록에 일치하는 행이 없으면 읽음 여부는 알 수 없습니다. 게시판에 다음 페이지가 있으면 `notice-board-paginated`가 표시되고 그 과목의 이전 목록은 유지됩니다. 해당 과목 게시판을 LMS에서 확인하세요. 자세한 계약은 [과제](contracts/assignments.md#selected-detail-fetch)와 [공지](contracts/notices.md#selected-detail-fetch)를 보세요.

## 자료 다운로드

`campusctl materials download ID`는 선택한 공식 첨부파일 하나를 운영체제 다운로드 폴더의 `campusctl/<course label>/` 아래에 저장합니다. Windows에서 다운로드 폴더를 옮겼다면 그 위치를 따릅니다. 다른 위치에 저장하려면 `--out DIR`을 쓰세요. 과목별 저장 경로와 기존 파일 재사용 설정은 [설정 안내](configuration.ko.md)에, 선택 및 전송 규칙은 [자료 계약](contracts/materials.md)에 있습니다.

## 브라우저 모드

전역 `--headless`와 `--headed`는 명령 앞에 놓으며 `browser.headless` 설정보다 우선합니다. 기본값은 화면 표시입니다. 예를 들어 `campusctl --headless sync`, `campusctl --headless assignments fetch ID`, `campusctl --headless notices fetch ID`, `campusctl --headless materials download ID`는 로컬 Chromium 프로필을 사용합니다. CDP 세션에는 headless 모드를 쓸 수 없고 공식 플레이어 재생은 화면 표시 모드만 지원합니다.

로컬 목록과 브라우저 프로필은 campusctl의 비공개 데이터 폴더에 보관됩니다. 로컬 모드에서는 명령이 끝나면 브라우저 세션을 닫고, CDP 모드에서는 외부 브라우저를 그대로 둡니다. 브라우저를 변경하는 명령은 하나의 배타적 잠금을 공유합니다. 경로, 자격 증명, 공유 브라우저 설정은 [설정 안내](configuration.ko.md)를 보세요.

## 성능 기록

`campusctl --profile sync`(또는 `--refresh`를 붙인 목록)를 실행하면 표준 오류 출력에 `campusctl-profile:` JSON 한 줄이 기록됩니다(스키마 버전 2). 구간별 시간, 문서별 시간, 잠금 범위와 실패한 페이지 준비 상태·과목 식별·할 일 표·자료실 상태·모달 연결 검사만 담은 `diagnostics` 배열이 포함됩니다. 진단에는 과목 이름이나 URL 대신 실행 중에만 유효한 과목 순번을 쓰며, 통과한 검사는 기록하지 않습니다. 이 기록은 실행 속도를 보장하지 않습니다.

## 업데이트와 문제 해결

`uv tool upgrade campusctl`로 업데이트한 뒤 `campusctl setup`으로 Chromium을 확인하거나 설치하세요. `campusctl sync`로 목록을 새로 고칠 수 있습니다. 버전 고정, 제거, 체크아웃 설치는 [설치 안내](installation.ko.md)를, 오류별 해결책은 [문제 해결](troubleshooting.ko.md)을 보세요.
