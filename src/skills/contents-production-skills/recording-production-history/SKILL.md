---
name: recording-production-history
version: 1.18
description: "Use when content production writes intermediate or final files, when a project needs a chronological audit of actual prompts, methods and outputs, or when interrupted production must be resumed."
---

# 제작 이력 기록

## 이 스킬이 하는 일

실제 파일이 만들어지는 제작 작업이 시작될 때 프로젝트 저장 루트를 초기화하고, 각 생성·검사·납품 작업을 `.history/events/`의 순차적 Markdown 이벤트로 기록한다. 이벤트는 파일 이름 순서가 시간 순서와 일치하는 append-only 기록이며, 중단된 실행은 `status`가 in-flight로 노출하고 `resume`이 중단과 재개를 명시적으로 남긴다. 완료를 꾸미지 않는다.

**`.history`는 감사 기록이지 승인 원장이 아니다.** `<project-root>/project.json`이 정본이며 이 도구는 init 이후 그 파일을 쓰지 않는다. 이 스킬은 산출물의 품질·QA·승인을 판정하지 않는다 — 이벤트 기록은 검수 통과가 아니며, 승인·검수 상태는 기존 계약과 검증 도구가 담당한다. 조정자(orchestrator)가 이 도구를 호출해 각 단계의 사실을 남긴다.

## 저장 위치 (cross-platform)

기본 루트는 실제 사용자 Documents 폴더 아래 `studio_production/<project-id-slug>`다:

- **Windows**: `SHGetKnownFolderPath(FOLDERID_Documents)` — 리디렉션·OneDrive 위치를 존중한다. 레지스트리 fallback, 마지막으로 `~\Documents`.
- **macOS**: `~/Documents`.
- **Linux**: `XDG_DOCUMENTS_DIR`이 `user-dirs.dirs`에 설정돼 있으면 그 값, 없으면 `~/Documents`.
- `STUDIO_DOCUMENTS_DIR` 환경 변수나 `--documents PATH`로 재정의 가능.

사용자 홈·사용자 이름 하드코드 없다. 생성된 산출물·이력 파일은 이 리포지토리/스킬 디렉터리 안에 두지 않는다.

## CLI 계약

모듈로 import할 때는 `resolve_project_root(project_id, documents=None) -> Path`와 `initialize_project(project_id, documents=None, *, project_root=None) -> dict`를 노출한다(반환 dict는 init CLI와 동일 키).

```
python scripts/production_history.py init --project-id ID [--documents PATH | --project-root ROOT]
```

`--project-id`는 파일명으로 slug화된다. `--documents`와 `--project-root`는 동시에 지정할 수 없다. `--project-root`는 전달한 폴더를 그대로 사용하며 `studio_production/<id>`를 덧붙이지 않는다. 기존 `project.json`은 같은 project_id인지 확인하고 보존한다.
유니코드 문자·숫자·`_`·`-`·`.`·공백은 허용하므로 한글 프로젝트명도 된다. `..`, 경로 구분자(`/`, `\`), Windows 예약 장치명(CON, PRN, AUX, NUL, COM1–9, LPT1–9), 대소문자만 다른 충돌 이름(casefold 비교)은 거부된다.

v5.1 패키지 레이아웃(`00_MANIFEST`…`10_DELIVERY`, `02_MASTER_ASSETS/*`, `03_SCENE_PACKS`, `04_STORYBOARDS`, `05_SHOT_CARDS`, `06_GENERATION_SPECS`, `07_AUDIO`, `08_ANIMATIC_EDIT`, `09_QA`, `10_DELIVERY`)과 `.history/events`, `.history/prompts`를 만든다. 스켈레톤 `project.json`(schema_version 1.1)은 같은 폴더의 staging 파일에서 완성한 뒤 exclusive atomic link로 게시한다. 중단된 staging 파일은 정본이 되지 않으며 기존 정본은 덮어쓰지 않는다. JSON 결과는 실제 절대 경로를 반환하며 init은 멱등이다.

```
python scripts/production_history.py record --project-root ROOT
    --operation TEXT --prompt-file FILE
    --status started|completed|interrupted|failed
    [--output PATH ...] [--operation-id ID] [--attempt N]
    [--tool NAME] [--model NAME] [--method TEXT] [--details TEXT]
    [--scope operation|project]
```

각 operation 이벤트는 UTC·로컬 타임스탬프, operation 텍스트, 파일에서 읽은 실제 프롬프트 전문(스냅샷은 `.history/prompts/`에 별도 보관, 원본 SHA-256 기록), tool/model/method, 선언된 output의 절대 경로와 존재 여부를 기록한다. `--scope operation`에는 `--prompt-file`이 필수다 — 프롬프트 없는 operation 이벤트는 거부한다.

**전이 규칙 (operation 이벤트):**

- `terminal`(`completed`/`interrupted`/`failed`)은 해당 operation_id에 pending `started`가 있을 때만 기록된다 — 시작 없는 종결은 거부된다.
- in-flight operation에 `started`를 다시 기록하면 거부된다. 종결 후 재시작만 새 attempt다. 명시 attempt ID는 operation 안에서 재사용할 수 없고, 자동 ID는 사용된 정수 중 최댓값 다음 번호를 선택한다.
- `completed`의 `--output`은 모두 프로젝트 루트 안의 실제 파일이어야 한다 — 존재하지 않는 산출물 선언은 거부된다. `failed`/`interrupted`는 미존재 output을 기록할 수 있다.
- 모든 `--output`(절대·상대)은 프로젝트 루트 안으로 해석돼야 하며 루트 밖으로 빠지면 거부된다.

`--scope project`는 프로젝트 수준 라이프사이클 이벤트(예: 최종 완료 선언)로 operations 상태와 구분된다. **project `completed`는 in-flight operation이 없고, 각 operation이 요청한 output이 디스크에 실제 존재할 때만 기록된다** — 중단(interrupted)이나 미생산 output이 남은 채 완료를 선언할 수 없다.

```
python scripts/production_history.py status --project-root ROOT
python scripts/production_history.py resume --project-root ROOT
    [--operation-id ID] [--details TEXT]
```

`status`는 operation_id별 최신 상태와 아직 terminal 이벤트가 없는 `in_flight` 목록을 JSON으로 돌려준다. 읽기 전용 조회이므로 `.history/`가 아직 없으면 빈 상태를 반환해도 폴더나 파일을 만들지 않는다. `resume`은 in-flight operation들을 명시적으로 `interrupted`로 마크하고(완료로 위장하지 않는다) `resumed` 라이프사이클 이벤트를 남긴다. `--operation-id`를 주면 해당 operation만 마크하며, in-flight가 아니면 거부한다. 이전 writer가 죽어도 lock은 OS가 해제하므로 stale lock이 재개를 막지 않는다.

모든 명령은 실제 절대 경로를 포함한 JSON 하나를 stdout에 출력하고, exit code는 0 성공 / 2 사용·프리플라이트 오류 / 1 I/O 실패다.

## 이벤트 파일 규칙

- 이름: `NNNNNN-YYYYMMDDTHHMMSSffffff-<operation-id>-<event>.md` — 정렬하면 시간 순서다. 타임스탬프는 마이크로초까지 기록해 실제 상관관계를 보존한다.
- event 종류: `started`, `completed`, `interrupted`, `failed`, `resumed`. `completed`/`interrupted`/`failed`가 terminal이다.
- 쓰기는 프로젝트별 OS advisory lock으로 직렬화된다(Windows `msvcrt`, POSIX `fcntl`). 한 writer가 다른 writer의 이벤트를 덮을 수 없고, writer가 죽으면 lock은 OS가 해제한다.
- 파일은 staging 후 atomic link로 게시되므로 프로세스가 죽어도 빈/부분 이벤트 파일이 남지 않는다. 이벤트는 불변으로 취급한다 — 수정 대신 다음 이벤트를 남긴다.
- `status.operations[ID].attempts`는 operation-scope `started` 이벤트 수이고, `current_attempt`는 실제 상관관계 ID다. 프로젝트 라이프사이클 이벤트는 이 수에 포함하지 않는다.
- 프롬프트 파일은 원본 bytes로 먼저 SHA-256을 계산한 다음 UTF-8로 decode·mask한다. JSON quoted secret 및 공백·`=`/`:` 주변 설정 형식은 비밀값만 숨긴다.
- history/events/prompts/lock과 산출물 경로는 실제 목적지까지 검사해 root 밖 symlink/junction을 거부한다. project_id·operation_id·파일명은 slug화되고 `..`/경로 구분자·예약 장치명·casefold 충돌은 거부한다.

## 언제 쓰나

- 사용자가 파일 기반 제작(스토리보드, 마스터 에셋, 생성 스펙, 영상 산출)을 요청해 실제 쓰기가 일어날 때 init을 먼저 수행하고, 각 미디어·파일 생성 작업을 record한다.
- 중간 산출물과 최종 산출물 모두 `--output`으로 등록하고 사용자에게 실제 절대 경로를 보고한다.
- 프로세스가 죽은 뒤 재진입하면 `status`로 in-flight를 확인하고 `resume`으로 중단을 명시한 뒤 작업을 다시 시작한다.

## 언제 쓰지 않나

- 채팅 전용 답변, 파일 없는 설계 논의, 단일 텍스트 산출물에는 저장 초기화를 하지 않는다(chat-only는 no-save).
- `.history`를 승인 증거·품질 판정·QA 검증·project.json 대체로 사용하지 않는다.
- 실행 증거가 없는 과거 사건은 새 `started`/`completed`로 재연하지 않는다. 사용자가 전한 과거 이력은 사용자 보고로 구분하고 원래 시각·출처·미검증 상태를 설명한다. 파일 존재는 생성 주체·방법·QA 성공의 증거가 아니다.

## 종속성

Python 3.10+ 표준 라이브러리만 사용한다. 외부 패키지 없다.

## Acceptance

An event is recordable only when it describes an operation that actually occurred and its status matches the observed outcome. Every declared output must be reported with its resolved path and existence state; completed outputs must exist within the project root. A project may be marked completed only when no operation is in flight and every requested output exists. Missing prompt, nonexistent operation, out-of-root path, or unobserved result is a blocker; never backfill an invented event.

