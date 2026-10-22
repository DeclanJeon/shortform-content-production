#!/usr/bin/env python3
"""Explicit developer action, never part of installation or automatic updates."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--accept-source-changes',action='store_true')
    parser.add_argument('--add',action='append',default=[])
    parser.add_argument('--remove',action='append',default=[])
    args=parser.parse_args()
    if not args.accept_source_changes:raise SystemExit('Explicit --accept-source-changes is required.')
    root=Path(__file__).resolve().parents[1]/'src'
    path=root/'MANIFEST.json';manifest=json.loads(path.read_text())
    existing={e['path'] for e in manifest['entries']}
    removed=set(args.remove)
    if removed-existing:raise SystemExit('Cannot remove a path absent from the manifest.')
    names=(existing-removed)|set(args.add)
    denied={'secrets','backups','production','coordinator-state','.history','.git'}
    entries=[]
    for name in sorted(names):
        relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or set(relative.parts)&denied or '\\' in name:
            raise SystemExit('Refused path.')
        p=root/relative
        if any(x.is_symlink() for x in (p,*p.parents)) or not p.is_file() or p.name.startswith('.'):
            raise SystemExit('Expected ordinary source file: '+name)
        if p.suffix not in ('.py','.json','.yml','.md','.txt','.sh','.sql') and p.name not in ('Dockerfile','creative.Dockerfile'):
            raise SystemExit('Unsupported source type: '+name)
        raw=p.read_bytes();entries.append({'path':name,'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw)})
    manifest['entries']=entries;manifest['release_status']='developer-modified-requires-tests-and-review'
    path.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    print('Manifest refreshed. Run public-data checks, tests and review before distributing. Existing instances are NOT migrated.')


if __name__=='__main__':main()
