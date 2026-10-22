---
name: topic-demand-selection
version: 1.18
description: Use whenever choosing, ranking, recommending, or validating topics for audience-facing content, especially when popularity, search demand, view performance, or audience enjoyment is a selection criterion.
---

# Audience-Led Topic Selection

Use audience demand and audience fit to choose topics, with demand the default primary ranking criterion for audience-facing topic discovery. Keep factual substantiation as a separate eligibility gate. A source's richness does not establish demand; popularity does not establish truth. The user may explicitly set a different creative priority.

## When to Use

- **Required:** whenever proposing, comparing, ranking, or validating content topics, including topic discovery before synopsis or script work.
- Also use when a user asks for the popular, searchable, trending, high-view, viral, or audience-favorite subject.
- Do not run discovery for a topic the user has locked and asks only to draft or transform; a request to assess that topic's prospects does invoke this skill.

## Retrieval handoff

When fresh public SNS video-post evidence is requested and not already supplied, use sibling [`searching-social-media`](../searching-social-media/SKILL.md) for bounded Flucto retrieval. Its source states, capture dates, publication-date unknowns, and metric units remain evidence inputs here—not a demand verdict. Reddit score/upvotes are not views; Flucto info counts can be ambiguous. Search-only requests do not invoke topic selection or a production interview. Missing runtime does not invalidate supplied evidence or authorize automatic installation/download.

## Decision Procedure

1. **Fix the comparison.** Record the decision objective (e.g. reach, qualified conversions, or retention), audience, platform, format (e.g. Shorts vs. long-form), language/region, evidence scope, and decision horizon. Compare like with like. Reuse the user's channel analytics and supplied research before collecting substitutes. An evidence-only comparison request fixes that comparison's objective, not a production-topic selection or a forecast.
2. **Build a small candidate set.** Preserve the requested count; otherwise offer five distinct candidates for an options request. Candidate hypotheses are not findings.
3. **Check demand with comparable evidence.** For each candidate, record dated URLs and the actual observation:
   - Compare several relevant videos across channels with similar formats and durations. Capture views, publication date, capture date, and—when publicly known—channel scale or a channel baseline. Prefer repeat outperformance across independent channels over one hit.
   - If search-interest data is available, use the same geography, time window, and comparable query terms. Identify index/relative-interest metrics as such; do not report them as absolute search volume.
   - Use comments as qualitative, self-selected feedback. Summarize recurring reactions; do not treat a few comments as a representative poll.
   - First-party impressions, click-through, retention, and returning-viewer data outrank public proxies for the user's own channel, when supplied and applicable.
4. **Normalize before interpreting.** Lifetime view totals confound channel reach, publication age, format, and promotion. Compare upload-age windows, format, duration, and channel-relative performance when data exists. Mark unavailable denominators and comparisons unknown. Do not infer a view forecast from search trends or one viral video.
   Record the metric's unit and population (views per video, channel total, relative search index, or supplied channel analytics). Compare only matching units, windows and populations; otherwise keep separate evidence groups. For supplied comparable 12k/4k/no-data observations, only the observed pair supports a 3:1 comparison; no-data stays `unknown`, never zero or last place by arithmetic. Give a conditional recommendation and identify the missing observation that could reverse it.
5. **Apply the truth/source gate independently.** For history, biography, documentary, or “true story” framing, verify the core event and claims against traceable primary or reliable scholarly sources. Separate recorded fact, testimony/allegation, oral tradition, interpretation, and dramatization. A folktale recorded in an archive is evidence of a recorded tradition, not proof that its events occurred. If substantiation fails, reject the factual framing or select a better-supported candidate; demand cannot waive this gate.
6. **Recommend from evidence, not a blended mystery score.** For audience-facing topic discovery, demand is the default primary ranking criterion; channel fit, purpose, and feasibility constrain or break close choices. Honor a different explicit user priority. State which candidates have stronger, weaker, or unknown demand evidence and why. State source readiness separately. When the user selects popularity as the priority, rank by demand evidence only among candidates that pass the factual gate. If evidence is inadequate, say so and give the smallest research action that could change the decision. An editorial placeholder may be offered only as an unvalidated hypothesis.
7. **Keep the audit trail compact.** For each recommended candidate, return: topic/hook; audience-demand evidence and source/date; comparison basis and caveats; factual source and fact-status; verdict/confidence; next verification gap. Never invent URLs, dates, view counts, audience analytics, search volumes, or source support.

## Evidence Labels

| Label | Meaning |
|---|---|
| `observed` | Directly retrieved or user-supplied evidence with its source and date/capture context. |
| `inference` | Interpretation of observed evidence; explain the comparison behind it. |
| `unknown` | Not available or not checked. Do not convert to a favorable or unfavorable fact. |
| `hypothesis` | Candidate suggested for testing, not audience-validated. |

Use a qualitative demand verdict (`repeated signal`, `limited signal`, `mixed`, `unknown`) rather than an arbitrary composite score. Explain disagreements between search interest, video performance, and comments instead of averaging incompatible measures.
## Provenance and validation scope

The comparison, normalization, evidence labels, and recommendation rules in this skill are package-authored decision controls. They are not a sourced theory of audience psychology, a validated demand-prediction model, or evidence that following the procedure increases views, retention, or conversion. User analytics and dated public observations are inputs for the current decision only; they do not validate the procedure's predictive accuracy. The 12k/4k/no-data example tests only arithmetic and missing-value handling: it does not establish a general demand ranking or forecast. Do not report an outcome claim beyond the observations actually supplied or retrieved.

For verification, supply two comparable counts and one missing observation. The result must compare only the observed pair, preserve the missing value as `unknown`, state the comparison limits, and avoid forecasting. This is a contract smoke, not a predictive-validity study.

## Common Mistakes

| Mistake | Correction |
|---|---|
| Select the highest lifetime-view video as proof a topic is popular. | Treat it as a lead; compare age-, format-, and channel-relevant observations. |
| Prefer a well-documented but low-demand topic when the user explicitly made popularity decisive. | First exclude candidates that fail the factual gate, then compare eligible candidates on supported demand. |
| Call a topic “trending” from undated or unverified search results. | Name the source, geography, query, time window, capture date, and metric—or mark demand unknown. |
| Treat archived folklore as a verified historical event. | Label it as a recorded/regionally transmitted story and keep history claims separate. |
| Score candidates precisely despite sparse, incomparable evidence. | Use evidence labels and state what is missing; no numeric confidence theater. |
| Start scripting while the audience-demand claim is only a guess. | Mark it as a hypothesis or finish the minimum comparable check before committing. |

## Acceptance

The recommendation is complete only when each candidate has a comparable demand observation or is explicitly `unknown`; the comparison basis includes the decision objective, audience, platform, format, geography, evidence scope, and time horizon; factual eligibility is assessed separately from demand; and the ranking names evidence, caveats, and the next verification gap. Missing audience, decision objective, or evidence scope blocks final topic selection. Research preparation, labeled hypotheses, and comparisons of supplied observations may still be returned within their stated limits; do not silently substitute maximizing views or invent metrics to complete a selection.
