"""Two separate requests: read all actual panels first, then compare intent."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .contract import canonical_sha256
from .planning_contract import JsonObject
from .review_board import board


def review_sequence(root: Path, plan: JsonObject, specs: list[JsonObject], assets: dict[str, Path],
                    base: Path, config: JsonObject, event) -> list[JsonObject]:
    """Keep image-only evidence separate from a script-informed emotion comparison."""
    from .media_runtime import _atomic, _default_review, _review_shape, _sha, VisualReviewRejected
    pages = [board(specs[i:i+6], assets, base, show_labels=False)
             for i in range(0, len(specs), 6)]
    paths = [p for p, _ in pages]
    panels = [panel for _, group in pages for panel in group]
    ids = [s['id'] for s in plan['shots']]
    source_stage=('extracted_storyboard_panels' if plan.get('schema_version')=='3.0'
                  else 'generated_scene_assets')
    identity = {'source_stage':source_stage,'panels':panels,
                'pages':[{'sha256':_sha(p)} for p in paths]}
    model = config.get('vision_model', config.get('model'))
    token = canonical_sha256({'images':identity, 'plan':plan, 'model':model})[:24]
    readings_path = base/'private'/f'visual-reading-{token}.json'
    compare_path = base/'private'/f'emotion-comparison-{token}.json'

    def assess(name: str, prompt: str, output: Path) -> JsonObject:
        if output.is_file():
            try: result=json.loads(output.read_text())
            except json.JSONDecodeError: raise RuntimeError('visual_sequence_review_invalid') from None
            if not isinstance(result,dict):raise RuntimeError('visual_sequence_review_invalid')
            if event:event(name,prompt,'completed',outputs=(str(output),),details={'cached_assessment':True,'decision':result.get('decision')})
            return result
        if event:event(name, prompt, 'started', details={'pages':identity['pages']})
        result = _default_review(base, name, prompt, paths, {**config,'_structured_emotion_review':True})
        _atomic(output, result)
        if event:event(name, prompt, 'completed', outputs=(str(output),), details={'decision':result.get('decision')})
        return result

    prompt = ('Read the attached actual SCENE panels in order, using ONLY visible images. '
        'No script, synopsis, acting notes or music is supplied. Do not invent motives/feelings '
        'that are not visually readable; report uncertainty, and distinguish action from emotion. '
        'Return JSON {decision:PASS|REVISE,issues:[],readings:[{shot_id,visible_action,visible_emotion}]}. '
        'One reading per shot_id, in exact order. Reject unreadable/missing panels, not merely '
        'the inability of pictures to state exact dialogue. Every blocking issue must name its '
        'affected SHxx shot_id so the existing bounded repair gate can target it.\n'
        +json.dumps({'shot_ids':ids,**identity},ensure_ascii=False))
    reading = assess('image-only-sequence-'+token, prompt, readings_path)
    rows = reading.get('readings')
    if (set(reading) != {'decision','issues','readings'} or not isinstance(rows, list)
            or [r.get('shot_id') for r in rows if isinstance(r,dict)] != ids
            or any(set(r) != {'shot_id','visible_action','visible_emotion'}
                   or any(not isinstance(r[k],str) or not r[k].strip() for k in ('visible_action','visible_emotion')) for r in rows)
            or not _review_shape({k:reading.get(k) for k in ('decision','issues')})):
        raise RuntimeError('visual_sequence_review_invalid')
    if reading['decision'] != 'PASS':
        raise VisualReviewRejected(reading['issues'],ids,readings_path)
    critical = [s for s in plan['shots'] if s['performance']['critical']]
    critical_ids = [s['id'] for s in critical]
    comparison = assess('emotion-sequence-'+token,
        'Compare the previously recorded image-only readings with this approved spoken story and '
        'the same actual attached panels. Do not pretend the script made a missing visible reaction '
        'present. Check causes, receiving/listening reactions, emotional contrast, physical location '
        'and arrival/reveal order, and protagonist payoff across the whole sequence. Warm colors or '
        'chibi style are not defects. Functional emotion/framing omissions are not optional decoration. '
        'Do not require exact dialogue to be readable without sound. Return JSON '
        '{decision:PASS|REVISE,issues:[],checks:[{shot_id,status:PASS|REVISE,evidence}]}. '
        'Each critical shot must have exactly one check, in order; evidence cites actual visible '
        'cues and the preserved image-only reading. Failed checks require issues and REVISE.\n'+json.dumps({
            'image_only_readings':reading,'narrative':plan['narrative'],
            'shot_contracts':plan['shots'],'critical_shot_ids':critical_ids},ensure_ascii=False), compare_path)
    raw_compare_path=compare_path
    rejection_proof=raw_compare_path
    comparison_evidence_paths=[raw_compare_path]
    raw_issues=comparison.get('issues')
    if isinstance(raw_issues,list) and raw_issues and all(isinstance(issue,dict) for issue in raw_issues):
        if any(set(issue)!={'issue','revision','shot_id'} or issue.get('shot_id') not in critical_ids
               or any(not isinstance(issue.get(key),str) or not issue[key].strip() for key in ('issue','revision'))
               for issue in raw_issues):
            raise RuntimeError('visual_sequence_review_invalid')
        revise_ids={check.get('shot_id') for check in comparison.get('checks',[]) if isinstance(check,dict) and check.get('status')=='REVISE'}
        issue_ids={issue['shot_id'] for issue in raw_issues}
        if issue_ids!=revise_ids:raise RuntimeError('visual_sequence_review_invalid')
        normalized_issues=[f"{issue['shot_id']}: {issue['issue']} Suggested visual correction: {issue['revision']}" for issue in raw_issues]
        normalized={**comparison,'issues':normalized_issues}
        normalized_path=base/'private'/f'emotion-comparison-normalized-{token}.json'
        meta_path=base/'private'/f'emotion-comparison-normalization-{token}.json'
        meta={'normalizer':'structured-review-issues-v1','source_sha256':_sha(raw_compare_path),
              'normalized_sha256':canonical_sha256(normalized),'target_shot_ids':sorted(issue_ids)}
        if normalized_path.exists() and json.loads(normalized_path.read_text())!=normalized:
            raise RuntimeError('visual_sequence_review_cache_conflict')
        if meta_path.exists() and json.loads(meta_path.read_text())!=meta:
            raise RuntimeError('visual_sequence_review_cache_conflict')
        _atomic(normalized_path,normalized);_atomic(meta_path,meta)
        if event:event('emotion-sequence-normalization-'+token,'Deterministically normalize the completed structured issue objects; no model request.','completed',outputs=(str(normalized_path),str(meta_path)),details=meta)
        comparison=normalized;rejection_proof=normalized_path
        comparison_evidence_paths.extend((normalized_path,meta_path))
    checks = comparison.get('checks')
    if (set(comparison) != {'decision','issues','checks'} or not isinstance(checks,list)
            or any(not isinstance(c,dict) for c in checks)
            or not set(critical_ids) <= {c.get('shot_id') for c in checks}
            or [c.get('shot_id') for c in checks] != [sid for sid in ids if sid in {c.get('shot_id') for c in checks}]
            or any(set(c) != {'shot_id','status','evidence'} or c['status'] not in ('PASS','REVISE')
                   or not isinstance(c['evidence'],str) or len(c['evidence'].strip()) < 10 for c in checks)
            or not _review_shape({k:comparison.get(k) for k in ('decision','issues')})
            or (comparison['decision']=='PASS' and any(c['status']!='PASS' for c in checks))):
        raise RuntimeError('visual_sequence_review_invalid')
    if comparison['decision'] != 'PASS':
        raise VisualReviewRejected(comparison['issues'],critical_ids,rejection_proof)
    profile='image-only-storyboard-v2' if source_stage=='extracted_storyboard_panels' else 'image-only-emotion-v1'
    return [{'evidence_artifact_ids':['sha256:'+_sha(readings_path),*['sha256:'+_sha(p) for p in comparison_evidence_paths]],
             'checker_version':f'{config.get("provider","openai-codex")}:{model}:{profile}',
             'source_stage':source_stage,
             'image_only_reading':reading,'emotion_comparison':comparison,'source_panels':panels}]
