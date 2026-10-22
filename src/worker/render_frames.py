"""Sample three actual rendered frames, tied to known MP4 SHA; no credential use."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def main():
    try:
        b=json.loads(sys.stdin.buffer.read(4096));job=b['job_id'];sha=b['sha256'];duration=b['duration_seconds']
        if not re.fullmatch(r'[0-9a-f-]{36}',job) or not re.fullmatch(r'[0-9a-f]{64}',sha) or type(duration) not in (int,float) or not 0<duration<=180:raise ValueError()
        root=Path(os.environ.get('ARTIFACT_ROOT','/data/artifacts')).resolve(strict=True);video=(root/job/'render.mp4').resolve(strict=True)
        if not video.is_relative_to(root):raise ValueError()
        digest=hashlib.sha256()
        with video.open('rb') as f:
            while chunk:=f.read(1024*1024):digest.update(chunk)
        if digest.hexdigest()!=sha:raise ValueError()
        outputs=[]
        for i,t in enumerate((min(.5,duration/4),duration/2,max(0,duration-.5)),1):
            path=video.parent/f'qa-frame-{i:02}.png'
            subprocess.run(['ffmpeg','-v','error','-y','-ss',str(t),'-i',str(video),'-frames:v','1','-vf','scale=360:640',str(path)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30)
            raw=path.read_bytes()
            if len(raw)>2*1024*1024:raise ValueError()
            outputs.append({'frame_time':t,'sha256':hashlib.sha256(raw).hexdigest(),'png_base64':base64.b64encode(raw).decode()})
        print(json.dumps({'job_id':job,'source_sha256':sha,'frames':outputs}))
    except Exception:print(json.dumps({'error':'render_frame_sampling_failed'}));raise SystemExit(1)

if __name__=='__main__':main()
