# Current-stage routing

Select the narrowest requested output. The coordinator reads this map once; workers consume their assigned packet and never reroute. No automatically running agent engine is supplied.

## Core lanes

| Current request | Owner / required read | Stop boundary |
|---|---|---|
| Article/blog/social text | Coordinator and actual supplied evidence | Requested text; no implied CTA, video, saving or publication |
| Korean marketplace product-detail page, copy, or image package | `product-detail-page-builder` under coordinator scope | Requires platform, product name/category, and real product photo; fact-linked copy/modules only; seller-only export rules remain explicit; no storefront UI, upload, or publishing |
| SNS/social-media public video-post search or reference discovery | `searching-social-media` | Bounded Flucto candidate retrieval; per-source failures/metrics/unknowns retained; no automatic download, transcript, body/video analysis, topic choice or publication. Demand-based topic selection still requires `topic-demand-selection`. |
| One synopsis/concept/beat map | `developing-video-synopses` and its stage guide | Stable beats/source locators; no automatic character/board stage |
| One character/voice-profile sheet | `designing-video-character-sheets` and its stage guide; REQUIRED `designing-character-personas` for persona/voice design | One integrated character SSOT, preserved approved identity/voice, voice N/A for silent roles; no training/rendering |
| One persona, fictional typology design, or persona-to-acting/voice direction | `designing-character-personas` | Text persona packet only; no forced full sheet, real-person typing, new SSOT database or actual audio |
| Explicit multi-artifact text video planning | `orchestrating-video-preproduction`; only requested downstream workers | One inherited spine; missing owner inputs return indexed requirements to parent |
| Ordinary or complete production storyboard | `storyboarding-video` + canonical [storyboard contract](../../video-production-assets/references/storyboard-contract.md) | All story coverage and applicable technical slots integrated; text-only keeps actual media unverified |
| Explicit rough-panel/prompt-only exercise | `storyboarding-video` narrow guide only | Requested narrative panels; no full technical package |
| Still-image video/edit with narration or sound | `storytelling-with-still-images` | Stills remain the only picture sources; no video-generation model for assembly |
| One brief/script/shot/light/edit/claim/budget/QA/delivery asset | `video-production-assets`, one requested module/reference/template | Requested asset, not a mandatory full packet |
| Explicit film/short/AI-movie full lifecycle or current-stage handoff request | `video-production-assets` [film production lifecycle](../../video-production-assets/references/28-film-production-lifecycle.md); only the current stage's owner module per request | Optional six-phase route; single synopsis/script/shot/delivery and non-film requests keep the narrow module routing above; no parallel schema or execution approval |
| Factual/educational plan | Relevant text owner and only applicable evidence module | No invented characters/causes/testimony/commercial goal |
| Reference camera movement / walking analysis | `camera-motion-reference-analysis`; current source interval only | Observed vs inferred motion; no metric solve or media generation inferred |
| Narrative camera planning: POV, shot size, composition, motivated pan/push/static choice in text or ordinary 2D storyboards | `storyboarding-video` / `video-production-assets` `06-shots` | Narrative camera fields and moves alone do not invoke Blender; simple 2D/flat graphics remain non-3D |
| Camera timing, lens/focus or subject-relative motion design | `camera-spatial-design` timing/lens guide; apply the Blender decision gate below | Preserve camera schema, action/reveal timing and actual rig limits; numeric output alone is not 3D verification |
| Camera-plan to AI-video control/prompt | `ai-camera-control` | Model-neutral or live verified inputs; exact path control never inferred from text |
| Actual camera movement / lens / reveal QA | `video-production-assets` reference27 | Playback and timed evidence, minimum fix; no automatic paid retries |
| Numeric camera design without a geometry-verification deliverable | `camera-spatial-design` | Proposed lens, coordinates or path may remain design-only; locked camera reused; numeric/schema analysis is preflight, unrendered geometry stays UNVERIFIED |
| Scene-specific 3D spatial verification/previs, including explicit Blender/previs requests | `camera-spatial-design` owns the spec; REQUIRED `blender-previsualization` implements and visually inspects it with real Blender | Requested .blend, frames, analysis, inspection and manifest tied to IDs/versions; unavailable Blender blocks only dependent 3D completion, not independent text work; no alternate renderer or FINAL claim |
| Video prompt/decomposition/model route/critique/pipeline recipe | [video-generation-planning.md](video-generation-planning.md); dialect JSON only for the selected dialect | Text planning/critique, not spend/submission permission |
| Explicit image-backed full preproduction package | `creative-production` is sole coordinator; use `orchestrating-video-preproduction` only for its assigned text outputs, then resume coordinator-owned media/output workflow under [preproduction-review](../../video-production-assets/references/preproduction-review.md), [storyboard contract](../../video-production-assets/references/storyboard-contract.md), and [full-package contract](../../video-production-assets/references/contract.md#전체-프리프로덕션-패키지) | Content/causality/coverage/continuity/timing correction before images; Codex Imagen still masters/panels; v5.1 FINAL gate; actual ordered sheets (≤8 panels), all scene/panel extractions and visual QA; external project root + single canonical project.json/.history writer; review readiness is not user acceptance or video execution approval |
| Reference selection/analysis or package audit | Production-assets reference25 and matching template | Evidence-scoped analysis; package edits only if separately requested |
| Actual content-file production, interrupted project or saved-output audit | `recording-production-history`, controlled by the coordinator | Documents/studio_production project storage and chronological `.history`; no rival approval ledger, duplicate generation or assumed completion |

Open only the selected sibling SKILL and its needed guide. Missing core files mean broken installation. Only an explicit matching provider/platform/deep-craft request reads [conditional lanes](production-routing-optional.md); migration maps in integrations are not startup context.

## Blender decision gate

**Blender trigger:** explicit Blender/previs request, or the requested result depends on validating an actual scene-specific 3D arrangement (camera path, geometry, blocking, occlusion, clearance, or contact), or a 3D-verified result is part of the requested deliverable. A camera field, camera move, storyboard, or scene ID alone is not a Blender trigger.

“Scene-specific 3D camera/spatial design” means actual scene-specific 3D geometry/placement/feasibility design under this trigger, not narrative camera descriptions or numeric proposals alone. This is the canonical routing table; specialists use this gate without creating another route table.

Text-only/read-only work may plan later previs but must not execute Blender; mark unperformed checks PLANNED/UNVERIFIED or BLOCKED. If required Blender is absent, report the exact missing runtime/evidence, continue independent work, and mark only dependent spatial deliverables BLOCKED/UNVERIFIED; no alternate-renderer substitution or FINAL claim. Numeric/schema checks and smoke runs do not prove actual 3D scene feasibility or full requested visual inspection. Smoke is pipeline preflight only, not full-scene/all-requested-frame/contact acceptance. Completed previs records actual Blender version/mode, preserves camera spec/version and shot/scene/panel IDs, and requires reopening requested .blend/image outputs and inspecting relevant frames. Bounding-box, center-point and ray-cast proxies do not prove articulated visibility, contact, performance, aesthetics or final generated-video quality; unsupported claims stay UNVERIFIED.

## Selected runtime gate

Apply this gate only before an actual requested runtime/provider operation, not text planning:

1. Verify the selected runtime/auth, actual live schema/capabilities, destination and rights/consent. Report missing prerequisites; installation is separate explicit scope, never automatic.
2. Apply [production execution](../../video-production-assets/references/24-production-execution.md): `codex-imagen` is the default still-image executor; no Higgsfield image submission/edit fallback. For Codex Imagen stills, do not check price, usage, quota, or block on unknown cost; verify only runtime/auth, supported inputs/outputs, destination, and rights.
3. Bind image calls to current input versions, exact requested outputs/settings, and practical retry/concurrency/time/storage limits. No Codex Imagen quote, free-allowance lookup, or cost cap is required. Keep separate video-generation and other-provider approval gates.
4. Preproduction acceptance, preview permission, final production and publication are distinct. No extra retries/batches/covers/training/analysis or live deploy inferred. Independent work may run concurrently; prior-frame dependencies and uncertain async jobs may not.
5. Preserve secrets/source files, inspect actual outputs, register only observed results. A private backend enhancer cannot be replaced by generic generation and claimed equivalent.

## State and handoff

For an existing project, [common handoff/single writer](../../video-production-assets/references/contract.md#worker-handoff-and-single-writer) owns ID/version lookup, worker return and authorized persistence. Domain bibles/provider adapters are linked artifacts. Text-only/no-save proposals do not write a ledger; a standalone artifact does not create one.

Actual saved content work uses `recording-production-history` before writes and after every observed output. Topic-to-video/image-backed work also reads preproduction-review. Use the actual user's Documents/studio_production project root by default; preserve an explicit active project destination. Report absolute saved paths and history paths. Tools and interview frameworks are checked only when selected, never universal prerequisites.
