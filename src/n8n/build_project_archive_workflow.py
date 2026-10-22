"""Reuse stable-ID/checksummed native Drive archival for the private project ZIP."""
import json
from build_automatic_archive_workflow import build as video_workflow
WORKFLOW_ID='SFAutomaticProjectArchive'


def get_workflow():
    raw=json.dumps(video_workflow(),ensure_ascii=False)
    raw=raw.replace('/v1/automatic-archives','/v1/project-archives').replace('video/mp4','application/zip').replace("'h264'","'zip'")
    value=json.loads(raw);value['id']=WORKFLOW_ID;value['name']='SF - Automatic Private Production Project Archive'
    for node in value['nodes']:
        if node['type']=='n8n-nodes-base.httpRequest':
            node['parameters'].setdefault('options',{})['timeout']=120000
    return value


if __name__=='__main__':
    from pathlib import Path
    root=Path(__file__).resolve().parent/'workflows';root.mkdir(exist_ok=True)
    (root/(WORKFLOW_ID+'.json')).write_text(json.dumps(get_workflow(),ensure_ascii=False,indent=2)+'\n')
