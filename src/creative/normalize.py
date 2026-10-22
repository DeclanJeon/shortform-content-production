"""Canonicalize model-created technical IDs only; never rewrite story content.

Arrays/order/identities/words remain unchanged. Unknown/duplicate references fail,
not guessed. Preserve the raw model response and this mapping in runtime evidence.
"""
from copy import deepcopy


def canonicalize_generated_ids(plan):
    if not isinstance(plan,dict):raise ValueError('plan_type')
    p=deepcopy(plan);maps={}
    id_tables=[('beats','B'),('characters','CH'),('narration_segments','S'),('shots','SH')]
    if p.get('schema_version')=='3.0':
        id_tables.append(('scenes','SC'))
    for key,prefix in id_tables:
        xs=p.get(key)
        if not isinstance(xs,list):raise ValueError('ids')
        old=[x.get('id') if isinstance(x,dict) else None for x in xs]
        if any(not isinstance(x,str) or not x for x in old) or len(set(old))!=len(old):raise ValueError('ids')
        maps[key]={value:f'{prefix}{i:02d}' for i,value in enumerate(old,1)}
        for x in xs:x['id']=maps[key][x['id']]
    for shot in p['shots']:
        try:
            shot['beat_id']=maps['beats'][shot['beat_id']]
            shot['segment_id']=maps['narration_segments'][shot['segment_id']]
            shot['character_ids']=[maps['characters'][c] for c in shot['character_ids']]
            if p.get('schema_version')=='3.0':
                shot['scene_id']=maps['scenes'][shot['scene_id']]
        except (KeyError,TypeError):raise ValueError('unknown_id_reference') from None
    if p.get('schema_version')=='3.0':
        try:
            for scene in p['scenes']:
                scene['beat_ids']=[maps['beats'][bid] for bid in scene['beat_ids']]
            for panel in p['storyboard']['panels']:
                panel['shot_id']=maps['shots'][panel['shot_id']]
                panel['scene_id']=maps['scenes'][panel['scene_id']]
                panel['beat_ids']=[maps['beats'][bid] for bid in panel['beat_ids']]
        except (KeyError,TypeError):raise ValueError('unknown_id_reference') from None
    changed={key:{a:b for a,b in table.items() if a!=b} for key,table in maps.items()}
    return p,{key:value for key,value in changed.items() if value}
