"""SSH/docker-stdin client for local creative runtime; secrets stay in gateway.

Only a fixed automation-route allowlist is forwarded. Never prints credentials,
request headers, raw upstream exceptions, or an arbitrary URL response.
"""
import base64
import hashlib
import json
import math
import os
import re
import sys
from urllib.request import Request,urlopen
from urllib.error import HTTPError


def main():
    raw=sys.stdin.buffer.read(512*1024+1)
    try:
        if len(raw)>512*1024:raise ValueError()
        b=json.loads(raw)
        if not isinstance(b,dict) or set(b)!={'method','path','body'}:raise ValueError()
        method,path=b['method'],b['path']
        valid=(method=='POST' and (path in ('/v1/automation/claim','/v1/automation/promote-buffer','/v1/automation/claim-media','/v1/pipeline/tts','/v1/pipeline/render','/v1/project-bundles/register') or re.fullmatch(r'/v1/automation/tasks/[1-9][0-9]*/(?:complete-plan|fail|policy)',path) or re.fullmatch(r'/v1/automation/media-tasks/[1-9][0-9]*/(?:complete|fail)',path) or re.fullmatch(r'/v1/automation/image-intents/[0-9]{8}-auto-[0-9a-f]{12}/(?:CH|SH)[0-9]{2}/(?:reserve|complete)',path))) or (method=='GET' and (path in ('/v1/automation/status','/v1/automation/media-status','/v1/automation/policy-tasks') or re.fullmatch(r'/v1/automation/(?:tasks|media-tasks)/[1-9][0-9]*',path) or re.fullmatch(r'/v1/jobs/[0-9a-f-]{36}(?:/artifact)?',path)))
        if not valid:raise ValueError()
        data=None if method=='GET' else json.dumps(b['body'],ensure_ascii=False).encode()
        request=Request('http://127.0.0.1:8080'+path,data=data,method=method,headers={'Authorization':'Bearer '+os.environ['API_TOKEN'],'Content-Type':'application/json'})
        with urlopen(request,timeout=40) as response:
            if path.endswith('/artifact'):
                size=int(response.headers['X-Artifact-Size-Bytes']);sha=response.headers['X-Artifact-SHA256'];duration=float(response.headers['X-Artifact-Duration-Seconds']);codec=response.headers['X-Artifact-Codec']
                if not 0<size<=32*1024*1024 or not re.fullmatch(r'[0-9a-f]{64}',sha) or codec!='h264' or not math.isfinite(duration) or not 0<duration<=180:raise ValueError()
                raw=response.read(size+1)
                if len(raw)!=size or hashlib.sha256(raw).hexdigest()!=sha:raise ValueError()
                result={'artifact_base64':base64.b64encode(raw).decode(),'sha256':sha,'size_bytes':size,'duration_seconds':duration,'codec':codec}
            else:
                raw=response.read(512*1024+1)
                if len(raw)>512*1024:raise ValueError()
                result=json.loads(raw)
            print(json.dumps({'http_status':response.status,'body':result},ensure_ascii=False))
    except HTTPError as error:
        # API details are fixed machine codes, never provider/credential data.
        try:detail=json.loads(error.read(2048)).get('detail','gateway_error')
        except Exception:detail='gateway_error'
        print(json.dumps({'http_status':error.code,'error':detail}))
    except Exception:print(json.dumps({'http_status':503,'error':'creative_bridge_unavailable'}))

if __name__=='__main__':main()
