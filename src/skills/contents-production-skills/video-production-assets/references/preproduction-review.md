# 주제 → 프리프로덕션 → 사용자 검토

주제만 받아 전체 영상 제작을 요청받았거나 검토용 프리프로덕션 패키지를 요청받았을 때 적용한다. 결과는 한 프로젝트 폴더 안의 실제 문서·정지 이미지·검수 보고다. 실제 영상은 별도 실행 승인을 받은 다음 단계다. 단일 텍스트 에셋 요청은 해당 모듈만 실행한다.

## 1. 정확한 프로젝트 폴더 확인

사용자가 선택했거나 현재 활성 프로젝트에서 확인된 **실제 승인·에셋 완결 프로젝트 루트**를 재사용한다. 과거에 쓴 프로젝트 경로를 이유 없이 자동 승계하지 않는다. 이를 확인할 수 없으면 실제 사용자 Documents 아래 `studio_production/<project_id>`를 사용하며, 도구/현재 프로젝트에서 확인할 수 있는 경로를 다시 묻지 않는다. 저장소 validation fixture를 사용할 때는 별도의 외부 프로젝트 루트를 만들고 모든 파일·보고서에 validation smoke이며 승인된 사용자 납품물이 아님을 표시한다.

파일을 쓰거나 이미지 도구를 호출하기 전에 선택 루트, 정본 `project.json`, 등록 파일/버전, 승인 근거, 쓰기 권한, `.history`의 in-flight operation을 검사한다. 등록 파일 누락은 기존 패키지가 완결 상태가 아님을 뜻하고, pending operation이 있으면 새 작업을 시작하지 말고 상태를 확인해 명시적으로 재개/조정한다. 경로에 기존 파일이 있으면 ID·파일을 보존하고 명시적 재개로 처리하며 덮어쓰거나 삭제하지 않는다. 루트 접근이 불가능하면 정확한 blocker를 말하고 해당 루트에 쓰지 않는 작업만 진행한다.

모든 산출물은 선택된 실제 외부 루트 안에 저장하고 `asset_registry.path`는 루트 기준 상대 경로로 기록한다. 이미지 도구에 같은 명시적 루트를 전달한다. 소스·검토본·수정본은 덮어쓰지 않고 버전으로 구분한다.


| 위치 | 내용 |
|---|---|
| `project.json` | 기존 제작 계약의 단일 원장 |
| `review.md`, `brief.md` | 검토용 파일 목록·검사·미정 사항, 제작 목표·제약 |
| `references/` | 필요한 출처 링크·관찰·이용범위 기록 |
| `script/`, `visual/` | 적용 가능한 각본·비트·연기, 비주얼 바이블·연속성 |
| `shots/` | 샷 리스트·패널 매핑·필요한 카메라 명세 |
| `images/`, `prompts/` | 실제 검토 이미지, 재현용 프롬프트·호출 설정 |
| `edit/`, `qa/` | 편집·사운드·타이밍 계획, 실제 검수 결과 |

요청에 필요한 파일만 만든다. 비적용 모듈에 빈 파일을 만들지 않는다. 새 manifest나 별도 승인 데이터베이스를 만들지 않는다.

### 1a. 실제 파일 저장 초기화와 제작 이력

실제 산출물 파일을 만드는 제작 작업이 시작되면, 프로젝트 루트를 정한 뒤 `recording-production-history`의 도구로 저장 구조를 초기화한다:

`python <skill-dir>/recording-production-history/scripts/production_history.py init --project-id <ID> [--documents <PATH> | --project-root <ROOT>]`

명시적 저장 루트가 없으면 기본 루트는 실제 사용자 Documents 폴더의 `studio_production/<project-id>`다(Windows는 리디렉션된 Documents/OneDrive, macOS는 `~/Documents`, Linux는 XDG 또는 `~/Documents`). `--documents`와 `--project-root`는 동시에 지정하지 않는다. `--project-root`는 선택한 폴더를 그대로 사용하며 불필요한 하위 경로를 덧붙이지 않는다. `init`은 v5.1 패키지 디렉터리와 `.history`를 만들고 기존 `project.json`을 덮어쓰지 않는다.

실제 미디어·문서 파일을 만들거나 바꾸는 작업은 시작과 종결을 `record`로 남긴다:

`python <skill-dir>/recording-production-history/scripts/production_history.py record --project-root <ROOT> --operation <TEXT> --prompt-file <FILE> --status started|completed|interrupted|failed [--output <PATH> ...]`

중간·최종 산출물은 `--output`으로 등록하고, 반환된 실제 절대 경로를 사용자에게 보고한다. `.history`는 감사 기록이지 승인 원장이 아니다 — `project.json`이 정본이며 승인 증거는 기존 계약이 기록한다. 작업이 중단된 뒤 재개하면 `status`로 in-flight operation을 확인하고 `resume`으로 중단을 명시한 뒤 이어간다.

파일을 만들지 않는 채팅 전용 작업은 저장 초기화·이력 기록을 생략한다(chat-only는 no-save).


## 2. 적용 가능한 에셋 작성

브리프의 주제·목적·형식·길이·제약을 추출하고 중요한 창작 방향은 사용자 선택 또는 명시적 위임으로 정한다. 해당 프로젝트의 소재 출처·판단 도구·실행 차단 조건을 기록한다. 특정 사이트, 이진 모델, 장르, 길이, 화면비를 공통 기본값으로 사용하지 않는다.

주제·시놉시스 탐색이 요청 범위에 있으면 [탐색 계약](../../orchestrating-video-preproduction/references/video-direction.md#discovery-topic-and-synopsis)의 채널/관객 우선 추천·기본 개수·선택 체크포인트·확정 입력 생략 규칙을 적용한다. 이 참조는 현재 승인 흐름에서 사용할 선택 규칙이지 두 번째 총괄이나 원장이 아니다. 일반 원고·단일 확정 에셋을 후보 탐색으로 확대하지 않는다.

레퍼런스 기반·다중 샷 제작에는 [프로젝트 스타일 브리프](01-brief.md#프로젝트별-스타일-브리프)의 필요한 항목을 기존 브리프/비주얼 바이블에 둔다. 승인된 레퍼런스 적용 요구와 스타일 버전은 뒤 단계로 그대로 인계한다.

관련 모듈의 템플릿을 실제 내용으로 채운다. 인물 없는 영상에는 캐릭터를 만들지 않는다. 전체 패키지는 [필수 산출물 계약](contract.md#전체-프리프로덕션-패키지)에 따라 **시놉 MD → 인물별 페르소나·SSOT MD → 실제 한 장짜리 Identity Sheet A → 통합 기술 콘티 → 전체 패널을 덮는 합본 스토리보드 이미지 세트(시트당 최대 8패널) → 검수**로 진행한다. 총괄이 시놉/캐릭터/콘티 담당과 이미지 실행자를 순서대로 배정한다. ‘별도 캐릭터 시트 문구 없음’을 이유로 전체 패키지의 인물 설계를 생략하지 않는다. 사실·권리 근거와 창작 제안을 구분하고 기존 승인 자료를 보존한다.
제작용 스토리보드는 [완전한 보드 계약](storyboard-contract.md)을 필수 적용한다. 전체 시놉 비트→씬→샷→키패널과 각 샷의 카메라/공간·VFX·대사/립싱크·사운드 큐를 이미지 생성 전에 통합한다. 제공 콘티도 같은 항목으로 QA→부족 항목 보완→재검수하며 승인된 이야기와 제공 시각 앵커를 보존한다.

`project.json`의 샷·에셋 ID를 문서와 매핑한다. 영상 모델은 프리프로덕션 동안 미정으로 둘 수 있다. 모델별 문법 대신 동작·구도·시작/종료 상태·연속성 요구사항을 인계한다.

## 3. 검토용 이미지는 Codex Imagen 기본 경로

Use `codex-imagen` by default for image-backed packages unless the user selected another executor. Read its helper, authentication, reference-input, and output instructions; do not look up Codex Imagen usage, quota, price, or quote, and do not block on unknown cost. Do not automatically search or substitute another provider.

Before the call, verify runtime/auth and technical readiness using that tool's actual smoke/auth method. Do not expose credentials. Authentication is a runtime check, not a reason to inspect usage or pricing.

실제 호출 전에 이미지 수, 출력 위치, 시간/재시도 범위를 정한다. 단일 검토 이미지는 선택 실행자의 실제 출력 방식으로 `<project-root>/images/` 아래 버전이 붙은 경로에 저장하고, 관련 프롬프트/설정을 `prompts/`에 남긴다. 선택 실행자의 최신 timeout·reference·다중 이미지 규칙을 따른다. 참조 이미지는 명시적으로 연결한다. 기존 파일 경로를 재사용하지 않는다.

창작 방향을 아직 수락/위임받지 않았으면 대표 **정지 이미지**로 확인한다. 위임받은 경우 요청한 세트를 생성한다. 반환된 실제 파일을 열어 의도·인물/소품·구도·연속성을 검사하고 원장에 ID·사용 샷·버전·해시·상대 경로를 기록한다. 부분 생성/미완료는 실제 받은 파일만 등록한다. 제공되지 않은 seed·가격·생성 결과를 만들지 않는다.
For a character-backed package, the character designer first returns the synopsis-based persona, visual locks, Identity Prefix, and Sheet A prompt. Before any dependent master or panel still, close the applicable storyboard-contract spatial/contact gate for required contact poses; without feasible-pose evidence, mark the relevant claim UNVERIFIED and hold dependent image generation. When evidence is missing, return the storyboard contract's indexed packet in the existing review/request: scene_id, shot_id, beat_id, panel_id, frame_time/range, required action/contact surfaces, evidence inspected with locator/version/hash or NONE, missing evidence, required articulated asset/pose/runtime and owner, held dependent asset/generation, and reinspection acceptance criterion. Do not infer IDs or feasible contact from a prompt, BBox, visibility, or overlap. For each master call, make the authoritative SSOT content being consumed part of the actual submitted prompt and preserve that submitted prompt plus actual input-reference hashes in the existing per-call `.history` record; file existence, trait agreement, or a filename/ID/hash mention alone does not prove consumption. Inspect each actual seven-view Identity Sheet A against its SSOT and continuity anchors before panel generation, then pass its real source IDs/versions/hashes downstream. A scene portrait or prompt cannot substitute. Sheet B/C/D prompts do not authorize additional outputs; generate them only when separately requested.

Review stills do not require video-generation approval. Video samples, moving animatics, test renders, and edited video exports remain outside still-image review and require the separate video-execution gate.

Other explicitly selected still-image or video providers retain their applicable provider-specific approval rules. Codex Imagen still-image calls do not require usage, quote, or cost approval.

전체 clean 패널을 실제 생성/검사한 후 `render_storyboard_sheet.py <project.json> --base-dir <root> --output <새-relative.png> [--font <font.ttf>]`로 모든 컷과 외부 촬영/연결 인덱스를 이야기 순서의 **시트 세트(시트당 최대 8패널)**로 조립한다. 반환된 각 시트의 실제 path/hash/panel_ids/sheet_index/sheet_count/source_asset_ids/source_sha256을 기존 원장에 등록한다. 합본을 열어 순서·전수 포함·인물 SSOT 링크·구도/앵글/샷·한글 캡션 가독성을 검사한다. 개별 컷/Markdown만으로 전체 합본 시트 완료라고 하지 않는다.

## 4. 검수하고 보고한 뒤 대기

초안에는 `python <skill-dir>/scripts/validate_project.py <project-root>/project.json --profile plan --base-dir <project-root>`을 쓴다. **전체 패키지 검토 인계 전에는 `--profile preproduction`을 필수 실행**해 네 산출물 범주·실제 파일·인물 연결·합본 순서/소스 해시·필수 보드 슬롯·열린 blocker를 검사한다. 파일 누락은 검토 준비 미완료이며 draft/stale로 남긴다. 이 검사는 미디어 의미/권리/사용자 승인 진위를 보장하지 않는다.
상세 보드는 `python <skill-dir>/scripts/validate_storyboard.py <project-root>/project.json --require-images --base-dir <project-root>`를 추가 실행한다. 그 뒤 **전체 clean 컷을 설명 라벨·음향 없이 순서대로 보아** 시놉의 사건·인과/정보·감정 변화가 실제 이미지로 읽히는지 계약 §5의 의미 검수를 수행한다. 구조 성공·패널 수 일치·대표 몇 장의 검사만으로 통과시키지 않는다. 요구된 Blender 검증이 없으면 해당 공간은 미검증이다.

`review.md`에는 다음을 넣고 채팅에서도 경로와 주요 미리보기를 보고한다.

- 정확한 프로젝트 폴더와 목표/형식; 실제 시놉 MD, 인물별 페르소나·SSOT MD/Identity Sheet A, 전체 기술 콘티 MD, 이야기 순서 합본 스토리보드 이미지 세트의 링크·ID·버전·해시
- 검사한 파일/버전 목록과 검사 결과·증거
- 실제 생성된 이미지와 텍스트만 준비된 항목의 구분
- 가정, 미검증/미정 사항, 차단 조건, 사용자에게 필요한 검토 선택
- 상세 보드: 전체 beat/scene/shot/panel 매핑과 이미지 계획/실제 수, 이미지 단독 검수 근거, KEEP/FIX/미검증, 인덱스별 최소 수정·추가 준비물·근거 있는 예상 시간·재검수 결과
- 필수 산출물 완결 검사와 실제 캐릭터 7뷰/전체 clean 컷/합본 가독성 검수의 구분; 누락·미검증 필수 항목은 준비 완료나 승인 상태로 승격하지 않음
- 상태: **프리프로덕션 검토 대기; 영상 미생성**

내용이 완성돼도 사용자 수락을 추정하지 않는다. 응답이 없으면 대기한다. 모델의 긍정 판정·validator 성공·generated/verified 상태는 사용자 승인 증거가 아니다.

## 5. 버전별 승인과 영상 인계

기존 [계약](contract.md)의 artifacts/approval/dependency_versions를 사용한다. `review.md`를 `type=preproduction_review` artifact로 등록해 검토한 입력 버전에 연결한다. 검토용 문서의 완료는 reviewed, 실제 사용자 수락은 approved와 `approval={by, at, evidence}`로 기록한다. 원장 안에 검토 자기 자신에 대한 순환 dependency를 만들지 않는다.

사용자가 해당 버전을 수락한 뒤 **영상 제작으로 진행하라고 별도로 명시한 경우**에만 조정자(`creative-production`)가 모델/endpoint의 실시간 기능·스키마·가격을 확인해 실행 계획을 제안한다. 모델 미정인 검토 패키지를 유효하게 유지하며 실행자 기본 모델을 호출하지 않는다. 실행자는 조정자가 선택한 공급자의 소유자(`fal-video-production`은 Fal이 선택된 경우에만)다.

`type=video_execution_plan` artifact는 승인된 검토 패키지 버전에 의존하고, 샷 범위·모델/endpoint·시간/재시도/비용 상한을 명시한다. 이 범위에 대한 실제 사용자 실행/과금 승인 근거를 기록한 뒤에만 선택된 실행자에게 인계한다. 프리프로덕션 수락·이미지 승인·검사 성공·일반적인 제작 요청은 이 승인을 대신하지 않는다. 게시도 별도 사용자 요청이다. 최소 충분 영상 프리뷰와 최종 제작 승격 절차는 [생성 경로·비용 라우팅 §5](24-production-execution.md)를 따른다 — 프리뷰 승인은 최종 실행 허가가 아니다.

에셋 내용/버전이 바뀌면 영향을 받는 검토/실행 계획을 stale로 표시하고 기존 승인 근거를 이력으로 보존한다. 변경된 버전에 이전 승인을 붙이지 않는다. 영향받는 부분만 재검토받는다. 상위 버전 불일치가 남아 있으면 준비 완료나 실행 가능으로 보고하지 않는다.
