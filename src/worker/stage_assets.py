"""Trusted SSH/docker-stdin PNG staging; no network, secret or arbitrary path input."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import tempfile


def stage(b,root):
    if set(b)!={'story_id','asset_id','sha256','png_base64'} or not re.fullmatch(r'[0-9]{8}-auto-[0-9a-f]{12}',b['story_id']) or not re.fullmatch(r'SH(?:0[1-9]|1[0-9]|2[0-4])',b['asset_id']) or not re.fullmatch(r'[0-9a-f]{64}',b['sha256']):raise ValueError('invalid_stage_identity')
    raw=base64.b64decode(b['png_base64'],validate=True)
    if not 24<=len(raw)<=8*1024*1024 or raw[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',raw[16:24])!=(1080,1920) or hashlib.sha256(raw).hexdigest()!=b['sha256']:raise ValueError('invalid_stage_png')
    root=Path(root).resolve(strict=True);folder=root/'auto-staged'/b['story_id'];path=folder/(b['asset_id']+'.png')
    if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('stage_path_escape')
    folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest()!=b['sha256']:raise ValueError('stage_hash_conflict')
    else:
        fd,name=tempfile.mkstemp(dir=folder)
        try:
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'wb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
            os.link(name,path)
        finally:os.unlink(name)
    return {'artifact_path':str(path),'sha256':b['sha256'],'size_bytes':len(raw)}


def main():
    try:
        raw=sys.stdin.buffer.read(12*1024*1024+1)
        if len(raw)>12*1024*1024:raise ValueError('stage_body_limit')
        print(json.dumps(stage(json.loads(raw),os.environ.get('ARTIFACT_ROOT','/data/artifacts'))))
    except Exception:print(json.dumps({'error':'artifact_staging_failed'}));raise SystemExit(1)

if __name__=='__main__':main()
