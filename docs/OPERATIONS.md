# 운영 경계

## 생산 주기

제작 정책은 120분마다 최대1편, 서울 날짜 기준 하루 최대12편입니다. 5분 intake poll은
제작 주기가 아닙니다. 실패·권리 미확인·QA HOLD·제공자 결과 미확정은 목표 편수를 맞추려고
통과시키지 않습니다.

## Handoff

`SFAutomaticPublishQueueHandoff`는 Drive 보관 완료, production authorization, rights evidence,
8개 QA PASS를 다시 확인한 뒤 안정된 `발행ID`로 queue row를 기록합니다. 같은 ID가 이미 있으면
metadata를 비교하고 정확히 일치할 때만 재사용합니다. 중복/충돌/비정상 header는 자동 덮어쓰기
대신 운영자 확인 상태로 멈춥니다.

제작 프로젝트가 쓰는 열은 `발행ID`부터 `공개설정`까지와 최초 `상태=대기`입니다. 발행 결과
열과 이후 상태 변경은 `youtube_publish_only`가 소유합니다. 발행 프로젝트가 별도 Google
credential을 쓰면 같은 Drive 파일에 접근할 수 있는 본인 계정인지 확인해야 합니다.

## 단일 제작 writer 전환

2026-10-21 읽기 전용 production audit에서 기존 producer/coordinator와 YouTube publisher
경로가 활성 상태였고, 일부 workflow 이름과 sanitized graph signatures가 이 source package와
겹쳤습니다. Live Sheet targets와 credentials는 확인하지 않았습니다. standalone doctor는
같은 호스트의 다른 active creator container에 source ID가 없거나 설정값과 같으면 활성화를
차단하고, 알려진 다른 source만 허용합니다. 이 fail-closed gate는 소스 및 격리 회귀
테스트에만 반영됐으며 production에 배포하지 않았습니다. 새로운 프로젝트를 활성화하기 전에
운영자가 실제 Sheet owner와 진행 중인 paid 작업/claim/outbox를 확인해야 합니다. 이 패키지는
기존 production system을 자동으로 끄거나 DB를 가져오지 않습니다.

YouTube 업로드는 이전 운영 경로와 새 `youtube_publish_only`를 동시에 활성화할 경우 중복될 수
있습니다. 새 creator는 업로드하지 않지만, 실제 publisher 전환 시에는 기존 SF09 운영 경로와
새 Sheet 큐의 단일 writer/consumer를 운영자가 명시적으로 결정해야 합니다.

## 백업과 pause

기존 설치기 pause/backup/restore를 사용합니다. handoff가 `IN_PROGRESS`이면 backup/재구성은
대기합니다. DB, n8n SQLite/WAL, artifact, private auth는 서로 다른 상태이므로 임의로
볼륨이나 claim을 지우지 않습니다. 복원된 인스턴스는 automation OFF 상태에서 doctor를 다시
통과해야 합니다.
