# shortform_content_production

콘텐츠 소재 입력부터 검증된 영상 산출물의 발행 큐 전달까지 담당하는 독립 프로젝트입니다.
**YouTube 업로드는 하지 않습니다.** 업로드·공개 상태·YouTube ID/URL 기록은 별도
`youtube_publish_only` 프로젝트만 담당합니다.

```text
Google Sheets
  커뮤니티 수집 이력 + 일일 요약
         │
         ▼
SFDailySheetIntake → 기획/정책 검수 → 캐릭터·장면 이미지 → Typecast/TTS → FFmpeg 렌더
         │                                                   │
         └──────── 전용 Postgres/gateway/worker/coordinator ─┘
                                                             ▼
                                              private Drive 보관 + QA readback
                                                             │
                                                             ▼
                                           발행 대기 탭에 대기 행 1건 기록
                                                             │
                                                             ▼
                                        youtube_publish_only가 YouTube에 발행
```

## 독립 경계

설치는 전용 Compose project, 네트워크, n8n, Postgres, gateway, TTS/render worker,
creative coordinator, DB/artifact 볼륨을 사용합니다. 기존 production stack이나 다른
creator 실행 인스턴스에 연결하지 않습니다. 설치 기본값은
자동화 OFF이며, 설치·OAuth 연결·doctor는 유료 생성이나 YouTube 업로드를 실행하지 않습니다.

포함된 n8n workflow는 모두 비활성입니다.

| ID | 역할 |
|---|---|
| `SFDailySheetIntake` | 소재 Sheet 읽기, 승인된 원본을 gateway task에 고정 |
| `SFAutomaticPrivateDriveArchive` | QA 통과 렌더를 비공개 Drive에 검증 보관 |
| `SFAutomaticProjectArchive` | 요청된 project bundle 보관 |
| `SFAutomaticPolicyApproval` | 설정된 창작 정책 게이트; 사람 승인으로 가장하지 않음 |
| `SFAutomaticProductionProjection` | 제작 상태를 기존 production ledger에 readback 검증 후 기록 |
| `SFAutomaticPublishQueueHandoff` | 검증·보관 완료 행을 별도 publisher queue에 멱등 전달 |

SF09/`SFAutomaticYouTubePublication`, YouTube OAuth ID, YouTube upload API/worker adapter는
이 프로젝트에서 제외했습니다. 별도 production 환경의 읽기 전용 점검에서 creator intake와
legacy YouTube publication이 활성인 것을 확인했습니다. 일부 active workflow의 node/type,
connections, 민감 값 제거 후 HTTP method/path는 local builder와 일치했지만,
`SFAutomaticPublishQueueHandoff`는 확인한 live instance에 없었습니다. 전체 node parameter,
credential ID, 실제 Sheet ID는 공개하지 않았고, 새 source package는 production에 설치하지
않았습니다. 다른 호스트의 active writer는 이 설치기가 감지하지 못할 수 있으므로, 새 경로를
활성화하기 전 운영자가 실제 Sheet owner와 single-writer 상태를 확인해야 합니다.

## 제작 스킬

`src/skills/contents-production-skills/`에는 사용자가 제공한
`contents-production-skills` **2.30** 중 이 파이프라인에 필요한 스킬과 지원 참조만
핀된 revision으로 내장했습니다. `BUNDLE_MANIFEST.json`에 원본 commit, 포함 경로, 파일별
SHA-256, 단계별 prompt 경로 및 실행 도구가 기록됩니다. 주제/수요 근거, 내러티브,
스토리보드 단계는 각자 필요한 스킬만 로드합니다. 스토리보드 단계에는 full-board skill과
고정된 validator/renderer/splitter가 적용됩니다. `community-shorts-stills-pipeline`은 고정 60초와 자체
playlist/publishing 단계를 전제해 현재 180초 제작·별도 publisher 경계와 충돌하므로
포함하지 않았습니다. 소스북, QA 실행 로그, 이미지/영상 파일은 복사하지 않았습니다.
Upstream `contents-production-skills` 저장소는 public이지만 LICENSE/license metadata가
발견되지 않았습니다. 요청에 따라 필요한 subset을 이 source에 포함했으나, 이 저장소는
그 파일들에 별도 재사용·재배포 라이선스를 부여하지 않습니다. 공개 전에 upstream 권리와
terms를 확인해야 합니다.

의도적으로 skill bundle을 갱신할 때는 새 upstream checkout을 확인한 뒤
`python3 build/embed_skills.py --skills-root <contents-production-skills 경로> --rebuild`를
실행하고 `python3 scripts/refresh_manifest.py --accept-source-changes`와 검증을 다시
수행합니다. `--rebuild`는 pinned revision의 변경을 묵인하지 않고 명시적 재생성을 요구합니다.

새 `schema_version 3.0` plan은 안정된 beat/scene/shot/panel 참조, 씬별 카메라 의도·인접
씬 비교, shot 기술 슬롯, panel별 공개/유보·가시 인물·발화/음향 연결을 요구합니다.
각 shot은 하나의 held still state로 설계하며 상태 변화는 별도 순차 shot으로 표현합니다.
media QA 전에 실제 scene 이미지로 최대 8패널 합본 시트를 페이지화하고, 실제 픽셀에서
clean panel과 scene overview를 추출해 checksums·provenance·production history에 등록합니다.
image-only sequence QA는 추출된 panel을 board label 없이 검토하고 결과를 publisher handoff
게이트에 결합합니다. 이 결과는 자동/AI 검토이며 사람의 승인·청취나 3D feasibility 검증을
뜻하지 않습니다. 2D scene은 spatial N/A 사유를 기록하고, 실제 3D 의존 입력은 막습니다.
Audience-demand evidence가 없는 주제는 측정된 인기로 포장하지 않습니다.

`ai-delegation-loop`와 Codex Imagen 실행기는 별도 host skills/runtime 의존성입니다. 기존
`Skills-and-Automation` 위치를 설정할 수 있으며, 이 두 의존성이 없거나 hash가 바뀌면
runtime 검증이 실패합니다. Codex Imagen을 다른 이미지 제공자로 자동 대체하지 않습니다.
Pi 런타임은 모델 도구를 끄고 사용하며, skill revision이 계획 provenance와 gateway 설정에
고정됩니다.

## 발행 큐 계약

발행 프로젝트의 `SFP00-Setup-Publish-Sheet`로 `발행 대기` 탭을 먼저 초기화합니다.
제작 설정의 `publish_sheet_id`는 기본적으로 소재 시트와 같지만 별도 지정할 수 있습니다.
queue tab/header가 없거나 순서가 다르면 handoff는 멈춥니다.

제작 프로젝트는 렌더·Drive checksum/Google readback·권리·8개 QA 게이트가 모두 통과한 뒤에만
다음 필드를 기록합니다.

| 열 | 제작 handoff 쓰기 |
|---|---|
| A `발행ID` | render lineage에서 결정적으로 만든 stable ID |
| B `영상링크` | 검증된 비공개 Drive MP4 |
| C–F | 제목, 설명, 태그, 카테고리 |
| G `공개설정` | 설정값, 기본 `공개` |
| H `상태` | 최초 삽입 시 `대기`만 기록. 이후 상태는 publisher 소유 |
| I–O | 비움; YouTube ID/URL/시도/클레임은 publisher만 기록 |

Handoff는 DB outbox claim, stable `발행ID`, Sheets 기존 행 조회·중복 거부·append/readback,
DB ack 순서로 동작합니다. 응답이 불확실하면 같은 ID를 먼저 찾아 대사하고 새 행을
무조건 추가하지 않습니다. publisher는 시트 공개설정에 따른 최종 privacy를 사용합니다.
제작 단계에서 유튜브에 올리거나 private→public 전환을 하지 않습니다.

## 정책과 외부 의존성

- 목표: **120분 간격, 하루 최대 12편**. 설치·활성화 전에 밀린 슬롯을 생성하지 않습니다.
- 초기 상태: intake, media, policy 자동화 모두 OFF. 실제 credentials 및 provider scope는
  n8n/host에서 연결한 뒤 별도 doctor로 점검합니다.
- Rights, originality, production approval, 8개 QA, archive checksum/readback 게이트는
  handoff 앞에서 유지됩니다. 스킬의 prompt는 실제 생성·청취·사용자 승인 증거를 대신하지
  않습니다.
- Typecast key, Pi auth, Google OAuth, Codex Imagen 실행기 코드는 복사하지 않습니다.
  n8n에는 본인 Google Drive/Sheets OAuth만 필요하고, YouTube credential은 필요 없습니다.
- Coordinator 컨테이너가 `/var/run/docker.sock`을 사용합니다. 이는 해당 호스트의 Docker
  관리자 권한과 동급이므로 격리된 운영 호스트/계정에서 실행하세요. Compose 분리는 host
  보안 sandbox가 아닙니다.

## 설치와 운영

요구사항: Linux, Python 3.12+, Docker Engine/Compose v2. 시작은 이 프로젝트 폴더에서:

```bash
./install.sh
```

설치기는 n8n, Postgres, gateway, worker만 준비하고 automation OFF로 둡니다. Google OAuth를
직접 연결하고, 별도 publisher의 queue 탭을 초기화한 뒤 `./shortform doctor`에서 실제
Google source/output sheet, private Drive folder, signer, embedded skill revision 및 외부
runtime을 확인합니다. 성공 전까지 자동화는 켜지지 않습니다.

모든 워크플로우 활성화는 유료 제작과 queue handoff만 허용하며 YouTube publication은
허용하지 않습니다. 상세 명령은 `docs/SETUP.md`, 보안과 복구는 `docs/SECURITY.md` 및
`docs/OPERATIONS.md`를 참조하세요.

## 검증 범위

설치 성공은 신규 계정의 Google/Typecast 인증, 유료 미디어 성공, 완전 자동 제작·발행,
또는 실제 음성·영상의 인간 QA가 아닙니다. 현재 구현/검증 상태는 `docs/VERIFICATION.md`에
기록합니다. 테스트와 모의 handoff는 실제 YouTube 업로드를 실행하지 않습니다.
