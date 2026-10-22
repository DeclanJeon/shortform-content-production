#!/usr/bin/env python3
"""Check source/known credential patterns, including reachable local Git history."""
from pathlib import Path
import re
import subprocess
import sys
sys.dont_write_bytecode=True

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/distribution'))
from installer import verify_source

DENIED={'.env','auth.json','install-state.json','creative-runtime.json','runtime-compose.json','doctor-receipt.json'}
FOLDERS={'.history','backups','secrets','production','coordinator-state','runtime-recovery'}
BINARY={'.gpg','.age','.sqlite','.dump','.mp4','.wav','.png','.zip','.pyc','.key','.pem','.p12','.pfx'}
PATTERNS=[
    re.compile(r'(?:sk-(?:proj-)?[A-Za-z0-9_-]{24,}|ya29\.[A-Za-z0-9_-]{30,}|Bearer [A-Za-z0-9_-]{32,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'),
    re.compile(r'''["'](?:API_TOKEN|WORKER_TOKEN|N8N_ENCRYPTION_KEY|POSTGRES_PASSWORD|refresh_token|access_token|hmacSecret|client_secret)["']\s*:\s*["'](?!REPLACE|CONFIGURE|YOUR|fixture|test|NON_FUNCTIONAL)[A-Za-z0-9_./+~=-]{20,}'''),
    re.compile(r'(?m)^(?:API_TOKEN|WORKER_TOKEN|N8N_ENCRYPTION_KEY|POSTGRES_PASSWORD)=[A-Za-z0-9_./+~=-]{20,}'),
]


def scan(name: str, text: str) -> None:
    path=Path(name)
    if (path.name in DENIED or set(path.parts)&FOLDERS or path.suffix in BINARY
            or path.name.startswith('activation-before-')
            or (path.name.startswith('.env') and path.name!='.env.example')):
        raise SystemExit('Refused private/artifact path: '+name)
    if any(pattern.search(text) for pattern in PATTERNS):
        raise SystemExit('Possible credential in: '+name)


def main() -> None:
    verify_source(ROOT/'src')
    git=(ROOT/'.git').exists()
    if git:
        result=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True,text=True,check=True)
        files=[ROOT/name for name in result.stdout.split('\0') if name]
    else:files=[p for p in ROOT.rglob('*') if p.is_file()]
    for path in files:
        name=path.relative_to(ROOT)
        if '.git' in name.parts:continue
        if path.is_symlink():raise SystemExit('Refused source symlink: '+name.as_posix())
        scan(name.as_posix(),path.read_text())
    blobs=0
    if git:
        history=subprocess.run(['git','rev-list','--objects','--all'],cwd=ROOT,capture_output=True,text=True,check=True)
        seen=set()
        for line in history.stdout.splitlines():
            oid,_,name=line.partition(' ')
            if not name or oid in seen:continue
            kind=subprocess.run(['git','cat-file','-t',oid],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
            if kind!='blob':continue
            seen.add(oid)
            value=subprocess.run(['git','cat-file','blob',oid],cwd=ROOT,capture_output=True,check=True).stdout
            scan(name,value.decode('utf-8'));blobs+=1
    print('PASS: manifest, '+str(len(files))+' source files and '+str(blobs)+' reachable history blobs checked; no credential values printed.')


if __name__=='__main__':main()
