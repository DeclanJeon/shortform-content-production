# 보안과 권한

- 프로젝트는 별도 DB, artifacts, n8n data와 Compose network를 사용하지만 host Docker 권한은
  관리 권한에 가깝습니다. coordinator Docker socket을 host sandbox로 간주하지 마세요.
- `.env`, Typecast key, Pi auth, Google OAuth token은 프로젝트 소스/설정 JSON에 넣지 않습니다.
- n8n에는 Google Drive/Sheets OAuth만 연결합니다. 이 프로젝트는 YouTube OAuth credential,
  YouTube API publisher workflow, publisher adapter를 포함하지 않습니다.
- Google Sheet와 비공개 Drive 폴더는 본인 계정으로 연결합니다. Creator와 publisher가 다른
  OAuth principal이면 파일 권한을 확인하고, 공개 링크 공유로 우회하지 않습니다.
- 설치는 자동화 OFF이며 유료 이미지/TTS 또는 YouTube upload를 실행하지 않습니다.
- `src/skills`에는 사용자 제공 `contents-production-skills` 2.30의 승인된 subset만
  source commit/file hash와 함께 포함합니다. source books, media, QA-run data와 optional
  Higgsfield/platform skills는 넣지 않습니다. `ai-delegation-loop`와 Codex Imagen 실행기는
  검증된 별도 host dependency로 남습니다.
- skill instructions와 AI review는 실제 권리 증빙, 사용자 승인, 인간 청취/시청을 대신하지
  않습니다. rights/QA/archive gates가 모두 통과해야 queue handoff가 허용됩니다.
