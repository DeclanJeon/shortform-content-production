---
name: designing-character-personas
version: 1.18
description: Use to create fictional character personas with MBTI or Enneagram lenses, deepen an existing character's motives and decision rules, or translate a persona into observable acting, dialogue, and voice direction. Not for real-person typing, personality assessment, or media generation.
---

# Designing Character Personas

Design behavior-grounded fictional personas and — for speaking roles — a voice design separating fixed identity, speech style, and per-line performance. Output is a **text packet** only: no image, audio, audition, cloning, voice selection, or spend is produced or authorized. Default output language is Korean.

This skill is a specialist, not a second bible. In a full character pipeline, `designing-video-character-sheets` owns the canonical `character_sheet` SSOT and consumes this packet, never a competing record. Standalone calls return just the packet — no forced full 0–33 sheet and no new project/artifact IDs allocated; preserve supplied character IDs and existing source ID/version references.

## Inputs

Required: the request scope (standalone packet vs. delegated input to a sheet) and a minimal character brief. Everything else is used when supplied — supplied facts are never re-asked:

- existing `character_id`, role, synopsis/brief, and requested scope;
- approved character settings, sheets, behavior/dialogue evidence, input artifact versions;
- fictional-design delegation scope and whether the subject is a real person;
- requested typology mode, supplied types, traits to keep or exclude;
- medium, whether the role speaks, existing voice selections or audio;
- supplied voice conditions such as language/dialect — demographics or clinical facts are not required or solicited.

Label every value `supplied | observed | proposal | undecided`. `observed` records the source actually read, its version, and inspection scope. Fictional-design delegation permits labeled creative proposals; it never permits inventing real files, approvals, or assessment results.

## Typology mode

`typology_mode ∈ {none, mbti, enneagram, both}`. Precedence:

1. **Explicit** — a requested mode or supplied types win. `none` is a valid request: motive, behavior, expression, and voice design still happen.
2. **Default proposal** — within explicitly delegated design of a NEW fictional character, propose `both` as labeled proposals.
3. **No forced typing** — existing approved characters, real people, and undelegated subjects get no new type: mode `none`, with unsupplied type values `undecided`. If types are supplied, use only those lenses within the requested scope; never infer an additional type.

Supplied types are preserved verbatim and recorded as `supplied`; gaps between a supplied type and approved behavior are named, never silently resolved — approved behavior wins. Type is a questioning lens, not a destiny: never convert E to "loud/high voice," I to "low/unable," T to "emotionless," F to "illogical"; never map a type to face, build, pitch, or timbre; never ban type combinations or force distinct types across a cast. Same-type characters must differ in motive, values, relationships, and speech.

## Execution order

1. **Input lock** — separate supplied facts, delegation scope, existing IDs/versions, speaking vs. silent. Done when preserved vs. open choices are distinct.
2. **Behavioral core** — role, outer goal, protected value, core desire, avoided state, interpretive beliefs, conflict priorities, hard limits, surface-vs-intent gap, strength costs, relationship-dependent openness, contradictions with conditions. Done when every core trait has an action, a choice, and a cost.
   Source scope: the action/objective/obstacle craft lens is Hooks [23] in `references/sources.md` (`READ-PARTIAL`, lines 99–103); the cost/exception packet structure is authored design. A theatrical lens never authorizes new obstacles in a locked story or factual claims about real psychology.
3. **Typology lens** — read `references/mbti-lens.md` and/or `references/enneagram-lens.md` only for the active mode. Preserve supplied types; propose within delegated scope only. Each active type needs its own `design_rationale`, character-specific axis/motive interpretation, grounds, limits, and recorded exceptions; preserve the template's separate fields and do not imply `design_rationale` from grounds or interpretation. Done when every interpretation carries its own grounds and exception, and every active type has an explicit rationale.
4. **State & relation variants** — only the pressure/trust/relationship contrasts the story needs. Done when changed behavior and preserved values are both explained.
5. **Expression bridge** — read `references/expression-voice-bridge.md`. Map each core rule to observable acting, speech, and — for speaking roles — vocal direction. Done when every mapped cue is observable or audible.
6. **Voice design** — speaking roles only: fixed Voice Identity (register, timbre, resonance impression, articulation, sentence-end handling, baseline rhythm, auditory anchors, allowed variation), Speech Style, and per-line Performance cues, kept as separate layers. Done when identity and performance states don't conflict and voice procurement status is honest.
7. **Cast check & return** — compare actual overlap in goals, judgments, relationships, speech, and voice anchors; keep intended similarity. Check every delivered line against Voice Identity anchors, Speech Style and state rules: an exclusive marker cannot also appear in a supposedly excluded state. Distinguish changed intent (admitting a failure vs. controlling a report) from merely using a pronoun. A phrasing tendency is not an absolute grammar: label its scope and exceptions; if a pattern is mandatory, demonstrate it in every applicable sample instead of declaring an unsupported consistency check. Preserve approved source lines/rules and report their gaps; reconcile only conflicting proposals you authored.

## Voice boundaries

- A reusable `voice_identity_direction` is separate from each line's `performance_cue`. Every delivered line carries **exact text, intent, subtext, pressure, stressed words, pause, breath, and pace** — including compact output. Use a short row/record, not a loose emotion label. If no special stress/pause/breath change is intended, say natural/default/none explicitly; missing scene evidence stays `undecided`, never invented. Shared defaults may be defined once only if each affected line explicitly references them. Shorten prose, not required cue fields. Type labels and biography dumps never enter voice prompts.
- Real `voice_id`, type, model, settings, and approved samples live only in the actual voice-selection record. A `proposed`/`unresolved` design never becomes "selected" here; unsupported controls are returned as requirement gaps, not claimed as applied. A handoff referencing a stale SSOT version is refused for current execution with the old pin named.
- Silent roles mark all three `voice_identity`, `speech_style`, and **vocal** `performance_cues` slots `N/A` with the non-speaking reason; visual state/acting cues belong in `expression_bridge`, not the vocal slot. Never add a narrator. Language/dialect follows the supplied-only contract. Invented precise Hz/rate figures are forbidden; resonance language is an acting impression, not measurement.

## Return contract

Return the packet using `assets/persona-template.md` — slots: `provenance`, `persona_core`, `typology_lens`, `behavior_rules`, `expression_bridge`, `voice_identity`, `speech_style`, `performance_cues`, `validation`, `unresolved_requirements`. For delegated work, include the input artifact versions consumed, material assumptions, checks performed, and unresolved requirements for the next executor. For standalone calls, no IDs are allocated.

## Quality gate

Every requested persona returned with supplied/observed/proposal/undecided distinguished; core traits traceable to behavior, dialogue, choice, and cost; each contradiction conditioned, not random; no type→appearance/timbre mapping; voice layers separated and speaking-role-scoped; supplied voices, rights, and approvals preserved; no fabricated voices, samples, or assessment results. A bare adjective list is incomplete.

## References

Read on demand: `references/mbti-lens.md`, `references/enneagram-lens.md` (active modes only); `references/expression-voice-bridge.md` (any expression/voice handoff); `references/sources.md` (source scope, adoption claims, verification limits — distinguish theory from evidence).
