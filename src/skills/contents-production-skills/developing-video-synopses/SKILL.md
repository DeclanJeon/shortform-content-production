---
name: developing-video-synopses
version: 2.22
description: Use to develop a video concept, logline, synopsis, plot outline, or story beats (콘셉트, 시놉시스, 줄거리, 비트) before production.
---

# Developing Video Synopses

This skill turns a premise or brief into narrative planning artifacts — concept, logline, synopsis, beat map. Show the artifact itself in Korean by default; a report that one was written is not the deliverable.

Narrative planning is where this skill ends. Character sheets and storyboard text belong to their respective owners; rendered panels and images require a separate authorized media stage. This skill never generates them. A frozen read-only/no-generation scope remains binding even when a later clause asks for completed panels: return the requested narrative text and name the conflicting media request, without tools, writes, or execution. This boundary applies standalone, without loading a coordinator.

## Inputs

Read the request as a brief and extract what is already known — never ask the user to repeat supplied information:

- the purpose, intended audience, and any single takeaway, question, or intended change;
- the video's mode — narrative/fiction, data/educational, personal/documentary, brand/business, abstract/mood, explanatory metaphor;
- genre and production method when supplied or selected, tracked separately from tone, narrative mode, and model/input type;
- format, platform, runtime, tone, constraints, and the exact deliverable requested;
- for factual briefs, the supplied claims, their sources, and explicit unknowns.

Ask one concise question only when a missing answer would change the mode or central premise; otherwise proceed, label material assumptions, and leave optional details open. When coordinated, return candidate concepts for the coordinator's concept checkpoint instead of expanding an unselected option. An unspecified runtime stays unspecified — "short" is not a duration — and no deadline, platform, demographic, or evidence source is manufactured.

## Invocation

Under a multi-stage coordinator or `orchestrating-video-preproduction`, inherit its direction, interaction mode, and choice statuses and honor its active checkpoint — a synopsis request is not authorization to complete later stages, and delegated work never calls back to `creative-production` or opens another interview. Stage packet intake and worker return follow the canonical [worker handoff and single writer](../video-production-assets/references/contract.md#worker-handoff-and-single-writer): reuse the packet's assigned IDs and input artifact IDs/versions as-is, and never invent or renumber existing project identity. Standalone calls consult `creative-production` once to confirm scope and route, then produce only the requested artifact. A supplied synopsis, uploaded draft or reference source is reused as-is with its version, source and actually-inspected scope recorded; an unreadable supplied item is unverified, never silently reinvented.

Running inside the shared lane, `../orchestrating-video-preproduction/references/video-direction.md` supplies the direction-axis and checkpoint conventions — following them neither widens a standalone request nor overrides explicit no-CTA, factual, or ending constraints. Routing and approvals stay with `creative-production`.

## Acceptance

Done means the actual artifact is present — not a completion report — the beat map passed the craft guide's QA and recheck, supplied and locked material is preserved verbatim, every claim carries its fact status, and no unrequested stage, media, or execution is implied. Repairs that would change the story are proposed, never applied silently.

## Detailed guide

Before drafting, read [`references/synopsis-craft.md`](references/synopsis-craft.md) — the mode-matched structures, the artifact field list, the beat-map QA and repair pass, accuracy discipline, and the self-check. It is the one detailed guide for this stage; load it once and follow it. A request scoped to a logline, premise, or beat outline applies only the relevant part and returns exactly that — never inflate a small request into a production packet.

For an explicit whole-film/short lifecycle request, use the film lifecycle reference for cross-stage placement and handoff only. This skill still returns exactly the assigned narrative artifact; a logline, synopsis or beat-only request does not expand into the film package.

## Return and handoff

Delegated returns follow the canonical [worker handoff and single writer](../video-production-assets/references/contract.md#worker-handoff-and-single-writer): report the assigned synopsis artifact ID and output version, the dependency/dependency_versions proposal naming each input artifact version used, material assumptions, the checks actually performed, and unresolved inputs — the coordinator remains sole `project.json` writer. Standalone text allocates no project or artifact IDs; the beat IDs inside the artifact are content IDs the next stage inherits, not allocated artifact identity.

The next stage inherits the spine unchanged: direction decisions, mode, audience, the one promise or question, tone, runtime/format if known, fixed facts with their status, constraints, world and character anchors, and inherited interaction mode and approvals. `designing-video-character-sheets` takes characters; `storyboarding-video` takes panels; anything beyond text planning returns to `creative-production` or `orchestrating-video-preproduction` — this skill never routes execution. Creative approval is not permission to generate paid assets.
