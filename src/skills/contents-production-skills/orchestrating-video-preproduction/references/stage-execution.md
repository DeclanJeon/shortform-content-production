# Lane stage execution: shared spine, stage craft, package assembly

Read this guide once when `orchestrating-video-preproduction` runs a multi-stage or package request. It holds the canonical spine fields, per-stage craft requirements, integrity standards, package assembly, and the final consistency pass. Mode selection, discovery, and checkpoint mechanics live in `video-direction.md`; scope, intake, the dependency-gap packet, and the return envelope live in `SKILL.md`. A single-artifact request opens neither this file nor `video-direction.md` — it runs only the matching specialist.

## Lane posture

The default posture is collaborative: settle topic, direction, and concept before drafting later artifacts, and let the user review the storyboard and visual anchors before anything downstream. Only an explicitly delegated one-pass request proceeds with labeled proposals instead of pauses. When running under `creative-production`, these checkpoints feed the coordinator's shared approval flow rather than forming a second ledger.

For requested topic or synopsis exploration, apply [Discovery](video-direction.md#discovery-topic-and-synopsis), its scoped counts, supplied-input skips and selection checkpoints, and the canonical [topic-selection policy](../../topic-demand-selection/SKILL.md#decision-procedure): honor the user's explicit priority; otherwise use comparable demand as the primary ranking criterion, with fit/purpose/feasibility as constraints or close-choice tie-breakers. Assess factual eligibility independently; neither demand nor fit establishes truth. Concrete single-artifact requests retain their narrow scope; option counts never authorize media outputs.

A checkpoint without an answer ends with the choices on the table — never with unapproved downstream artifacts. Supplied choices and existing approvals are always reused. Each specialist owns its artifact; a specialist's standalone question rules cannot override an active lane checkpoint. Regardless of mode, the lane remains text-only: no folders, no generation, no paid calls, no asset claims.

## The shared spine

One canonical record, captured once, reused verbatim by every stage:

| Field | Contents |
|---|---|
| Choices | Interaction mode; each decision's supplied/proposed/approved/unresolved status — topic, direction, concept, synopsis included; the active checkpoint |
| Direction | Genre, purpose, production method as three independent decisions |
| Mode | Narrative, data/educational, personal/documentary, brand/business, abstract/mood, explanatory metaphor, or a justified combination |
| Goal | Primary purpose, any secondary purpose, the one audience takeaway/promise/question — or an explicit "unresolved" |
| Audience | Only what was stated; no invented demographics |
| Tone | Requested register plus explicit exclusions, tracked apart from genre and method |
| Emotion | The target feeling the audience should leave with — an ordered journey that may blend feelings (humor into warmth, tension into relief) plus a sensory/visual anchor for the central feeling — or an explicit "unresolved". Tracked apart from Tone: Tone is the register the video speaks in, Emotion is the state it moves the audience to. |
| Format | Supplied platform, aspect ratio, runtime only; unspecified stays unspecified. For full productions with runtime unresolved, the Length-and-structure checkpoint picks the runtime (30s/1m/2m/5m/10m/30m/up to 1h or a stated length) and the chapter/single-film/series decision; narrow requests never acquire these forced choices |
| Message and facts | One governing message; every claim tagged as supplied/verified, derived arithmetic, interpretation, proposal, unverified, or unknown |
| Constraints | Dialogue, CTA, ending, and content limits; delivery scope; the no-generation boundary |
| Characters | Supplied identities, roles, goals, relationships, approved visual invariants; unknowns left open |
| World | Supplied setting, motifs, art direction, labeled staging proposals |
| Continuity | Prop identity, color, condition, owner and hand, spatial direction, reveal order, intentional changes |

Mark a material assumption at the first artifact it affects. Nothing in the spine may drift — genre, purpose, method, names, audience, cause, runtime, role, tone, prop details, ending. A later stage may attach a clearly labeled visual proposal, but a proposal never becomes confirmed story fact, and a changed approval reopens only the checkpoints it touches.

## Running the stages

**Concept and synopsis** (`developing-video-synopses`): establish mode, audience, purpose, and a structure suited to them. When the spine's Emotion is still unresolved, settle it before the synopsis locks — offer a small set of candidate emotional journeys at one checkpoint instead of letting the synopsis silently pick a feeling — and apply [`../../video-production-assets/references/16-concept-emotion-retention.md`](../../video-production-assets/references/16-concept-emotion-retention.md) §2 only as an optional deepening when the brief asks for emotional or retention design; it never becomes a mandatory stage. A supplied confirmed synopsis is preserved, never rewritten for convenience. Factual and brand work keeps source facts, arithmetic, interpretation, proposed action, and unknowns in separate buckets. The program or brand is not automatically the hero — put the audience at center when the brief calls for it. Unsupported effects, capabilities, causes, and CTAs stay out.

**Character sheets** (`designing-video-character-sheets`): for each relevant character, preserve the supplied synopsis/role/relationships and existing approved identity. Read `designing-character-personas` for the integrated motives, conditional decision/behavior rules, expression bridge and speaking-role Voice Identity/Speech Style/Performance design. Follow its typology mode/provenance rules: new delegated fictional design can propose lenses, but supplied settings, explicit `none`, existing approved identities and real-person evidence win. Explicitly delegated fictional design may choose missing age, gender, proportions, motives and causal backstory as labeled proposals; real-person facts and undelegated consequential choices remain unknown. Full SSOT assignments use the character master reference and 0–33 format, filmable persona, immutable/stable/variable visual and voice anchors, identity prefix and Sheet A–D specifications. This lane returns text, not an actual voice or audition; the coordinator then assigns the actual per-character Identity Sheet A in an image-backed package before panel generation. Customer/service roles require brief support; no invented product capability or unsolicited mascot. Character-free modes skip this stage.

**Storyboard** (`storyboarding-video`): once the spine or supplied synopsis is stable. An ordinary storyboard in this lane is a complete production plan under the required [`../../video-production-assets/references/storyboard-contract.md`](../../video-production-assets/references/storyboard-contract.md): the lane supplies the stable synopsis beat IDs and anchors, and the shot/camera/spatial/VFX/voice/audio specifications are integrated before the final board rather than after all panels are drawn. Missing specialist inputs follow the dependency-gap rule in `SKILL.md` — they return to `creative-production` as one indexed requirement packet, never silently shrink the board to rough panels, and never make the lane pick owners. Only an explicit request for a panel exercise, rough narrative beats/thumbnails, or image prompts alone uses the narrow format; those panels still hold one readable beat each — a visible action or information change — with reveal order and continuity anchors, and they are not shot timings, camera moves, clip splits, or model-specific prompts. A long-form or series request decomposes whole chapter/episode → scene → beat/shot so full coverage is preserved instead of treating a feature as one short block.

## Integrity standards

- **Factual and documentary:** only supplied or verified claims; derived arithmetic labeled as such; a count change never becomes causation, persistence, a future result, or a program effect; separate periods never sum into "unique participants." Slogans and open endings can imply persistence too — promise nothing that was not supplied.
- **Prompts cannot launder evidence:** if the text calls something unknown, its image prompt must not depict it. Numbers-only briefs get neutral graphics, not invented documentary scenes, people, equipment, facilities, or records.
- **Brand and business:** capabilities only with brief or evidence support; a next step only when requested or warranted; never fabricated contact details, urgency, social proof, guarantees, or outcomes.
- **Personal and documentary:** event truth and perspective preserved; no invented biography, no inferred diagnosis.
- **Fiction:** connective invention is allowed to stage the premise; optional designs stay labeled proposals; fixed endings, dialogue bans, and tone constraints hold.
- Craft techniques apply only within the mode and scope they serve; no craft framework is evidence about a real person, service, statistic, or event.

## Assembling the package

Once the applicable choices are approved — or immediately in one-pass mode — a full package returns:

1. **공유 스토리 스파인** — the canonical fields, including direction and choice status, with unspecified values shown as open.
2. **콘셉트·시놉시스** — brief, logline or concept, synopsis, ordered beats.
3. **캐릭터 시트** — one per relevant character: integrated persona/rules and expression bridge, speaking-role Voice Identity/Speech Style/Performance (silent roles N/A), full SSOT where assigned, continuity locks/variants, identity prefix and reference-sheet prompt specifications. Derived voice profiles pin the exact character SSOT version; text voice design is not chosen or auditioned audio. In an image-backed package the coordinator must turn Sheet A into one actual identity image per character and inspect it; the text lane cannot mark that image complete.
4. **비주얼 바이블** — the consolidated character, product, location, palette, lighting, wardrobe, material, and continuity anchors, with proposals and missing references marked.
5. **스토리보드** — the requested board: for an ordinary storyboard the complete production-plan sections with every beat→scene→shot→panel mapping and technical slots or indexed requirement gaps; for an explicitly narrow request, the one-readable-beat panels with per-panel image prompts and an optional unified sheet prompt matching the exact panel count.
6. **일관성·미정 사항** — the consistency check, consequential unresolved choices, review status, and any requested handoff.

Sections the user excluded or never asked for do not appear. Prompts stay in Korean unless another language was requested. No generation status, no rendered preview, no fabricated asset reference.
For a full image-backed parent request, this return is only the textual stage packet. The coordinator continues under the canonical full-package contract: register synopsis/character SSOT versions, produce and inspect identity references, integrate the technical board, render the actual storyboard sheet set (v5.1: at most 8 panels per sheet; boards beyond that paginate into an ordered `<stem>_sNN.png` set whose union covers every canonical panel in story order), then extract real clean panel crops and per-scene overview images from the produced sheets via `split_storyboard.py --sheet`, and run strict preproduction and visual checks. A text package is not the completed image package.

## Final consistency pass

Before returning, test every stage against the spine:

- Direction, mode, purpose, audience, and takeaway agree; nothing fictional poses as fact and vice versa.
- Runtime, dialogue, CTA, tone, ending, and counts match across outputs.
- Character identity, role, goal, agency, traits, and relationships are identical in synopsis, sheet, and panels.
- Location, style, color, material, condition, possession, screen direction, and reveal order hold; every change happens inside a visible beat.
- Each claim traces to supplied or verified material or wears its label — arithmetic, interpretation, proposal, unknown. No unsupported aggregates, future-continuity slogans, or predictions; prompts carry no smuggled evidence.
- Scope matches the request exactly. A production board's shot-level technical specifications are integrated through the complete contract before final review — with any missing specialist inputs carried as the indexed requirement packet — while model-specific clip decomposition and execution remain downstream. Narrow narrative-panel requests acquire no unrequested shotlist or media.

When two explicit brief values collide, prefer the one tied most directly to the user's stated constraint and disclose the conflict rather than silently reconciling it. If the collision changes the premise and no conservative resolution exists, ask one concise question and complete every unblocked part.
