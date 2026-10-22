"""Read-only cycle observation. Business completion is NOT scheduler provenance."""
import json
from pathlib import Path
import shlex
import subprocess
from .pi_runtime import _atomic_json

QUERY='''import os,json,psycopg
from psycopg.rows import dict_row
with psycopg.connect(os.environ['DATABASE_URL'],row_factory=dict_row) as db:
 parents=db.execute("SELECT id,aggregate_id,status,payload FROM outbox WHERE event_type='creative_production' ORDER BY id DESC LIMIT 20").fetchall()
 out=[]
 for row in parents:
  p=row['payload'];events=db.execute("SELECT id,event_type,status,payload FROM outbox WHERE aggregate_id=%s AND event_type IN ('creative_plan_ready','automatic_video_archive','automatic_project_archive','manual_youtube_release','production_projection') ORDER BY id",(row['aggregate_id'],)).fetchall()
  by={e['event_type']:e for e in events};r=by.get('manual_youtube_release',{});rp=r.get('payload',{})
  video=by.get('automatic_video_archive',{});project=by.get('automatic_project_archive',{});sheets=by.get('production_projection',{})
  complete=(r.get('status')=='PUBLISHED' and rp.get('public_status_verified') is True and video.get('status')=='ARCHIVED' and project.get('status')=='ARCHIVED' and sheets.get('status')=='DELIVERED')
  out.append({'task_id':row['id'],'story_id':row['aggregate_id'],'source_date':p['source_date'],'lane':p.get('planning_lane','primary'),'plan_state':row['status'],'media_state':by.get('creative_plan_ready',{}).get('status'),'video_id':rp.get('video_id'),'release_id':r.get('id'),'video_archive_id':video.get('id'),'project_archive_id':project.get('id'),'provider_completion_verified':complete,'operator_markers_present':any(k.startswith('operator_') or k=='planning_recovery' for k in p),'unattended_verified':False,'missing_evidence':['native_scheduler_execution_lineage']})
 print(json.dumps({'observations':out}))
'''


def observe(config,gateway):
    command=['docker','exec','-i',gateway.gateway_container,'python','-c',QUERY]
    if gateway.host!='local':command=['ssh','-T','-o','BatchMode=yes',gateway.host,shlex.join(command)]
    p=subprocess.run(command,capture_output=True,text=True,timeout=30)
    if p.returncode:raise RuntimeError('cycle_observation_unavailable')
    result=json.loads(p.stdout);start=config.get('observe_from_source_date','9999-12-31')
    result['fresh_cycle_observations']=[r for r in result['observations'] if r['source_date']>=start and r['lane']=='primary']
    result.update(mode='READ_ONLY_NOT_A_TRIGGER',fresh_unattended_cycle_verified=False)
    state=Path(config['state_dir']).expanduser();_atomic_json(state/'scheduled-cycle-observations.json',result)
    return result
