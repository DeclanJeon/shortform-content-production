# 아키텍처와 책임 경계

```text
Sheets(source) → intake/coordinator → Postgres/gateway
               → image/TTS/render worker → verified private Drive archive
               → durable publish_handoff outbox
               → Sheets(publish queue, state=대기)
               → [별도 youtube_publish_only project]
```

이 패키지 소유 서비스는 dedicated n8n, PostgreSQL, gateway, worker, creative coordinator,
private artifacts volume입니다. 새 stack은 기존 n8n, gateway, database, worker 또는 Docker
network를 공유하지 않습니다. Provider credentials/runtime는 사용자 host가 제공합니다.

Creator의 6개 workflow는 intake, private video archive, project archive, policy approval,
production ledger projection, publisher queue handoff입니다. 모두 installer에서 inactive로
import됩니다. YouTube credential, upload workflow/adapter, publish state machine은 포함하지
않습니다.

Queue handoff는 source archive의 stable render lineage로 `publishID=sp-<sha256-prefix>`를
만들고, `outbox.dedup_key`로 durable claim합니다. n8n은 exact queue header, ID uniqueness,
row metadata를 확인한 다음만 append하고 readback 결과를 ack합니다. Append 응답이 유실되면
retry는 동일 publish ID를 찾아 검증하며 새 row를 바로 만들지 않습니다.

Google Sheet는 기존 publisher의 15-column contract입니다. Producer writes A–G plus initial
H=`대기`; publisher exclusively owns H transitions after that and I–O.
