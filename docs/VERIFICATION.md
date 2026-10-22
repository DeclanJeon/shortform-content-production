# 검증 범위

검증 결과는 프로젝트 구현과 별도 n8n/DB/runtime의 실제 실행을 구분해 기록합니다.

## 2026-10-21

- `scripts/check_public.py`: PASS; 154 source files checked, no reachable history blobs or
  credential values exposed. `installer.verify_source()` confirmed the source manifest.
- Root contracts: 41 tests passed, covering installation, activation, account boundaries,
  handoff eligibility/idempotency/readback, and the interactive setup wizard.
- Portable skill runtime and storyboard contracts: 14 tests passed in the Python 3.12 CI
  container, including reproducible bundling, per-stage skill routing, host dependency
  separation, creator-exclusivity failure on unknown source Sheet identity, and review-gate
  preservation.
- Full storyboard v3: the storyboard stage now loads the pinned `storyboarding-video` module
  and storyboard contract; the verified bundle includes the upstream validator, renderer, and
  splitter scripts with SHA-256 pins. The project ledger maps synopsis beats through scenes,
  shots, panels, speech, and audio-cue IDs.
- Isolated storyboard artifact test: synthetic PNG fixtures produced 18 canonical panels across
  three sheets, 18 clean extracted cuts, and one scene overview. Upstream project/storyboard and
  extraction validators passed. A deterministic reviewer fixture consumed the extracted panels
  without visible board labels, and the content-checked archive staging function accepted the
  ZIP containing the sheets, split manifest, cuts, overview, rejected/accepted image revisions,
  and both submitted prompt versions. The version-2 base-prompt restoration hash check passed.
  No image/TTS provider ran.
- Installer/Compose: generated a fresh `PREPARED` target with automation disabled; generated
  Compose configuration passed `docker compose config --quiet`. Services were not started.
- API image: Docker build passed. Importing the built image registered the creator handoff routes
  and no YouTube/manual-release route.
- Worker and coordinator images: both Docker builds passed. The isolated coordinator image
  imported the five pinned storyboard tool modules from the mounted skill bundle.
- Publisher boundary: the separate `youtube_publish_only` project exists. Its `build/js/lib.js`
  A–O queue header exactly matches the producer's `PUBLISH_HEADERS`,
  including `발행시각`; the publisher README's `발행일시` table typo was corrected. The producer
  doctor verifies its configured queue sheet's edit access and header, but does not read the
  separate publisher's `발행 설정` values; setup now documents how to align `큐시트ID` and `큐탭`.
- Live boundary audit: a read-only inventory found an existing production n8n publisher active.
  Its published graph includes gateway release dispatch, YouTube channel validation, a private upload, and a public
  transition/readback. This work did not deploy the standalone creator or publisher packages and
  made no live workflow, service, credential, queue, database, or YouTube account changes. The
  refreshed active-workflow inventory and sanitized graph comparison follow.
- Fresh production-boundary inventory (2026-10-21, read-only): a separate production Compose
  deployment and n8n instance were active. Five active creator workflow names overlap this source
  package. Sanitized graph comparison matched their
  active node names/types, connections and HTTP method/path signatures with the local builders.
  The standalone queue-handoff workflow was not found in that live instance; its existing YouTube
  publisher remains active. Full node parameters, credentials and actual Sheet targets were not
  read. No production Compose resources were changed, and this package was not installed or
  activated there.
- The standalone same-host creator gate treats a missing external source Sheet ID as a conflict
  rather than assuming a different writer. Its fixture regression passed; this change was not
  deployed to production.
- n8n E2E: n8n 2.43.5 imported all six creator workflows plus one isolated test copy. The test
  executed the handoff graph against a local mock Sheets/Gateway transport: one queue row was
  appended, read back, and acknowledged; the workflow remained inactive and made zero YouTube
  requests. No real Google write or YouTube upload occurred.
- Separate publisher: six installer tests and four Node logic/structure tests passed. Its
  rewritten local-only n8n E2E completed claim → mock download/upload → verified completion
  inside an internal Docker network: three sheet writes, one mock upload, zero real provider
  requests. The test harness contains no SSH/remote-production target.
- BasedPyright diagnostics were unavailable because `basedpyright-langserver` is not installed;
  the user chose to proceed with test-based verification instead of installing it.

Full local commissioning, Google OAuth scopes, Typecast billing, Codex Imagen execution, actual
voice/video human QA, and remote production cutover are not certified by these checks. The storyboard
fixture uses synthetic PNGs and deterministic AI-review responses; it does not prove a real
episode's creative quality or authorize paid media.

Packaged workflows remain inactive by default; Compose services were validated but not started.
The only remote access was the read-only inventory/export described above; no production service,
database, volume, queue, credential, or YouTube account was modified.
