"""Deterministic, provenance-bound lettering, never another paid image attempt."""
import hashlib
import json
from pathlib import Path
import re
from .media_runtime import _atomic,_sha,_safe,_korean_font


def _projective_coefficients(destination,source):
    rows=[]
    for (x,y),(u,v) in zip(destination,source):
        rows.extend([[x,y,1,0,0,0,-u*x,-u*y,u],[0,0,0,x,y,1,-v*x,-v*y,v]])
    for i in range(8):
        pivot=max(range(i,8),key=lambda j:abs(rows[j][i]))
        rows[i],rows[pivot]=rows[pivot],rows[i]
        div=rows[i][i]
        if abs(div)<1e-10:raise ValueError('invalid_lettering_quad')
        rows[i]=[v/div for v in rows[i]]
        for j in range(8):
            if j!=i:
                ratio=rows[j][i];rows[j]=[a-ratio*b for a,b in zip(rows[j],rows[i])]
    return tuple(r[-1] for r in rows)


def apply_lettering(root,specs,assets,base,config):
    recipe=root/'00_MANIFEST/scene-lettering-v1.json'
    if not recipe.exists():return
    records=json.loads(recipe.read_text())
    if not isinstance(records,list):raise ValueError('invalid_lettering_recipe')
    by_id={s['asset_id']:s for s in specs}
    seen=set()
    from PIL import Image,ImageDraw
    for record in records:
        aid=record['asset_id']
        if aid in seen or not re.fullmatch(r'SH[0-9]{2}',aid) or aid not in by_id:raise ValueError('invalid_lettering_asset')
        seen.add(aid);spec=by_id[aid];source=Path(assets[aid]);source_sha=_sha(source)
        if source_sha!=record['source_sha256']:raise ValueError('lettering_source_changed')
        lines=record['lines']
        if not isinstance(lines,list) or not 1<=len(lines)<=2 or any(not isinstance(s,str) or not s.strip() or len(s)>40 for s in lines):raise ValueError('invalid_lettering_text')
        text=re.sub(r'\s+','',''.join(lines))
        # Text must be literal approved shot direction, not a new generated claim.
        plan=json.loads((root/'00_MANIFEST/creative-plan-v1.json').read_text())
        ids=[s['id'] for s in plan['shots']]
        text_id=record.get('text_shot_id',aid)
        if text_id not in ids or ids.index(aid)-ids.index(text_id) not in (0,1):raise ValueError('invalid_lettering_text_source')
        action=plan['shots'][ids.index(text_id)]['action']
        if text not in re.sub(r'\s+','',action):raise ValueError('lettering_not_in_approved_plan')
        font=_korean_font(config,42)
        binding={'source_sha256':source_sha,'recipe':record,'font_sha256':_sha(font.path),'tool':'pillow-lettering-v1'}
        revision=hashlib.sha256(json.dumps(binding,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:8]
        output=source.with_name(source.stem+'-lettered-'+revision+'.png');marker=base/'operations'/(aid+'-lettering-'+revision+'.json')
        if marker.exists():
            old=json.loads(marker.read_text())
            if old.get('binding')!=binding or not output.is_file() or _sha(output)!=old.get('sha256'):raise RuntimeError('lettering_cache_conflict')
        else:
            image=Image.open(source).convert('RGB');w,h=image.size
            if record.get('paper_quad'):
                quad=record['paper_quad']
                if len(quad)!=4 or any(len(p)!=2 or any(type(v) not in (int,float) or not 0<=v<=1 for v in p) for p in quad):raise ValueError('invalid_lettering_quad')
                paper=Image.new('RGBA',(360,280),(249,240,220,255));ink=ImageDraw.Draw(paper)
                size=30
                while size>10:
                    face=_korean_font(config,size)
                    if max(ink.textbbox((0,0),line,font=face)[2] for line in lines)<330:break
                    size-=1
                for i,line in enumerate(lines):
                    bounds=ink.textbbox((0,0),line,font=face)
                    ink.text(((360-bounds[2])//2,75+i*55-bounds[1]),line,font=face,fill=(37,34,28,255))
                target=[(x*w,y*h) for x,y in quad]
                coeff=_projective_coefficients(target,[(0,0),(360,0),(360,280),(0,280)])
                layer=paper.transform(image.size,Image.Transform.PERSPECTIVE,coeff,resample=Image.Resampling.BICUBIC)
                image.paste(layer,(0,0),layer)
            # One enlarged receipt inset above the reserved caption band. Explicit
            # label distinguishes this from another physical object/scene.
            box=(round(w*.08),round(h*.65),round(w*.92),round(h*.755))
            draw=ImageDraw.Draw(image)
            draw.rounded_rectangle(box,radius=12,fill=(253,247,231),outline=(123,107,88),width=2)
            label_font=_korean_font(config,max(14,round(w*.018)))
            draw.text((box[0]+18,box[1]+9),'영수증 확대',font=label_font,fill=(112,99,80))
            size=round(w*.042)
            while size>12:
                font=_korean_font(config,size)
                if max(draw.textbbox((0,0),line,font=font)[2] for line in lines)<box[2]-box[0]-40:break
                size-=1
            y=box[1]+round(h*.027)
            for line in lines:
                b=draw.textbbox((0,0),line,font=font);width=b[2]-b[0]
                draw.text(((w-width)//2,y-b[1]),line,font=font,fill=(32,30,27));y+=round(size*1.25)
            image.save(output,format='PNG');output.chmod(0o600)
            _atomic(marker,{'binding':binding,'sha256':_sha(output)})
        spec['relative_path']=str(output.relative_to(root));spec['lettering']=binding
        assets[aid]=output
