#!/usr/bin/env python3
"""Validate a production plan or delivery manifest, never artistic/media quality."""
import argparse
import hashlib
import json
import math
import re
from pathlib import Path


def validate(p, profile='plan', base_dir=None):
    errors = []
    def error(s): errors.append(s)
    def text(v): return isinstance(v, str) and bool(v.strip())
    def number(v):
        try: return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        except OverflowError: return False
    def require_text(row, keys, label):
        for k in keys:
            if not text(row.get(k)): error(f'{label}: {k} must be a nonempty string')
    if not isinstance(p, dict): return ['project must be an object']
    if profile not in ('plan', 'preproduction', 'delivery'): return ['unknown validation profile']
    schema = p.get('schema_version', '1.0')
    if schema not in ('1.0', '1.1'): error('unsupported schema_version')
    require_text(p, ['project_id', 'version', 'aspect_ratio'], 'project')
    ratio = p.get('aspect_ratio')
    if not isinstance(ratio, str) or not re.fullmatch(r'[1-9]\d*:[1-9]\d*', ratio):
        error('aspect_ratio: positive integer pair required, e.g. 9:16')
    for k in ('target_duration_s', 'fps'):
        if not number(p.get(k)) or p[k] <= 0: error(f'{k}: positive finite number required')
    fps = p.get('fps')
    target = p.get('target_duration_s')
    def on_frame(v):
        if not number(v) or not number(fps): return False
        frame = v * fps
        return number(frame) and abs(frame-round(frame)) < 0.001
    if number(target) and number(fps) and not on_frame(target): error('target duration is not frame aligned')
    tables = {}
    required = ('scenes', 'characters', 'shots', 'claims', 'artifacts')
    optional = ('asset_registry', 'audio_cues', 'captions', 'issues', 'delivery_checks', 'generation_attempts')
    for key in required + optional:
        rows = p.get(key, [] if key in optional else None)
        if not isinstance(rows, list): error(f'{key}: array required'); rows = []
        tables[key] = {}
        for row in rows:
            if not isinstance(row, dict): error(f'{key}: entry must be an object'); continue
            rid = row.get('id')
            if not text(rid): error(f'{key}: nonempty id required'); continue
            if rid in tables[key]: error(f'{key}: duplicate id {rid}')
            tables[key][rid] = row
    for key in ('scenes', 'shots', 'artifacts'):
        if not tables[key]: error(f'{key}: at least one entry required')
    for rid, row in tables['scenes'].items(): require_text(row, ['purpose'], rid)
    for rid, row in tables['characters'].items():
        traits = row.get('locked_traits')
        if not (text(traits) or (isinstance(traits, (dict,list)) and bool(traits))):
            error(f'{rid}: nonempty locked_traits required')
    mode = p.get('audio_mode')
    if schema == '1.1' or profile == 'delivery':
        if mode not in ('no_audio', 'no_dialogue', 'dialogue'): error('audio_mode required: no_audio/no_dialogue/dialogue')
    if mode == 'no_audio' and tables['audio_cues']: error('no_audio project cannot contain audio cues')
    if mode == 'no_dialogue':
        if any(row.get('layer') in ('dialogue','voiceover') for row in tables['audio_cues'].values()):
            error('no_dialogue project contains speech cues')
    def exists_ref(value, table, label):
        if not isinstance(value,str) or value not in tables[table]: error(f'{label}: unknown {table} reference {value}'); return False
        return True
    def refs(row, field, table, label, required=False):
        values = row.get(field, None if required else [])
        if not isinstance(values,list): error(f'{label}: {field} must be an array'); return
        for v in values: exists_ref(v, table, label)
    look_id = p.get('look_asset_id')
    if look_id is not None:
        if not exists_ref(look_id, 'asset_registry', 'project look_asset_id'):
            look_id = None
        elif tables['asset_registry'][look_id].get('entity_type') not in (None, 'look'):
            error('look_asset_id must reference a look asset')
    spans = []
    for rid, s in tables['shots'].items():
        exists_ref(s.get('scene_id'), 'scenes', rid)
        refs(s, 'character_ids', 'characters', rid, required=True)
        refs(s, 'asset_ids', 'asset_registry', rid)
        refs(s, 'claim_ids', 'claims', rid)
        budget = s.get('retry_budget')
        if budget is not None and (not isinstance(budget,int) or isinstance(budget,bool) or budget<0):
            error(f'{rid}: retry_budget must be nonnegative int')
        require_text(s, ['purpose','start_state','end_state'], rid)
        a,b,d = s.get('start_s'),s.get('end_s'),s.get('source_duration_s')
        if not all(number(x) for x in (a,b,d)): error(f'{rid}: numeric times required'); continue
        if a < 0 or b <= a or d <= 0: error(f'{rid}: invalid interval'); continue
        if number(fps) and (not on_frame(a) or not on_frame(b)): error(f'{rid}: timeline boundaries are not frame aligned')
        rate=s.get('playback_rate',1)
        source_in=s.get('source_in_s',0)
        if not number(rate) or rate<=0: error(f'{rid}: playback_rate must be positive'); rate=1
        if not number(source_in) or source_in<0: error(f'{rid}: source_in_s must be nonnegative'); source_in=0
        if d + 1e-6 < source_in+(b-a)*rate: error(f'{rid}: insufficient source for trim and playback rate')
        timing = s.get('source_duration_status')
        if schema == '1.1' or profile == 'delivery':
            if timing not in ('estimated','measured'): error(f'{rid}: source_duration_status required')
        if profile == 'delivery' and timing != 'measured': error(f'{rid}: delivery requires measured source duration')
        spans.append((a,b,rid))
    spans.sort()
    cursor = 0
    for a,b,rid in spans:
        if abs(a-cursor) > 1e-6: error(f'{rid}: timeline gap or overlap at {cursor:g}s')
        cursor = b
    if number(target) and abs(cursor-target) > 1e-6: error('timeline does not equal target_duration_s')
    seen_attempts = set()
    for rid, at in tables['generation_attempts'].items():
        sid = at.get('shot_id')
        exists_ref(sid, 'shots', rid)
        num = at.get('attempt')
        if not isinstance(num,int) or isinstance(num,bool) or num<1:
            error(f'{rid}: attempt must be positive int'); num=None
        elif isinstance(sid,str):
            if (sid,num) in seen_attempts:
                error(f'{rid}: duplicate attempt {num} for {sid}')
            else: seen_attempts.add((sid,num))
        require_text(at, ['route'], rid)
        if at.get('result') not in ('accept','reject','conditional'):
            error(f'{rid}: result must be accept/reject/conditional')
        for k in ('changes','observed_failures','preserve'):
            if k in at and not isinstance(at[k],list): error(f'{rid}: {k} must be an array')
        fc = at.get('failure_class')
        if fc is not None and fc not in ('identity','product','motion','temporal','camera','performance','composite','creative','other'):
            error(f'{rid}: invalid failure_class')
        result_id = at.get('result_asset_id')
        if result_id is not None:
            if exists_ref(result_id,'asset_registry',rid) and at.get('result') == 'accept':
                if tables['asset_registry'][result_id].get('status') not in ('available','verified'):
                    error(f'{rid}: accepted attempt needs an available result asset')
        elif at.get('result') == 'accept':
            error(f'{rid}: accepted attempt needs result_asset_id')
        if isinstance(sid,str) and sid in tables['shots']:
            budget = tables['shots'][sid].get('retry_budget')
            if isinstance(budget,int) and not isinstance(budget,bool) and num is not None and num > budget+1:
                error(f'{rid}: attempt {num} exceeds retry_budget {budget}')
    for key in ('audio_cues','captions'):
        for rid,row in tables[key].items():
            a,b = row.get('start_s'),row.get('end_s')
            if not all(number(x) for x in (a,b)) or a<0 or b<=a or (number(target) and b>target+1e-6):
                error(f'{rid}: invalid {key} interval')
            if row.get('shot_id') is not None: exists_ref(row['shot_id'],'shots',rid)
            if key == 'captions': require_text(row,['text'],rid)
            else: require_text(row,['layer'],rid)
            if row.get('asset_id') is not None: exists_ref(row['asset_id'],'asset_registry',rid)
    for rid,c in tables['claims'].items():
        require_text(c,['statement'],rid)
        kind,status=c.get('kind'),c.get('status')
        if kind not in ('fact','inference','fiction'): error(f'{rid}: invalid claim kind')
        if status not in ('unverified','verified','fiction'): error(f'{rid}: invalid claim status')
        if status=='verified' and not text(c.get('source')): error(f'{rid}: verified claim needs a source locator')
        if (kind=='fiction') != (status=='fiction'): error(f'{rid}: inconsistent fiction label')
        if profile=='delivery' and status=='unverified': error(f'{rid}: unverified delivery claim')
        refs(c,'shot_ids','shots',rid)
    graph={}
    for rid,a in tables['artifacts'].items():
        require_text(a,['type','version'],rid)
        status=a.get('status')
        if status not in ('draft','reviewed','approved','generated','verified','stale'): error(f'{rid}: invalid artifact status')
        if status=='verified' and not text(a.get('evidence')): error(f'{rid}: verified artifact needs inspection evidence')
        if status=='approved':
            approval=a.get('approval')
            if not isinstance(approval,dict) or not all(text(approval.get(k)) for k in ('by','at','evidence')):
                error(f'{rid}: approved artifact needs by/at/evidence approval record')
        if status in ('generated','verified') and schema=='1.1':
            if not a.get('asset_ids'): error(f'{rid}: produced artifact needs asset_ids')
        refs(a,'asset_ids','asset_registry',rid)
        finality=a.get('finality')
        if finality is not None and finality not in ('preliminary','final'):
            error(f'{rid}: finality must be preliminary or final')
        required=a.get('required_asset_versions')
        if required is not None:
            if not isinstance(required,dict) or any(not isinstance(k,str) or not isinstance(v,str) or not v.strip() for k,v in required.items()):
                error(f'{rid}: required_asset_versions must map asset ids to versions'); required=None
            else:
                for required_id,expected in required.items():
                    target=tables['asset_registry'].get(required_id)
                    if target is None:
                        error(f'{rid}: required asset {required_id} is not registered')
                    else:
                        if target.get('version')!=expected and status!='stale':
                            error(f'{rid}: required asset {required_id} version drift')
                        if target.get('status')=='stale' and status!='stale':
                            error(f'{rid}: required asset {required_id} is stale')
        if finality=='final' and not required:
            # v5.1: declaring finality requires a nonempty pin map; the actual
            # FINAL production gate (verified assets, active LOOK, transitive
            # chain, draft/stale artifact rejection) is asset_gate
            # .check_asset_gate, invoked explicitly by the producer scope and
            # by the preproduction/delivery profiles — not globally here, so
            # stale-legacy final declarations and unrelated finality states do
            # not block narrow plan validation or stale propagation.
            error(f'{rid}: final artifact needs required_asset_versions')
        deps=a.get('dependencies',[])
        if not isinstance(deps,list): error(f'{rid}: dependencies must be an array'); deps=[]
        versions=a.get('dependency_versions',{})
        if not isinstance(versions,dict): error(f'{rid}: dependency_versions must be an object'); versions={}
        for d in versions:
            if d not in deps: error(f'{rid}: dependency version {d} is not a declared dependency')
        graph[rid]=[]
        for d in deps:
            if not exists_ref(d,'artifacts',rid): continue
            graph[rid].append(d)
            upstream=tables['artifacts'][d]
            if schema=='1.1' and d not in versions: error(f'{rid}: missing dependency version for {d}')
            if d in versions and versions[d] != upstream.get('version') and status != 'stale':
                error(f'{rid}: dependency {d} version mismatch')
            if status in ('reviewed','approved','generated','verified') and upstream.get('status')=='stale':
                error(f'{rid}: depends on stale artifact {d}')
    # Iterative cycle detection avoids recursion failures on large projects.
    indegree={k:0 for k in graph}
    for deps in graph.values():
        for d in deps: indegree[d]+=1
    queue=[k for k,v in indegree.items() if v==0]; visited=0
    while queue:
        k=queue.pop(); visited+=1
        for d in graph[k]:
            indegree[d]-=1
            if indegree[d]==0: queue.append(d)
    if visited != len(graph): error('cyclic artifact dependency')
    for rid, artifact in tables['artifacts'].items():
        if artifact.get('type') != 'video_execution_plan' or artifact.get('status') != 'approved':
            continue
        pending = list(graph[rid]); seen = set(); approved_review = False
        while pending:
            dependency = pending.pop()
            if dependency in seen: continue
            seen.add(dependency)
            row = tables['artifacts'][dependency]
            if row.get('type') == 'preproduction_review' and row.get('status') == 'approved':
                approved_review = True
            pending.extend(graph[dependency])
        if not approved_review:
            error(f'{rid}: approved execution plan needs an approved preproduction_review dependency')
    base=Path(base_dir).resolve() if base_dir is not None else None
    for rid,asset in tables['asset_registry'].items():
        require_text(asset,['kind','version'],rid)
        state=asset.get('status')
        if state not in ('planned','available','verified','stale'): error(f'{rid}: invalid asset status')
        et = asset.get('entity_type')
        if et is not None and et not in ('character','product','prop','location','wardrobe','look','lighting','graphic','environment'):
            error(f'{rid}: invalid entity_type')
        auth = asset.get('authority')
        if auth is not None and auth not in ('authoritative','inferred','derived'):
            error(f'{rid}: invalid authority')
        if 'entity_id' in asset:
            if not text(asset['entity_id']): error(f'{rid}: entity_id must be a nonempty string')
            elif et == 'character': exists_ref(asset['entity_id'], 'characters', rid)
        refs(asset, 'source_asset_ids', 'asset_registry', rid)
        if asset.get('result_asset_id') is not None: exists_ref(asset['result_asset_id'],'asset_registry',rid)
        role=asset.get('role')
        if role is not None and role not in ('master','derivative'):
            error(f'{rid}: asset role must be master or derivative')
        mref=asset.get('master_asset_ref')
        if mref is not None:
            if role is not None and role!='derivative':
                error(f'{rid}: master_asset_ref belongs on derivative assets')
            if not isinstance(mref,dict) or not text(mref.get('asset_id')) or not text(mref.get('version')):
                error(f'{rid}: master_asset_ref needs asset_id and version')
            else:
                if mref['asset_id']==rid: error(f'{rid}: derivative cannot reference itself as master')
                master=tables['asset_registry'].get(mref['asset_id'])
                if master is None:
                    error(f'{rid}: master asset {mref["asset_id"]} is not registered')
                else:
                    if state!='stale' and master.get('version')!=mref.get('version'):
                        error(f'{rid}: master version drift for {mref["asset_id"]}')
                    if master.get('status')=='stale' and state not in ('planned','stale'):
                        error(f'{rid}: derives from stale master {mref["asset_id"]}')
        for field in ('prompt','provider','model','workflow','generation_mode','negative_prompt','model_version','creator','license_status','seed','resolution','aspect_ratio','created','checksum'):
            value=asset.get(field)
            if value is not None and not (text(value) or (field in ('seed','resolution') and number(value))):
                error(f'{rid}: {field} must be a nonempty value or UNKNOWN/NOT_EXPOSED')
        if state in ('available','verified'):
            if not text(asset.get('path')): error(f'{rid}: available asset needs path')
            elif base is not None:
                path=(base/asset['path']).resolve()
                if not path.is_relative_to(base):
                    error(f'{rid}: local asset path escapes --base-dir')
                elif not path.is_file(): error(f'{rid}: local asset file missing')
                else:
                    digest=asset.get('sha256')
                    if digest is not None:
                        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest): error(f'{rid}: invalid SHA-256')
                        else:
                            h=hashlib.sha256()
                            with path.open('rb') as f:
                                for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
                            if h.hexdigest()!=digest: error(f'{rid}: file hash mismatch')
        if profile=='delivery' and state in ('planned','stale'): error(f'{rid}: unfinished delivery asset')
    for rid,issue in tables['issues'].items():
        if issue.get('severity') not in ('blocker','major','minor'): error(f'{rid}: invalid severity')
        if issue.get('status') not in ('open','resolved'): error(f'{rid}: invalid issue status')
        if profile=='delivery' and issue.get('severity')=='blocker' and issue.get('status')!='resolved': error(f'{rid}: unresolved blocker')
    if profile=='delivery':
        if base is None: error('delivery validation requires --base-dir')
        spec=p.get('delivery_spec')
        if not isinstance(spec,dict): error('delivery_spec must be object'); spec={}
        require_text(spec,['destination','container','video_codec','caption_mode','final_asset_id'], 'delivery_spec')
        for k in ('width','height'):
            if not isinstance(spec.get(k),int) or isinstance(spec.get(k),bool) or spec[k]<=0: error(f'delivery_spec: positive integer {k} required')
        if isinstance(ratio,str) and re.fullmatch(r'[1-9]\d*:[1-9]\d*',ratio) and all(number(spec.get(k)) for k in ('width','height')):
            rw,rh=map(int,ratio.split(':'))
            if spec['width']*rh != spec['height']*rw: error('delivery resolution and aspect ratio differ')
        cm=spec.get('caption_mode')
        if cm not in ('none','sidecar','burned_in'): error('invalid caption_mode')
        if cm!='none' and not tables['captions']: error('captioned delivery needs caption cues')
        if cm=='sidecar': exists_ref(spec.get('caption_asset_id'),'asset_registry','caption file')
        final=spec.get('final_asset_id')
        if exists_ref(final,'asset_registry','final file'):
            asset=tables['asset_registry'][final]
            if asset.get('status')!='verified' or not text(asset.get('sha256')): error('final file needs verified asset and hash')
        required_checks={'playback','timing','visual','audio','captions','continuity','claims'}
        checks={c.get('category'):c for c in tables['delivery_checks'].values() if isinstance(c.get('category'),str)}
        for category in sorted(required_checks):
            c=checks.get(category)
            if c is None: error(f'delivery check missing: {category}'); continue
            if c.get('status')=='pass':
                if not text(c.get('evidence')): error(f'{category}: passing check needs inspection evidence')
            elif c.get('status')=='not_applicable':
                if not text(c.get('reason')): error(f'{category}: N/A needs reason')
                if category in ('playback','timing','visual'): error(f'{category}: core media check cannot be N/A')
                if category=='audio' and mode!='no_audio': error('audio check required for audible delivery')
                if category=='captions' and cm!='none': error('caption check required for captioned delivery')
                if category=='claims' and tables['claims']: error('claim check required when claims exist')
            else: error(f'{category}: delivery check is not pass or justified N/A')
    ready_package = any(a.get('type') == 'preproduction_review' and a.get('status') in ('reviewed', 'approved')
                        or a.get('type') == 'video_execution_plan' and a.get('status') == 'approved'
                        for a in tables['artifacts'].values())
    if profile == 'preproduction' or ready_package:
        from validate_preproduction import validate_preproduction
        errors.extend(validate_preproduction(p, base_dir, image_backed_required=ready_package))
    if profile in ('preproduction', 'delivery') or ready_package:
        # Full-package gate: every declared-final artifact must clear the real
        # v5.1 production gate (verified assets, active LOOK, transitive chain).
        from asset_gate import check_asset_gate
        errors.extend(f'asset gate: {blocker}' for blocker in check_asset_gate(p))
    return errors


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project')
    parser.add_argument('--profile',choices=('plan','preproduction','delivery'),default='plan')
    parser.add_argument('--base-dir')
    args=parser.parse_args()
    try:
        p=json.loads(Path(args.project).read_text(encoding='utf-8'))
        errors=validate(p,args.profile,args.base_dir)
    except (OSError,ValueError) as e:
        print(json.dumps({'valid':False,'errors':[str(e)]},ensure_ascii=False)); return 2
    print(json.dumps({'valid':not errors,'profile':args.profile,'errors':errors,'scope':'contract, timing, references and declared inspection records; preproduction also checks actual package files and image decoding, not artistic or approval verification'},ensure_ascii=False,indent=2))
    return 1 if errors else 0
if __name__=='__main__': raise SystemExit(main())
