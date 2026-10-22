# Shortform Content Production Package

This source-only package installs an isolated content-production stack:
PostgreSQL, gateway, Typecast/render worker, n8n and an opt-in creative
coordinator. It does not contain a YouTube publisher or YouTube OAuth setup.

The coordinator reads the embedded, hash-pinned skill subset at `skills/`.
Pi runtime/auth, `ai-delegation-loop`, and the Codex Imagen executor are
explicit host dependencies configured separately. Installation never copies
credentials or activates paid generation.

The publisher handoff is a durable outbox event plus a readback-verified row in
the configured Google Sheet queue. The project writes only the initial
`대기` state; `youtube_publish_only` owns YouTube upload and all later queue
state.

See the repository-root README and `docs/` for setup, security, operations and
verification boundaries.
