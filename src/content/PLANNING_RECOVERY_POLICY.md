# 기획 자동 복구 정책 — planning-recovery-v2

## 정본과 조립
- 확정된 후보와 source/skill hash를 하나의 canonical_context로 전달한다. 작성·검수·수정·최종 독립 판정 모두 같은 packet을 사용한다.
- source 문서의 과거 후보 선택은 참고 자료다. 신뢰된 선정 정책의 locked_pitch_id가 있다면 다른 후보로 전환하지 않는다.
- 모델은 비트별 패널 내용을 작성한다. 프로그램은 고정된 여섯 비트 순서대로 기능(hook/setup/development/turn/payoff/closing), shot/segment ID와 참조를 생성한다. 모델의 비트 기능 표기 실수 때문에 내용을 정렬하거나 교체하지 않으며, 실제 기능·인과의 적절성은 별도 의미 검수로 판단한다. 기존 장면을 멋대로 정렬하거나 문장을 고쳐 통과시키지 않는다.
- 기존 실패 기록/원본 source/실제 모델 응답은 덮어쓰지 않는다.

## 제한된 복구
1. 조립 가능한 초안의 enum/분량 등 구조 오류는 정확한 leaf 위치·현재 길이·허용값을 제공하여 동일한 2회 국소 수정 한도 안에서 고친다. 구조를 통과한 경우에만 별도 모델 의미 검수를 실행한다. 불완전한 비트/패널 묶음은 임의로 창작해 채우지 않는다.
2. 반려 사항은 exact plan SHA에 묶인 JSON-pointer leaf patch만 허용. 최대 12개 수정, 후보당 최대 2회. 배열 전체 교체·ID/순서·안전 플래그 변경 금지.
3. 구조·의미 검수를 다시 수행한다. 계속 반려되면 별도 실행의 최종 독립 판정을 1회 수행한다. 이전 반려를 PASS로 바꾸지 않는다.
4. 여전히 반려되면 같은 원본/같은 제작 슬롯 안의 다음 적격 후보로 이동한다. 최대 2후보, 전체 모델 request provenance 최대 15개. 초기 후보 비교 1회와 후보당 최대 7개 요청을 포함한다.
5. 전부 소진하면 해당 건만 planning_candidates_exhausted로 격리한다. 서비스는 다른 eligible 작업을 계속 소비한다. 통과할 때까지 무한 재시도하지 않는다.

## 결과 미확정·권한 보호
- timeout/결과 불명/auth/모델 실행 오류에서는 다른 후보를 무작정 호출하지 않는다. 성공 checkpoint만 재사용한다.
- 기획 복구는 이미지 구매·TTS·렌더·YouTube 업로드를 호출하지 않는다. 기존 이미지 자산별 2회 한도, daily media admission, 권리·승인·보관·공개 readback gate는 유지한다.
- legacy creative_plan_review_failed만 1회 정책 업그레이드 재claim 가능. 새 fenced claim을 사용하고 source/skill이 같아야 하며 plan version/제작 child/이미지 intent/job/release가 있으면 재claim하지 않는다.
- legacy 캐시는 source/skill/이전 claim/원본 UTF-8 byte SHA에 묶는다. CRLF를 정규화해서 hash를 비교하지 않는다.
- 검수의 문자열 issue 또는 {id,location,issue,fix} 또는 ID 없는 {location,issue,fix} 형식을 명시적으로 파싱한다. 구조화된 REVISE를 형식 차이 때문에 잃거나 PASS로 승격하지 않는다.


## 배포 기본값
하루12편·최소120분 간격이며 모든 자동화는 설치 시 OFF다. 실제 신규 계정의 권한/공급자/활성화 검증은 별도다.
