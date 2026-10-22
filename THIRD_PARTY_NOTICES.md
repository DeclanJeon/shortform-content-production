# Third-party components

This repository references external components; it does not transfer their ownership, accounts, credentials, or commercial entitlements.

- Pi (`@earendil-works/pi-coding-agent`): MIT; attach your own installation and authentication.
- Node.js: MIT and bundled third-party notices.
- Python: Python Software Foundation license and bundled notices.
- Docker CLI/Engine/Compose: respective Docker/Moby/Compose licenses and terms; administrator-level access is required.
- n8n: Sustainable Use License / applicable commercial terms. Do not assume that hosting or reselling this as a SaaS for others is permitted without reviewing n8n's terms.
- Pillow: its applicable HPND/MIT-CMU-style license notices.
- FFmpeg: LGPL/GPL and optional component obligations depend on the build; retain the distribution's notices if redistributing binary images.
- Noto CJK fonts: SIL Open Font License; DejaVu fonts have their own permissive notices.
- Google/YouTube, Typecast, LLM providers and image executors: account, API, content, privacy and billing terms apply separately.

Selected `contents-production-skills` 2.30 instructions/references/scripts are embedded under
`src/skills/contents-production-skills/` and hash-pinned in `BUNDLE_MANIFEST.json`. The upstream
public repository has no LICENSE/license metadata in the inspected checkout. This repository
does not grant an open-source license for those files; downstream reuse/redistribution rights are
unverified and must be confirmed with the rights holder.

The `ai-delegation-loop` module and Codex Imagen executor source are deliberately NOT bundled.
Supply those host dependencies only when authorized. No provider or font license is replaced by
the project's own source license.
