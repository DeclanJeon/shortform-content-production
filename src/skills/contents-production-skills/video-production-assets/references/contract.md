# 공통 제작 계약 v1.1

## ID와 단위
- project_id는 프로젝트 내 고유값으로 정한다. scene_id S01, beat_id B01, shot_id SH01, character_id CH01, claim_id CL01, sound_id AU01, artifact_id A01 형식을 권장하되 기존 체계를 우선한다.
- 시간은 초 단위 숫자로 기록한다. 최종 타임라인은 start_s 포함 / end_s 미포함이다. fps는 초당 프레임 수다. 프레임 범위는 시작 포함 / 끝 미포함이며 길이는 (end-start)/fps다.
- 생성/촬영 원본 길이 source_duration_s와 최종 편집 구간 길이를 구분한다. final 구간은 겹치지 않는다. 전환과 오디오 중첩은 별도 레이어 큐로 표현한다.
- 기본 계약의 최종 영상 구간은 0초부터 target_duration_s까지 연속으로 덮는다. 블랙·정지·타이틀도 하나의 shot으로 기록한다.

## 상태와 버전
- artifact 상태: draft / reviewed / approved / generated / verified / stale.
- approved는 실제 사용자 또는 위임된 승인 사실이 있을 때만 기록한다. generated는 실제 파일이 생성됐을 때, verified는 해당 파일을 검사했을 때만 기록한다.
- 버전과 dependency를 기록한다. 상위 각본이 바뀌면 영향을 받는 샷과 계획을 stale로 표시한다.
- `assets/project-template.json`의 빈 배열과 null은 작성 시작 상태다. 검사 통과 예제가 아니다.

## JSON 구조
필수: project_id, version, target_duration_s, fps, aspect_ratio, scenes, characters, shots, claims, artifacts.
scenes: id, purpose. characters: id, locked_traits.
shots: id, scene_id, character_ids, start_s, end_s, source_duration_s, purpose, start_state, end_state.
claims: id, statement, kind(fact/inference/fiction), source(실제 문서/데이터 위치), status(unverified/verified/fiction).
artifacts: id, type, version, status, dependencies(artifact id 배열), evidence(검수 위치; verified 상태 필수).

## 인계 최소값
각 산출물에 입력 ID와 버전, 생성한 항목 ID, 가정, 실제 검증 결과, 다음 단계 미해결 사항을 붙인다. 재생성에는 바뀐 변수와 실패 타임코드를 남긴다. 같은 정보를 복사해 여러 문서에서 따로 변경하지 말고 공통 원장을 기준으로 조회한다.

## Worker handoff and single writer

현재 단계에 필요한 조각만 인계한다. 이 패킷은 일시적 실행 문맥이며 새 원장/스케줄러가 아니다. 단독 채팅 산출물은 project.json·프로젝트/산출물 ID를 새로 요구하지 않는다. 기존 프로젝트라면 선택된 `<project-root>/project.json`이 정본이고, 설치된 skill/support 폴더에는 제작 데이터를 쓰지 않는다.

**입력 패킷:** 실제 project_root/project_id/project version(존재할 때), 한 개의 assigned stage와 requested output, 담당 artifact/entity/scene/shot/beat/panel/cue ID와 수정 범위, 소비하는 artifact ID→version, 필요한 소스/범위, 잠근 사실·연속성·제외 조건, 관련 승인 근거, 미해결 입력. 이미 확정된 값을 재질문하지 않고 다른 단계의 상세 문서를 미리 읽지 않는다. 별도 원장 없이 필요한 필드만 조회한다.
source/character/beat/provider ID는 artifact ID가 아니다. dependencies/dependency_versions/input_versions에는 **정본에 등록된 artifact ID만** 넣는다. 등록 여부를 확인하지 못한 입력은 source locator 또는 등록 제안으로 반환하며 실제 dependency라고 꾸미지 않는다.

**워커 반환:** 담당 output ID와 output version, dependencies/dependency_versions 제안, 바뀐 레코드 제안, 실제 파일/공급자 locator와 관찰 상태·검수 근거/한계, 가정과 blocker. ID는 배정된 값 또는 명시적 신규 등록 제안으로 구분한다. 부족한 공간/음성/참조 입력은 scene/shot/beat ID별 요구 패킷 한 개로 반환한다. 워커가 총괄을 재호출하거나 현재 범위를 넓히지 않는다.

**총괄만 작성:** 저장·변경이 허용된 기존 프로젝트에서 현재 입력 버전을 다시 읽고 stale 결과를 거부한다. 담당 변경과 해당 소유 artifact 버전만 반영하고 종속 승인만 stale로 표시한다. 저장 후 다시 읽어 검사하고 다음 배정 단계에 넘긴다. 워커는 project.json을 동시 수정하지 않는다. 텍스트만/저장 금지 요청은 반환 제안에서 끝난다. 초기 프로젝트 선택·초안 작성은 기존 프리프로덕션 절차이며, 아래 도구는 자동 생성/자동 실행 엔진이 아니다.

- `python scripts/project_index.py <project-root>/project.json --shot SH06` 또는 `--artifact A01`: 정본에서 현재 샷의 scene/character/beat/panel/voice/audio/claim/asset와 provenance·artifact dependency를 읽는 **읽기 전용 조회**. 샷/산출물을 지정하지 않으면 전체 원장을 자동 로드하지 않는다. 결과는 전체 브리프나 실행 승인 대신 사용할 수 없다.
- focused `project_index.py` 전달에는 panel pins, `required_asset_versions`, `master_asset_ref` 전체 계보, 활성 `look_asset_id`와 해당 에셋을 포함하고 무관한 샷/산출물은 제외한다.
- `python scripts/update_project.py <project-root>/project.json --update update.json`: **총괄 전용**, 구조가 유효한 기존 원장의 upsert 도구. 입력은 `{project_id, base_version, version, input_versions, owner_artifact_ids?, changes, storyboard?, preproduction?}`. changes는 기존 테이블 이름→부분 레코드 배열이며 기존 ID의 미변경 필드는 보존한다. storyboard는 synopsis_artifact_id와 beats/panels upsert, preproduction은 아래 패키지 포인터의 부분 갱신만 지원한다. 생성/삭제·프로젝트 선택·그 밖의 헤더 설계·승인 추론은 하지 않는다.
- 내용 레코드(씬/인물/샷/주장/에셋/오디오/자막/보드)를 바꾸면 패킷에서 배정한 `owner_artifact_ids`와 신규/증가한 소유 artifact 버전을 함께 준다. 상태·증거만 갱신할 때 내용 버전을 억지로 바꾸지 않는다. 소유 관계와 사용자 권한은 총괄이 실제 문맥에서 판단한다; 도구가 진위를 인증하지 않는다.
- 업데이트 도구는 base/input 버전, 레코드 참조/시간, 파일 루트, 후보 전체 구조를 확인한 뒤 같은 폴더 임시 파일→원자 교체한다. 협력 작성자 lock과 교체 전 원본 재확인으로 오래된 결과·중복 작성·관찰된 외부 수정을 거부한다. 기존 lock을 자동 제거하거나 재시도하지 않는다. 비협력 편집기의 마지막 순간 경합/정전 내구성까지 보장하는 DB는 아니다.
- 상위 버전 변경은 실제 dependency 그래프를 따라 stale을 전파한다. stale artifact는 **이전 입력 버전**을 보존할 수 있으나 현재 승인/실행에 쓰지 않는다. 현재 버전으로 다시 만든 레코드는 명시적으로 등록하고 재검수한다. 독립 승인과 정상 컷은 보존한다.
- `storyboard.panels[].asset_version_refs`의 버전 변경은 해당 에셋이 `required_asset_versions`에 없어도 보드 artifact의 입력 변경이다. 그 보드와 실제 종속 downstream만 stale 처리하고 과거 핀은 그대로 보존한다. 무관한 분기는 현재 상태를 유지한다.

공급자 upload/job/Soul/site/folder ID와 URL은 외부 locator이며 canonical asset ID가 아니다. 실제 파일을 얻고 등록한 available/verified asset만 result_asset_id로 쓴다. Brandkit 상태·blocks.json·camera_spec.json·분할 manifest는 각각 domain authority/파생 adapter로 연결하고 project.json과 경쟁하는 전역 원장을 만들지 않는다.
- `python scripts/package_production.py <project-root>/project.json --base-dir <project-root> --output <새-출력> [--zip] [--require-final]`: 원장과 실제 파일에서 v5.1 패키지 트리/ZIP를 조립하는 **파생 출력 도구**. 원장을 쓰지 않고 출력 경로를 덮어쓰지 않으며, 매니페스트는 정본에서 재생성한다.

## v1.1 구조와 검사 프로필
`schema_version`은 `1.1`을 사용한다. 이전 `1.0` 입력도 일부 호환되지만 새로운 프로젝트는 v1.1 필드를 작성한다.

카메라·공간 명세를 연결할 때는 [카메라 공간 계약](../../camera-spatial-design/references/spatial-contract.md#json)의 별도 `schema_version=camera-spatial-1.0`을 사용한다. `camera-spatial-design/scripts/spatial_spec.py`가 이 문자열을 검사한다. 프로젝트 schema `1.1`, 카메라 schema `camera-spatial-1.0`, 각 artifact의 내용 `version`은 서로 다른 필드다. 카메라 schema `0.9`를 임의 수락하지 않으며, 프로젝트 `1.0`의 기존 호환 입력 허용을 새 프로젝트의 `1.1` 계약 충족으로 보고하지 않는다.

- plan: 필수 제작 정보·시간·참조·상태 기록을 검사한다. 생성 전 자료와 source_duration_status=estimated를 허용한다.
- preproduction: 저장한 전체 패키지의 필수 산출물·실제 Markdown/이미지·전체 보드·인물 페르소나/SSOT·참조와 해시를 검사한다. `--base-dir` 필수, 이미지 모드에는 Pillow 필요. 예술성·동일 얼굴·권리·사용자 승인 진위를 인증하지 않는다. plan 성공은 이 준비 완료 판정을 대신하지 않는다.
- delivery: plan 검사에 실제 파일 존재·해시·측정 시간·열린 blocker·납품 검사 기록을 추가한다. `--base-dir`을 프로젝트 폴더로 지정한다. 파일 재생, 디코딩, 음량 측정, 사실의 진위나 동의의 진위는 이 코드가 확인하지 않는다.
- 필수 테이블 scenes/shots/artifacts는 비어 있으면 안 된다. 등장인물이 없는 영상은 characters=[]를 허용한다.
- scene.purpose는 장면 기능, character.locked_traits는 문자열 또는 비어 있지 않은 목록/객체로 기록한다.
- target_duration_s와 shot.start_s/end_s는 fps 기준 프레임 경계에 맞춘다. 초는 실수로 허용하고 반올림 오차를 제한한다.
- shot.source_duration_status: estimated/measured. 계획의 원본 길이는 추정이며 실제 생성/촬영 후 측정한다.
- shot.source_in_s는 선택 테이크의 소스 시작 초, playback_rate는 소스 초/최종 초(기본 1). 배속·슬로모션에서 필요한 소스 길이는 source_in_s+(end_s-start_s)*playback_rate다.
- shot.asset_ids는 asset_registry의 선택된 참조 ID 목록이다. 프롬프트의 경로·룩·의상·소품은 CSV 연속성 원장과 맞춘다.
- artifact.dependencies는 ID 목록이고 dependency_versions는 **정확히 그 ID들**의 {artifact_id: 입력 버전} 객체다. 선언되지 않은 키는 오류다. 현재 산출물은 상위 버전과 일치해야 하고, stale 산출물만 과거 입력 버전을 보존할 수 있다.
- approved artifact는 approval={by, at, evidence}를 기록한다. 승인자·시간·근거는 실제 기록을 사용한다. 생성/검증 산출물에는 asset_ids, verified에는 evidence가 필요하다.

## 전체 프리프로덕션 패키지

단일 텍스트/프롬프트 요청은 이 확장을 요구하지 않는다. 전체 이미지 기반 제작은 **시놉 MD, 인물별 페르소나·SSOT MD와 실제 한 장짜리 Identity Sheet A, 통합 기술 콘티 MD, 모든 패널을 이야기 순서로 덮는 합본 스토리보드 이미지 세트(시트당 최대 8패널, 순서 있는 1장 이상)**를 인계한다. 캐릭터 없는 작품은 인물 산출물만 비적용이다. 텍스트 전용 전체 설계는 이미지 제출/파일/지출을 추가하지 않는다.

- `preproduction={mode:text|image_backed,synopsis_artifact_id,storyboard_artifact_id,storyboard_sheet_artifact_ids,storyboard_split_asset_id}`를 기존 project.json에 둔다. 이미지 모드에는 합본 시트 artifact ID의 **정렬된 배열**이 필수다. 한 장도 배열로 기록하며 기존 단수 선언은 같은 ID의 한 항목 배열로 이전한다. 시놉 type은 synopsis, 보드 type은 storyboard이며 각각 정확히 한 실제 `.md` 에셋을 연결한다.
- 전체 패키지의 각 `characters[]`에는 `persona={role,personality,observable_behavior,speech}`, `ssot_artifact_id`를 둔다. 네 페르소나 값은 구체적인 비지 않은 문자열이며 무대사에는 이유를 적는다. character_sheet artifact는 시놉 입력 ID/버전과 실제 SSOT MD 에셋 하나를 연결한다. 공급된 캐릭터는 원본을 보존하고 시놉 적용 범위의 인계 시트를 버전으로 연결한다.
- 이미지 모드의 `identity_sheet_asset_id`는 kind=character_identity_sheet, entity_type=character, 해당 entity_id의 실제 이미지다. 전신 정면/3·4/측면/후면 + 얼굴 정면/3·4/측면의 7뷰를 한 장에 구성하고 각 뷰의 고정 앵커를 실제 검사한다. 장면 초상 하나는 대체물이 아니다. 해당 에셋의 `source_asset_ids`에는 그 캐릭터 SSOT artifact가 가리키는 실제 Markdown asset ID가 포함돼야 하며, 검사기가 연결을 확인한다. 인물이 등장하는 모든 샷의 asset_ids에 이 정본을 연결한다.
- storyboard artifact는 현재 시놉과 모든 캐릭터 character_sheet의 정확한 버전에 의존한다. 각 샷/패널에 원문·beat/scene/shot/panel/캐릭터·페르소나/SSOT·카메라·시간·실제 이미지 참조를 통합한다. 각 패널은 해당 beat·가시 캐릭터·audio cue·speech ID를 직접 기록한다.
- 합본 `type=storyboard_sheet` artifact는 보드의 현재 버전에 의존하고 `kind=storyboard_sheet` 이미지 에셋 정확히 하나를 연결한다. PNG의 `storyboard_sheet.panel_traceability` metadata와 표시 캡션은 panel별 ID 매핑을 담는다. 입력 시트/crop은 합본 출력의 대체물이 아니다.
- 이미지 모드의 `storyboard_sheet_artifact_ids`는 이야기 순서로 정렬된 `type=storyboard_sheet` artifact 배열이다. **한 시트에는 최대 8개 패널**만 둔다 — 패널 수·정보 밀도·씬 경계로 장수를 정하고 한 장에 욱여넣지 않는다. 각 시트 artifact는 보드의 현재 버전에 의존하고 `kind=storyboard_sheet` 이미지 에셋 정확히 하나를 연결한다. 각 시트 에셋의 `panel_ids`는 자기 구간의 정본 panels 순서이며, 모든 시트를 순서대로 이어 붙이면 정본 전체 순서와 일치한다(누락·중복 없음). 시트가 두 장 이상이면 각 에셋에 `sheet_index`(1부터)·`sheet_count`를 기록한다. `source_asset_ids`는 그 시트 패널의 실제 픽셀 입력(source_sheet_asset_id가 있으면 그 ID, 없으면 image_asset_id)을 같은 순서로 담고 `source_sha256`는 해당 입력 ID→현재 해시 맵이다.
- `render_storyboard_sheet.py <project.json> --base-dir <root> --output 04_STORYBOARDS/sheets/<새.png> [--font <font.ttf>] [--panels-per-sheet 1..8]`는 clean 패널과 외부 촬영 캡션을 순서 있는 PNG 세트로 조립한다. 각 시트의 실제 path/hash/패널·소스 매핑과 동일 traceability PNG metadata를 반환하며 원장을 쓰지 않는다. 총괄이 모든 시트를 등록하고 실제 합본을 연다.
- 합본을 만든 직후 `split_storyboard.py --sheet <프로젝트-relative 시트>`를 이야기 순서로 반복 지정해 장면 overview와 clean 패널을 추출한다. 실제 JSON manifest를 `kind=storyboard_split_manifest`로 등록하고 `preproduction.storyboard_split_asset_id`에 연결한다. 각 추출 이미지도 실제 경로·SHA-256으로 등록한다. manifest의 `file`은 manifest 폴더 기준, `sheet_path`·`sheets[].path`·`band_sources`는 프로젝트 root 기준 상대 경로다. 원본 패널 입력을 추출 이미지로 바꾸지 않는다.
- preproduction/FINAL은 manifest의 주장만 신뢰하지 않는다. 각 panel/scene의 실제 등록 파일·SHA-256, canonical scene/shot/panel, source asset/version/hash 및 asset pins가 서로 일치해야 하며 다른 패널 파일을 alias한 split은 거부한다.
- `package_production.py --require-final`은 명시적인 현재 FINAL 보드, 완료된 이미지 기반 패키지, 정렬된 현재 시트/hash와 일치하는 split manifest, 전체 scene/panel 이미지의 실제 등록을 요구한다. ZIP의 root `project.json`은 휴대 가능한 파생 스냅샷이며 registry 경로가 실제 패키지 파일을 가리킨다. 표준 폴더 경로는 유지하고, 구형 평면 경로는 asset ID를 포함해 충돌 없이 배치한다. CSV manifest는 정본의 파생물이고 모든 실제 패키지 파일을 열거한다.
- ZIP 재개봉은 asset registry 경로와 split 내부 `file`/`sheet_path`/overview source 참조를 같은 relocation map으로 재투영하고 파생 snapshot hash를 갱신한다. 원본 정본은 변경하지 않으며, 실제 복사 byte stream이 등록 지문과 다르면 게시를 중단한다.
- 모든 필수 실제 파일에는 SHA-256을 기록한다. 이미지 디코딩·현재 종속 버전·전체 커버리지·열린 blocker도 검사한다. reviewed/approved인 preproduction_review 또는 approved인 video_execution_plan에는 plan에서도 이 완결 게이트를 자동 적용하며 이미지 모드를 요구한다. 검토는 시놉·보드·합본을 dependency 그래프로 소비해야 한다.
- 누락 상태는 draft/stale로 보존하고 strict preproduction 검사에서는 준비 미완료로 반환한다. 프롬프트·가상 path·planned 에셋·검사 기록만으로 실제 산출물이라고 표시하지 않는다. 구조 통과 뒤에도 캐릭터 7뷰 정체성, 전체 clean 컷 읽힘, 합본 누락/가독성, 장면 내 제품·빛·그림자·반사 통합을 실제 검수하고 사용자 수락을 별도로 받는다.


## 실제 파일과 부가 시간표
- asset_registry: {id, kind, version, path, status, sha256}. 실제 path는 프로젝트 폴더 기준 상대 경로를 권장하며 `--base-dir` 검사에서 resolve한 결과가 그 폴더 밖이면 거부한다(절대 경로/..//symlink 포함). planned는 path=null을 허용한다. available/verified는 실제 경로를 갖고 base-dir가 있으면 파일 존재와 제공된 해시를 확인한다. 최종 파일에는 verified와 SHA-256이 필요하다.
- asset_registry 선택 provenance 필드(있을 때만 채운다): entity_type(character/product/prop/location/wardrobe/look/lighting/graphic/environment), entity_id, authority(authoritative/inferred/derived), source_asset_ids, prompt, provider, model, workflow, result_asset_id. 사용자가 준 SSOT·바이블은 authoritative로 기록하고 임의로 변경하지 않는다. 확인되지 않은 provider/model을 기록하지 않는다.
- source_asset_ids/result_asset_id는 실제 asset_registry ID를 참조한다. entity_id가 있으면 비어 있지 않은 문자열이어야 하고 entity_type=character는 characters의 ID와 일치한다. 캐릭터 Identity Sheet A의 source_asset_ids에는 해당 캐릭터 SSOT artifact의 유일한 Markdown asset ID가 반드시 포함된다. 다른 entity_id는 해당 전문 바이블 locator로서 실제 소스를 인계하며 새 임의 테이블을 강제하지 않는다. shot.claim_ids가 있으면 claims의 실제 ID를 참조한다.
- shot.retry_budget: 최초 생성 이후 허용된 최대 재시도 수(선택, 0 이상 정수). 승인된 video_execution_plan의 상한에서 온다. 0이면 최초 1회만 허용하고 attempt의 최대값은 1+retry_budget이다. 필드 부재는 무제한 실행 허가가 아니다.
- generation_attempts: {id, shot_id, attempt(최초=1, 이후 샷별 중복 없는 양의 정수), route, model?, changes?, observed_failures?, preserve?, result(accept/reject/conditional), result_asset_id?, failure_class?, notes?}. 실제 시도만 기록한다. accept는 available/verified인 실제 결과 에셋 ID를 참조해야 하며 planned 에셋을 성공 결과로 기록하지 않는다. 기록/구조 검사 자체가 실행 승인이나 미디어 품질 검사는 아니다.
- audio_mode: no_audio / no_dialogue / dialogue. audio_cues: {id, start_s, end_s, layer, shot_id?, asset_id?}. 소리는 겹칠 수 있지만 전체 영상 구간을 벗어나지 않는다.
- captions: {id, start_s, end_s, text, shot_id?}. 오디오 음소나 자막 타이밍의 의미 적합성은 실제 재생으로 검사한다.
- issues: {id, severity: blocker/major/minor, status: open/resolved, evidence?, fix?}.
- delivery_spec: {destination, width, height, container, video_codec, caption_mode: none/sidecar/burned_in, final_asset_id, caption_asset_id?}. 기존 소스에서 현재 목적지 규격을 추정하지 않는다.
- delivery_checks: {id, category: playback/timing/visual/audio/captions/continuity/claims, status: pass/fail/unverified/not_applicable, evidence?, reason?}. pass는 실제 검사 위치, N/A는 비적용 이유를 갖는다. 실제 확인하지 않고 pass를 적지 않는다.

## 버전 변경과 범위
변경된 산출물과 그 하위 인계를 stale로 표시하고 입력 버전을 갱신한 뒤 관련 항목만 재검수한다. 계획 패키지가 완성됐어도 실제 영상이 완성된 것은 아니다. 참고 후보·예산·도구 기능이 미정이면 계획에서 가정으로 남기고 실제 실행 직전에 확인한다.

## 프리프로덕션 검토와 영상 실행 승인
[프리프로덕션·검토 절차](preproduction-review.md)의 `review.md`는 `type=preproduction_review` artifact로 등록하고 검토 대상의 ID·버전을 dependencies/dependency_versions로 연결한다. 실제 사용자 수락 때만 approved와 approval 근거를 기록한다. 문서·이미지의 generated/verified 상태는 사용자 수락을 뜻하지 않는다.

별도 `type=video_execution_plan` artifact는 승인된 검토 패키지 버전과 명시적 샷·모델/endpoint·실행/재시도/비용 상한을 참조한다. 실제 사용자 실행 승인 뒤에만 approved로 기록한다. 프리프로덕션 중 모델/가격 미정은 허용하지만 실행자 기본값으로 보충하지 않는다. 변경된 에셋의 종속 검토/실행 계획은 stale로 표시하고 새 버전에 이전 승인을 재사용하지 않는다. 사용자 승인과 파일 존재/구조 검사는 별개의 증거다.
approved 영상 실행 계획은 dependency 그래프에 현재 approved인 preproduction_review를 반드시 포함한다. 이 구조 검사는 실제 사용자 권한·비용·음성/이미지의 품질을 인증하지 않는다.

프리뷰와 최종은 별도 허가다. `video_execution_plan`의 승인 범위에 프리뷰 단계가 포함되면 승인된 프리뷰 입력 버전·설정·샷/출력 수·상한을 그대로 기록하고, 프리뷰 승인만으로 최종 생성·추가 과금을 허가하지 않는다. 프리뷰 실제 출력·검수 결과·수락 근거는 연결된 artifact(result_asset_id, generated/verified, evidence)로 남기고, 승인된 최종 계획은 프리뷰 판정과 최종 승격 경로를 dependencies로 참조한다. 프리뷰와 최종 범위를 하나의 plan 버전으로 함께 승인한 경우에만 최종이 같은 승인 안에서 현재 입력 버전·조건·상한으로 진행되며, 그렇지 않으면 현재 버전의 별도 최종 승인이 필요하다. 같은 seed·입력의 재생성은 같은 동작·정체성을 보장하지 않으므로 최종본은 다시 검수한다.

## 선택적 상세 스토리보드 확장
제작용/상세 콘티와 전체 보드 시트에는 [완전한 스토리보드 계약](storyboard-contract.md) §8의 `storyboard` 객체와 shot 기술 필드를 정본 `project.json`에 추가한다. 기존 schema_version=1.1 및 일반 plan/delivery 프로필은 유지한다. 별도 `validate_storyboard.py`는 전체 비트/씬/샷/패널 연결과 제작 슬롯을 검사하며 실제 이미지에는 `--require-images --base-dir`를 적용한다. 합본 crop/clean 컷은 `split_storyboard.py`의 파생 결과를 asset_registry에 등록하고 같은 ID로 연결한다. CSV/시트/분할 manifest는 조회·등록용 투영이며 별도 원장이 아니다. 의미 검수·권리·사용자 수락을 파일 검사로 대체하지 않는다.

## v5.1 프로덕션 무결성 (LOOK·에셋 게이트·계보·provenance·패키지)

선택 확장이 아니라 전체 이미지 기반 패키지와 그 다운스트림(씬 상태·보드·생성 명세·애니매틱)에 적용한다. 기존 schema_version=1.1을 유지하고 아래 필드는 모두 선택이지만, `final`을 선언하는 순간 해당 게이트는 강제다.

### 활성 LOOK과 스타일/월드 바이블
- `look_asset_id`는 루트 필드로, `entity_type=look`(또는 미분류)의 asset_registry 에셋을 가리킨다. LOOK은 **하나만 활성**으로 둔다 — 프로젝트 전체 시각 문법(매체·리얼리즘·비율·팔레트·조명 철학·카메라 문법·스타일 드리프트 금지)을 버전으로 잠근다. 바이블 문서에는 `assets/style-world-bible-template.md`를 쓴다.
- 모든 시각 생성 에셋은 활성 LOOK 버전을 참조한다. LOOK 에셋의 내용이 바뀌면 버전을 올리고 종속물을 stale 처리한다. 패널이 활성 바이블에 없는 새 시각 스타일을 발명하지 않는다.

### 마스터·파생 에셋 계보
- `asset_registry` 선택 필드 `role: master|derivative`와 `master_asset_ref: {asset_id, version}`을 둔다. 반복 정체성 에셋(캐릭터 마스터·장소 마스터·소품 마스터)은 한 번만 master로 등록하고, 씬별 상태(젖음·파손·의상·조명·배치)는 derivative로 등록해 마스터 ID와 **정확한 버전**을 핀한다. 여러 씬 폴더에 같은 마스터의 편집 가능한 사본을 두지 않는다.
- 마스터 버전이 올라가면 그것을 핀하는 derivative는 자동으로 stale가 되고(재핀하기 전까지), 이 계보를 따라 이어지는 derivative도 연쇄로 stale가 된다. 비stale 에셋이 옛 버전을 핀하거나 stale 마스터에서 파생되면 검사가 거부한다.
- 캐릭터 마스터는 7뷰 정체성 시트 외에 서사 관련 표정·의상(WD ID)·DO-NOT-CHANGE 락을 포함한다. 장소 마스터는 생산상 중요한 장소에 4공간뷰(establishing/reverse/lateral/top-down)와 출입·창·장애물·광원 앵커를 둔다 — 수치는 추정이면 `ESTIMATED`로 명시한다. 소품/도구/제품은 연속성이 문제가 될 때만 전/후/측/3/4·상태 변형·스케일 참조를 둔다. 장식 보드를 위해 에셋을 만들지 않는다.

### 에셋 게이트: preliminary vs final
- artifact 선택 필드 `finality: preliminary|final`과 `required_asset_versions: {asset_id: version}`을 둔다. finality를 생략하면 preliminary다.
- `final` 선언은 **생성 전 잠금 게이트**다: 참조 에셋이 전부 등록돼 있고 status가 **`verified`**(v5.1의 LOCKED/APPROVED/VERIFIED_REFERENCE에 해당 — `available`은 파일이 존재할 뿐 에셋 QA 잠금이 아니므로 final을 차단한다)이며 핀 버전이 현재와 같아야 한다. final 시각 산출물(type storyboard/storyboard_sheet)은 추가로 **활성 `look_asset_id`가 등록된 style_world_bible/look 에셋이고 verified이며 해당 board의 `required_asset_versions`에 현재 버전으로 핀**돼 있어야 한다. stale이거나 draft인 artifact는 final이 될 수 없고, final은 의존 체인 어디에도 누락/draft/stale/버전 드리프트를 가질 수 없다. 실행 시점 게이트는 `scripts/asset_gate.py`의 `check_asset_gate(project, artifact_ids=None)`다 — `artifact_ids`로 특정 final 범위만 검사할 수 있고, 반환 blocker 목록이 비어 있어야 최종 생성·납품으로 진행한다. `--profile preproduction|delivery`와 ready-package 검사는 전체 게이트를 적용한다.
- **PRELIMINARY는 언제나 허용된다** — DRAFT/planned/pending 에셋에 의존하는 보드·명세는 preliminary로 생성·검토할 수 있지만 연속성 잠금 산출물로 취급하지 않는다. 누락 에셋은 ASSET_PENDING으로 표시하고 스토리보드를 역설계 소스로 쓰지 않는다.
- `required_asset_versions`의 에셋 버전이 바뀌면 핀을 업데이트하지 않은 해당 artifact는 `update_project`가 자동으로 stale 처리한다. 무관한 다운스트림은 유지된다.

### 패널 자산 참조
- `storyboard.panels[]` 선택 필드 `asset_version_refs: {asset_id: version}`은 그 패널이 사용하는 잠긴 에셋의 정확한 버전을 기록한다(기계 판독 패널 매니페스트 계약). 보드가 `finality: final`이면 모든 패널에 필수이며, 그 샷의 `asset_ids` 전부와 활성 LOOK을 포함해야 한다. 보드가 stale면 패널 핀은 이전 버전을 보존할 수 있다.

### provenance·프롬프트 메타데이터
- asset_registry 선택 필드를 확장한다: `generation_mode`, `negative_prompt`, `model_version`, `seed`, `aspect_ratio`, `resolution`, `created`, `creator`, `license_status`, `checksum`. 기존 `provider`, `model`, `workflow`, `prompt`와 동일하게 **확인된 값만** 기록한다. 확인할 수 없는 값은 비워두지 말고 `UNKNOWN` 또는 `NOT_EXPOSED`로 적는다 — 없는 메타데이터를 발명하지 않는다.
- 프롬프트 원문 파일은 `prompts/`에 두고 asset의 `prompt` 필드나 별도 locator로 연결한다. 생성 이력 자체는 `.history` 감사에 기록한다(아래).

### 패키지 출력과 이력 저장
- 제작 프로젝트 루트는 실제 사용자의 `Documents/studio_production/<project>` 아래를 기본으로 한다. 저장 경로 초기화·중간/최종 결과 등록·중단/재개 기록은 [프리프로덕션 저장 계약](preproduction-review.md#1a-실제-파일-저장-초기화와-제작-이력)과 `recording-production-history`의 `production_history.py` CLI를 따른다. `.history/`는 시간순 감사 흔적이며 승인 원장이 아니고 project.json과 경쟁하지 않는다.
- `python scripts/package_production.py <project.json> --base-dir <root> --output <새-출력>`는 정본 원장에서 v5.1 패키지 트리(00_MANIFEST…10_DELIVERY)를 만든다. `--zip`은 `<root_name>.zip` 한 개를 만든다. 실제 available/verified 파일만 복사하고, `package_manifest.csv`·`asset_gate.csv`·`asset_registry.csv`·`asset_provenance.csv`·`scene/shot/storyboard_panel manifest`·`dependency_graph.csv`·`generation_status.csv`·`prompt_ledger.csv`·`qa_report.md`를 원장에서 **파생**해 쓴다 — 존재하는 데이터에만 해당 매니페스트를 만들고 빈 파일을 산출물로 위장하지 않는다. 기존 출력 경로는 덮어쓰지 않는다. `.history/`가 있으면 감사 사본으로 포함한다(`--no-history`로 제외 가능). `--require-final`은 에셋 게이트가 열려 있으면 패키징을 거부한다.
- 패키지는 이미지가 있다고 완료가 아니다: 게이트 blocker 0, 핵심 에셋 버전·provenance 추적 가능, 패널 매니페스트가 사용 에셋을 특정 버전으로 참조해야 완료다.

