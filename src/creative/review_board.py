"""Labeled QA-only composites prevent scene/reference attachment-order ambiguity."""
import hashlib
import json
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont


def board(specs,assets,base,*,show_labels=True):
    panels=[{'id':s['asset_id'],'role':'REFERENCE' if s['kind']=='character_image' else 'SCENE',
             'sha256':hashlib.sha256(Path(assets[s['asset_id']]).read_bytes()).hexdigest()} for s in specs]
    key=hashlib.sha256(json.dumps({'panels':panels,'show_labels':show_labels},sort_keys=True).encode()).hexdigest()[:24]
    path=base/'review-boards'/(key+'.png');path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not path.exists():
        columns=3;width=400;height=760 if show_labels else 560
        canvas=Image.new('RGB',(columns*width,((len(specs)+columns-1)//columns)*height),'#f2f2f2')
        draw=ImageDraw.Draw(canvas)
        font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',24) if show_labels else None
        for i,(spec,panel) in enumerate(zip(specs,panels)):
            x=(i%columns)*width;y=(i//columns)*height
            top=0
            if show_labels:
                draw.rectangle((x,y,x+width-1,y+44),fill='#16334f' if panel['role']=='SCENE' else '#454545')
                draw.text((x+12,y+7),panel['role']+' '+panel['id'],font=font,fill='white')
                top=48
            with Image.open(assets[spec['asset_id']]) as original:image=original.convert('RGB')
            margin=8 if show_labels else 16
            image.thumbnail((width-margin,height-top-margin),Image.Resampling.LANCZOS)
            canvas.paste(image,(x+(width-image.width)//2,y+top+(height-top-image.height)//2))
        canvas.save(path,'PNG');path.chmod(0o600)
    return path,panels
