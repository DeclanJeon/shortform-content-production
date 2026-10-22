# Video direction contract

Shared decision contract for the text-planning lane. `orchestrating-video-preproduction` applies it; the specialist skills follow its conventions when they run under that lane. It governs how creative choices are framed, recorded, and paused on — nothing else. `creative-production` owns project routing, shared state, and provider/spend approvals; this contract creates no project artifacts, calls no tools, and authorizes no spend.

## Interaction modes

- **Collaborative** — the default for multi-stage planning. Reuse answers already supplied, resolve consequential creative choices with the user, and pause at the checkpoints below. Asking for a video or a full package grants no approval for any proposed direction.
- **One pass** — only when the user explicitly delegates creative choices or asks to proceed without intermediate questions. Produce the requested work with labeled proposals and assumptions; ask only when constraints collide badly enough to block a valid result. This mode waives creative pauses, never the production/spend boundary.
- **Single artifact or locked brief** — when the direction already exists or does not matter for the artifact, deliver it without ceremony. Interrupt only for a gap that would change the artifact itself; a character sheet, synopsis, or locked storyboard never grows into a project-wide interview.

Use the host's available structured question or dialogue tool when one exists; otherwise present numbered choices in the user's language. Group related questions together, give each a small set of options with a short trade-off, and mark a recommended option where one is defensible — including an "unsure; recommend for me" choice when useful. Users may answer freely. Never re-ask supplied or approved values.

## Discovery: topic and synopsis

Discovery applies only to requested video topic/synopsis exploration. A supplied or approved topic skips topic discovery; a supplied or approved synopsis skips synopsis discovery. A concrete single synopsis, locked brief, article or other standalone artifact does not acquire an options workflow. An explicit options request keeps its requested count; five is only the unspecified discovery count.

- **Topic options.** Before ranking, apply **REQUIRED SUB-SKILL: `topic-demand-selection`** to candidates and the supplied audience/channel brief. Rank by the user's stated priority using comparable evidence; distinguish demand evidence from channel fit, purpose, and production feasibility. Every candidate carries fit, demand verdict/evidence with source and date (or `unknown`), source/fact status, distinction, and one feasible production constraint. Do not claim trend/popularity without observed evidence.
- **Synopsis options.** Only when a topic is settled, the scope includes synopsis exploration, and no synopsis exists yet: offer five genuinely different synopsis variants — different structure, angle, or promise, not five phrasings — and pause for selection. There is no automatic 5×5 batch: topic count never produces synopsis variants, and neither count authorizes any media count.
- **Overrides.** An explicit requested count ("세 가지 주제", "two concepts") replaces the five at that stage. Explicit delegation allows selection and continuation with labeled decisions rather than pauses; it does not force unsolicited option lists into a one-pass artifact request.
- **Evidence.** Trend or performance claims require a named source and date/capture context or are marked unknown. Channel history and audience metrics are never invented. Demand research and historical/source substantiation are separate gates; neither stands in for the other.
- **Inheritance.** Requirements approved from analyzed reference videos and an existing project style brief are inherited from the coordinator or request as-is — they constrain candidates rather than being rebuilt here. Supplied uploads — topic/reference links, synopses, character sheets, voices, prior boards — are reused with version, source and actually-inspected scope recorded; an unreadable supplied item is unverified and is never reinvented. An approved emotional journey in the spine is reused the same way: never re-asked, and never re-derived from a selected synopsis. A cleared direction checkpoint does not restart discovery; a selected synopsis also settles the premise/concept it contains. Reuse those choices instead of inserting a redundant concept interview.

## Recording choices

Every creative choice carries one status: **supplied** (the user stated it), **proposed** (the lane suggested it), **approved** (the user confirmed it), or **unresolved**. A recommendation is a proposal, not an approval. Record decisions once and reuse them; do not keep parallel copies that can drift. When an approved choice changes, show the downstream impact and reopen only the checkpoints it touches — an earlier creative approval never authorizes a changed scope or spend.

## The direction decision

Before drafting concepts, decide the direction on three independent axes:

- **Genre** — what kind of video this is. Genre does not determine tone.
- **Purpose** — what the audience should take away. Record one primary purpose and, if present, a secondary one; purpose is not the job of an individual beat.
- **Production method** — how it should look and be performed. Method is not a model, input type, or provider name; a hybrid production assigns each segment its own method while sharing one visual identity.

The axes are independent: a supplied value on one fixes nothing about the others, and the brief's own vocabulary takes precedence over any preset list. For an underspecified brief, offer two or three plausible direction combinations — each with a one-line trade-off — and ask which to develop, rather than committing silently. Missing evidence constrains what may be claimed, not which direction the user may choose.

## Shared brief

The lane keeps one canonical brief covering the direction axes, mode, audience, takeaway, format/runtime, message, fact status, constraints, anchors, and continuity decisions — the field list lives in `stage-execution.md` §The shared spine so there is exactly one source of truth. This contract adds the discipline around it: capture each field once, carry it through every handoff unchanged, mark material assumptions where they first matter, and never let a later stage quietly convert a proposal into an approved fact.

## Checkpoints

Apply only the checkpoints inside the request; each has a clear exit condition.

| Checkpoint | Present | Exit |
|---|---|---|
| Topic | Five fit-justified topic candidates (or the explicit count), each with fit, evidence status, distinction, and a production constraint | Selected topic; a supplied or approved topic skips this checkpoint |
| Emotion | Present only after the topic settles and the spine's Emotion is unresolved: a small set (max three) of emotional journeys appropriate to the brief, each as an ordered feeling sequence — mixed journeys (e.g. humor into warmth, tension into relief) are valid candidates, with humor/emotion/lesson distinguished from the final result feeling — plus a one-line trade-off; not a mood word list | Selected emotional journey; a supplied or approved Emotion skips this checkpoint, and an unresolved Emotion never blocks stages that do not read it |
| Direction | Known brief plus a small set of direction combinations, or the missing axis choices | User confirms a direction or explicitly delegates the choice |
| Concept | A small set of distinct concepts fitting the direction, each with its hook and trade-off | Selected concept; a supplied approved concept skips this checkpoint |
| Synopsis | Five genuinely different synopsis variants (or the explicit count), each with structure, angle, and promise | Selected synopsis; a supplied or approved synopsis skips this checkpoint |
| Length and structure | Full-production requests with runtime unresolved: runtime candidates up to one hour (30s/1m/2m/5m/10m/30m/1h or a stated length) plus the chapter/single-film/series decision; one-pass mode carries them as labeled proposals | Runtime and structure selected or delegated; narrow or single-artifact requests skip this checkpoint entirely |
| Plan review | The requested later artifacts — storyboard and fixed visual anchors where applicable — with unresolved references visible | User accepts or revises before downstream work |

Production and results gates belong to the downstream execution owner, not to this lane; a planning approval is never spend approval. Reuse existing approvals instead of stopping again at a cleared checkpoint. A partial request needs no approval to omit unrequested stages. An unanswered checkpoint ends with the choices themselves — never with downstream artifacts. An ordinary storyboard request — and always a production/detailed storyboard or full board sheet — is a complete production plan: the Plan review hands off to the required `video-production-assets/references/storyboard-contract.md`, where shot, camera/spatial, VFX, voice and audio owners integrate their per-cut specifications before the final board, not as a blanket pass after all boards are drawn. Only an explicit request for a panel exercise, rough narrative beats/thumbnails, or image prompts alone uses the narrow panel format.

## Handoff boundary

This lane ends at text artifacts and their review. The shot-level production specifications inside the storyboard contract — camera, spatial, VFX, speech and audio slots — are this lane's own text deliverable, planned not executed. Model-specific clip decomposition, generation prompts for a chosen model, execution, editing, and delivery are downstream work selected by `creative-production`; do not route or run them here, and do not expand delivery beyond the requested scope. When handing off, pass the direction decision, choice statuses, the canonical brief, and the continuity ledger — the receiving stage inherits them as-is rather than re-interviewing.
