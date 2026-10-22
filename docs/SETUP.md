# 설치와 계정 연결

필수 환경은 Linux, Python 3.12+, Docker Engine/Compose v2입니다. 프로젝트는 별도
Compose 이름/네트워크/볼륨/n8n 포트로 설치됩니다. 기존 `shortform` 또는 `n8n`
인스턴스를 업데이트하지 않습니다.

권장 시작 방법은 프로젝트 루트에서 `./install.sh`를 실행하는 대화형 CLI입니다.
설치 위치, Docker project/port, 제작 cadence, 소재·발행 Sheet ID, Drive 폴더,
OAuth credential ID, 외부 Pi/skills/Codex Imagen 경로와 모델, Typecast 설정을 순서대로
묻습니다. 비밀번호·토큰을 JSON에 붙이지 않으며 Typecast 키는 숨김 입력으로 받습니다.

## 준비

1. 입력 Google Sheet에 `커뮤니티 수집 이력`, `일일 요약` 탭을 준비합니다.
2. 출력 publisher queue Sheet와 탭 `발행 대기`를 준비합니다. 기존
   `youtube_publish_only` 프로젝트의 `SFP00-Setup-Publish-Sheet`로 동일 시트/탭을
   초기화할 수 있습니다. 제작 측 handoff는 헤더를 새로 만들거나 바꾸지 않습니다.
3. 제작 파일을 보관할 비공개 Drive 폴더를 준비합니다.
4. 새 n8n에서 본인 Google Drive/Sheets OAuth credential을 만듭니다. YouTube OAuth는
   필요하지 않습니다.
5. 호스트에 이미 설치·인증된 Pi runtime, `ai-delegation-loop`, Codex Imagen 실행기,
   Typecast voice/key를 준비합니다. 설치기는 이 외부 의존성을 설치하거나 복사하지 않습니다.

## 설정

대화형 wizard가 기본 경로입니다. 아래 JSON은 `./install.sh --non-interactive` 또는
설정값 검토를 위한 대안이며, 대화형 경로를 쓰면 직접 파일을 만들 필요가 없습니다.

`settings.example.json`을 복사해 새 Docker project/port를 선택합니다. 기본 목표는
하루 최대12편, 최소120분 간격입니다. 놓친 슬롯을 몰아 처리하지 않습니다.

`accounts.json`에는 ID/설정값만 적고 토큰이나 비밀값은 넣지 않습니다.

```json
{
  "source_sheet_id": "SOURCE_SHEET_ID",
  "publish_sheet_id": "PUBLISH_QUEUE_SHEET_ID",
  "publish_queue_tab": "발행 대기",
  "publish_privacy": "공개",
  "publish_category_id": 22,
  "archive_folder_id": "PRIVATE_DRIVE_FOLDER_ID",
  "google_credential_id": "N8N_GOOGLE_CREDENTIAL_ID",
  "typecast_voice_id": "TYPECAST_VOICE_ID"
}
```

`publish_sheet_id`가 비어 있으면 입력 시트 ID를 기본으로 씁니다. 기본 공개설정은 앞서
정한 정책에 따라 `공개`입니다. 실제 publisher는 이 행의 값을 그대로 적용합니다.

두 프로젝트의 queue 대상도 일치시켜야 합니다. `youtube_publish_only`의
`SFP00-Setup-Publish-Sheet`가 만든 `발행 설정` 탭에서 `큐탭`을 이 프로젝트의
`publish_queue_tab`과 같게 둡니다. Queue가 publisher 설정 시트와 같은 spreadsheet면
`큐시트ID`를 비워 publisher 설정 시트 ID를 사용하고, 다른 spreadsheet면 `큐시트ID`에
이 설정의 `publish_sheet_id`를 그대로 넣습니다. 제작 doctor는 설정된
`publish_sheet_id`의 권한·헤더를 검증하지만, 별도 publisher의 `발행 설정` 값을 읽지는
않으므로 두 ID와 탭 이름은 활성화 전에 직접 대조해야 합니다.

## Publisher 설정과 SFP00

`SFP00-Setup-Publish-Sheet`는 publisher 프로젝트에서 **한 번 수동 실행하는 시트 초기화**
워크플로우입니다. `발행 대기`와 `발행 설정` 탭/헤더를 생성하며, YouTube 업로드를 하지는
않습니다. 이 단계를 수동으로 두는 이유는 별도 publisher n8n의 Google credential과
사용할 spreadsheet를 운영자가 직접 선택·승인하기 때문입니다.

Publisher는 별도 `youtube_publish_only/config/publish_only.json`에서 queue spreadsheet ID,
channel ID, Google/YouTube credential ID를 설정하고, 해당 n8n UI에서 OAuth를 연결합니다.
Creator wizard는 제작 측 publish Sheet ID/tab을 수집하고 SFP00 초기화 절차를 안내하지만,
publisher 설정 파일을 대신 작성하거나 SFP00을 대신 실행하지 않습니다. SFP00 이후에도
SFP01–03은 inactive이며, 활성화한 뒤 SFP01이 5분 주기로 큐를 읽고 행의 공개설정에 따라
업로드합니다. 따라서 **SFP00 수동 실행은 수동 발행이 아니라 1회성 시트 준비**입니다.

## 대화형 설치 흐름

```bash
./install.sh
```

CLI는 설치, 브라우저에서 본인이 수행하는 Google OAuth 연결, account mapping, 외부 runtime
연결, 실제 read-only doctor 순서로 진행합니다. Wizard는 소재/발행 Sheet ID, 탭, privacy,
category, Drive 폴더, credential ID, voice/model/runtime 경로를 입력받습니다. Google OAuth
승인과 publisher의 SFP00 실행은 사용자가 직접 완료해야 합니다. n8n workflow는 모두
비활성으로 들어오며, queue header가 다르거나 source/publisher Sheet/Drive 권한이 확인되지
않으면 활성화를 막습니다.

`./shortform enable --allow-paid-generation`은 제작과 큐 handoff만 허용합니다. YouTube
업로드는 이 패키지에서 할 수 없습니다.
