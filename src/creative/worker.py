"""Single-writer local creative coordinator, polled durably from private gateway.

Pi uses existing local runtime OAuth with tools disabled. Google OAuth remains
in n8n. This service does not publish or turn a pending media handoff into PASS.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from .contract import canonical_sha256,validate_plan
from .skills import load_bundle
from .pi_runtime import generate_plan,_atomic_json
from .production import materialize_plan

SID=re.compile(r'[0-9]{8}-auto-[0-9a-f]{12}\Z')


def _record_final_frame_qa(result, frames, config):
    """Persist a machine-verifiable QA-05 record from sampled final frames."""
    checks=result.get('qa_checks')
    matches=[check for check in checks if check.get('check_id')=='QA-05'] if isinstance(checks,list) else []
    if len(matches)!=1 or not frames:raise RuntimeError('final_render_qa_evidence_missing')
    frame_hashes=[hashlib.sha256(path.read_bytes()).hexdigest() for path in frames]
    proof={'decision':'PASS','source_sha256':result['sha256'],'frame_sha256s':frame_hashes}
    evidence_sha='sha256:'+hashlib.sha256(json.dumps(proof,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    check=matches[0]
    check['status']='PASS'
    check['evidence_artifact_ids']=list(dict.fromkeys([*check.get('evidence_artifact_ids',[]),evidence_sha]))
    check['checker_version']=f"{config.get('provider','openai-codex')}:{config.get('vision_model',config.get('model','gpt-6-luna'))}:sampled-final-frames-v1"
    result['final_frame_review_evidence']=proof
    result['final_render_visual_review']='AI_SAMPLED_FRAMES_PASS'


class Gateway:
    def __init__(self,host,gateway_container='shortform-gateway',worker_container='shortform-worker'):
        if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}',n) for n in (gateway_container,worker_container)):raise ValueError('invalid_container_name')
        self.gateway_container=gateway_container;self.worker_container=worker_container
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}',host):raise ValueError('invalid_ssh_alias')
        self.host=host
    def command(self,container,script):
        prefix=[] if self.host=='local' else ['ssh','-T','-o','BatchMode=yes','-o','ConnectTimeout=10',self.host]
        return prefix+['docker','exec','-i',container,'python','/app/'+script]
    def call(self,path,body=None):
        packet={'method':'GET' if body is None else 'POST','path':path,'body':body}
        p=subprocess.run(self.command(self.gateway_container,'creative_bridge.py'),input=json.dumps(packet,ensure_ascii=False),text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=60,check=False)
        limit=48*1024*1024 if path.endswith('/artifact') else 512*1024
        if p.returncode or len(p.stdout)>limit:raise RuntimeError('gateway_transport_unavailable')
        try:r=json.loads(p.stdout)
        except ValueError:raise RuntimeError('gateway_transport_unavailable') from None
        if r.get('http_status') not in (200,202):raise RuntimeError('gateway_request_failed:'+str(r.get('http_status')))
        return r['body']
    def worker_rpc(self,module,body):
        if module not in ('stage_assets.py','render_frames.py','stage_project_bundle.py'):raise ValueError('invalid_worker_rpc')
        p=subprocess.run(self.command(self.worker_container,module),input=json.dumps(body),text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=240 if module=='stage_project_bundle.py' else 120)
        if p.returncode or len(p.stdout)>12*1024*1024:raise RuntimeError('worker_rpc_failed')
        r=json.loads(p.stdout)
        if r.get('error'):raise RuntimeError('worker_rpc_failed')
        return r
    def stage_image(self,story_id,asset_id,path):
        raw=Path(path).read_bytes();sha=hashlib.sha256(raw).hexdigest()
        if len(raw)>8*1024*1024:raise ValueError('staged_image_too_large')
        result=self.worker_rpc('stage_assets.py',{'story_id':story_id,'asset_id':asset_id,'sha256':sha,'png_base64':base64.b64encode(raw).decode()})
        if result.get('sha256')!=sha or result.get('artifact_path')!='/data/artifacts/auto-staged/'+story_id+'/'+asset_id+'.png':raise RuntimeError('staged_image_receipt_invalid')
        return result
    def render_frames(self,result,root):
        r=self.worker_rpc('render_frames.py',{'job_id':result['render_job_id'],'sha256':result['sha256'],'duration_seconds':result['duration_seconds']})
        if r.get('source_sha256')!=result['sha256'] or len(r.get('frames',[]))!=3:raise RuntimeError('render_frame_receipt_invalid')
        paths=[]
        for i,frame in enumerate(r['frames'],1):
            raw=base64.b64decode(frame['png_base64'],validate=True)
            if hashlib.sha256(raw).hexdigest()!=frame['sha256']:raise RuntimeError('render_frame_hash_invalid')
            path=root/'09_QA'/f'render-frame-{i:02}.png'
            if path.exists() and path.read_bytes()!=raw:raise RuntimeError('render_frame_cache_conflict')
            path.write_bytes(raw);paths.append(path)
        return paths


class Coordinator:
    def __init__(self,config,gateway=None):
        self.config=config;self.gateway=gateway or Gateway(config['ssh_alias'],config.get('gateway_container','shortform-gateway'),config.get('worker_container','shortform-worker'))
        self.base=Path(config['production_root']).expanduser().resolve();self.state=Path(config['state_dir']).expanduser().resolve()
        self.state.mkdir(parents=True,exist_ok=True,mode=0o700);os.chmod(self.state,0o700)
        self.pending=self.state/'pending-task.json'
        self.pending_media=self.state/'pending-media.json'
        self.history=Path(config['skill_root'])/'contents-production-skills/recording-production-history/scripts/production_history.py'
    def history_call(self,root,*args):
        command=[sys.executable,str(self.history),*args,'--project-root',str(root)]
        p=subprocess.run(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=30)
        if p.returncode:raise RuntimeError('production_history_failed')
        return json.loads(p.stdout)
    def event(self,root,state,outputs=(),version=1):
        brief='auto-creative-brief.txt' if version==1 else f'auto-creative-brief-v{version}.txt'
        args=['record','--operation','Automatic skill-based original creative plan','--operation-id',f'auto-creative-plan-v{version}','--prompt-file',str(root/'.history/prompts'/brief),'--status',state,'--tool','pi-no-tools','--model',self.config['model'],'--method','Pinned skills/source; canonical selected pitch; structural validation; bounded local correction and separate AI review/adjudication; at most two eligible pitches; no media/publication']
        for output in outputs:args.extend(['--output',str(output)])
        return self.history_call(root,*args)
    def operation_status(self,root,version=1):
        d=self.history_call(root,'status');ops=d.get('operations',{})
        op=ops.get(f'auto-creative-plan-v{version}',{})
        return op.get('state')
    def model_ready(self):
        p=subprocess.run(['pi','auth','check','--provider',self.config['provider'],'--model',self.config['model'],'--json'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=20)
        try:return p.returncode==0 and json.loads(p.stdout).get('status')=='ready'
        except ValueError:return False
    def once(self):
        if self.config.get('runtime_enabled',True) is not True:return {'status':'DISABLED'}
        if self.config.get('observe_cycles') and time.monotonic()-getattr(self,'_observer_last',0)>300:
            from .cycle_observer import observe
            try:observe(self.config,self.gateway)
            except (OSError,RuntimeError,ValueError,subprocess.SubprocessError):print(json.dumps({'status':'CYCLE_OBSERVATION_UNAVAILABLE'}),flush=True)
            self._observer_last=time.monotonic()
        if self.pending_media.exists():return self.process_media(json.loads(self.pending_media.read_text()))
        if self.pending.exists():task=json.loads(self.pending.read_text())
        else:
            if not self.model_ready():return {'status':'WAITING_FOR_MODEL_AUTH'}
            media_wait=None
            if self.config.get('auto_media_enabled'):
                script=self.config.get('codex_imagen_script')
                ready=subprocess.run(['node',script,'--smoke'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=30).returncode==0 if script else False
                media_wait={'status':'WAITING_FOR_IMAGE_AUTH'}
                if ready:
                    media_response=self.gateway.call('/v1/automation/claim-media',{})
                    media=media_response.get('task')
                    if media:
                        _atomic_json(self.pending_media,media)
                        return self.process_media(media)
                    media_wait=({'status':'WAITING_FOR_MEDIA_ADMISSION','reason':media_response['reason'],
                        **({'next_eligible_at':media_response['next_eligible_at']} if media_response.get('next_eligible_at') else {})} if media_response.get('reason') else None)
            if self.config.get('auto_retention_enabled') and time.monotonic()-getattr(self,'_retention_last',0)>300:
                self._retention_last=time.monotonic()
                from .housekeeping import once as retention_once
                retention=retention_once(self.config,self.gateway)
                if retention.get('status')=='CLEANED':return retention
            if self.config.get('auto_policy_enabled'):
                from .policy_worker import once as policy_once
                with open(self.state/'policy-worker.lock','a') as policy_lock:
                    try:fcntl.flock(policy_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:return {'status':'POLICY_WORKER_BUSY'}
                    policy=policy_once(self.config,self.gateway)
                    if policy.get('status')!='NO_POLICY_TASKS':return policy
            task=self.gateway.call('/v1/automation/claim',{}).get('task')
            if not task:
                if self.config.get('auto_buffer_enabled'):
                    promoted=self.gateway.call('/v1/automation/promote-buffer',{})
                    if promoted.get('promoted'):return {'status':'PLANNING_BUFFER_PROMOTED',**promoted}
                return media_wait or {'status':'IDLE'}
            _atomic_json(self.pending,task)
        return self.process(task)
    def process(self,task):
        from .plan_revision import plan_version
        version=plan_version(task)
        if not SID.fullmatch(task.get('story_id','')) or not str(task.get('task_id','')).isdigit():raise ValueError('invalid_task_identity')
        root=self.base/task['story_id'];root.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        if root.is_symlink():raise ValueError('unsafe_project_root')
        current=self.gateway.call('/v1/automation/tasks/'+task['task_id'])
        if current.get('claim_id')!=task['claim_id']:raise RuntimeError('creative_claim_conflict')
        if current['state']=='NEEDS_REVIEW':
            self.pending.unlink(missing_ok=True);return {'status':'NEEDS_REVIEW','task_id':task['task_id']}
        self.history_call(root,'init','--project-id',task['story_id']);os.chmod(root,0o700)
        brief='auto-creative-brief.txt' if version==1 else f'auto-creative-brief-v{version}.txt'
        prompt=root/'.history/prompts'/brief
        if not prompt.exists():prompt.write_text('User authorized automatic skill-based content construction and downstream workflow. One Korean original narrated still-image Short, 0<duration<=180 seconds, no publication. Selection follows accepted sheet rank, not independently measured demand.\n'+json.dumps(task,ensure_ascii=False,indent=2)+'\n')
        operation=self.operation_status(root,version)
        if operation!='started' and current['state']!='PLAN_READY':self.event(root,'started',version=version)
        try:
            bundle=load_bundle(self.config['skill_root'],self.config['repository_root'])
            if bundle['revision']!=task['skill_revision']:raise ValueError('skill_bundle_changed')
            checkpoint=root/f'00_MANIFEST/creative-result-v{version}.json'
            work=root/'00_MANIFEST/.creative-runtime'
            if version>1:work=work/f'revision-{version}'
            if checkpoint.exists():result=json.loads(checkpoint.read_text());validate_plan(result['plan'])
            else:
                result=generate_plan(task,bundle,work,provider=self.config['provider'],model=self.config['model'],timeout_seconds=self.config.get('model_timeout_seconds',240),review_model=self.config.get('planning_reviewer_model',self.config.get('vision_model',self.config['model'])))
                _atomic_json(checkpoint,result)
            outputs=materialize_plan(result['plan'],root,version=version)
            pitches=work/'pitches-and-selection-v1.json'
            if pitches.is_file():
                destination=root/f'02_STORY/pitches-and-selection-v{version}.json'
                destination.parent.mkdir(parents=True,exist_ok=True)
                _atomic_json(destination,json.loads(pitches.read_text()));outputs.append(f'02_STORY/pitches-and-selection-v{version}.json')
            receipt=self.gateway.call('/v1/automation/tasks/'+task['task_id']+'/complete-plan',{'claim_id':task['claim_id'],**result})
            if receipt.get('state')!='PLAN_READY' or receipt.get('plan_sha256')!=canonical_sha256(result['plan']):raise RuntimeError('creative_completion_receipt_invalid')
            _atomic_json(root/f'00_MANIFEST/creative-plan-receipt-v{version}.json',receipt)
            project_path=root/'project.json';project=json.loads(project_path.read_text())
            project.pop('error_code',None)
            if result['plan'].get('schema_version')=='3.0':
                from .storyboard_project import project_from_plan
                specs=json.loads((root/f'00_MANIFEST/asset-specs-v{version}.json').read_text())
                project=project_from_plan(project,result['plan'],specs,root,version)
                project.update(status='CREATIVE_PLAN_READY',story_id=task['story_id'],run_id=task['run_id'],title=result['plan']['title'],source_key=task['source_key'],creative_task_id=task['task_id'],creative_plan_path=f'00_MANIFEST/creative-plan-v{version}.json',plan_version=version,skill_revision=bundle['revision'],publication={'authorized':False,'youtube_upload':False},review={'structural_validation':'PASS','ai_content_review':'PASS','human_review':'NOT_PERFORMED'})
            else:
                project.update(status='CREATIVE_PLAN_READY',story_id=task['story_id'],run_id=task['run_id'],title=result['plan']['title'],source_key=task['source_key'],creative_task_id=task['task_id'],creative_plan_path=f'00_MANIFEST/creative-plan-v{version}.json',plan_version=version,skill_revision=bundle['revision'],target_duration_s=result['plan']['estimated_duration_seconds'],fps=30,aspect_ratio='9:16',publication={'authorized':False,'youtube_upload':False},review={'structural_validation':'PASS','ai_content_review':'PASS','human_review':'NOT_PERFORMED'},characters=result['plan']['characters'],shots=result['plan']['shots'])
            registry=project.setdefault('asset_registry',[])
            for relative in outputs:
                file_path=root/relative
                if not any(a.get('path')==relative for a in registry):
                    registry.append({'id':'TEXT-'+canonical_sha256(relative)[:12],'kind':'creative_text',
                        'version':f'v{version}','status':'available','path':relative,
                        'sha256':__import__('hashlib').sha256(file_path.read_bytes()).hexdigest(),
                        'size_bytes':file_path.stat().st_size,'authority':'derived'})
            _atomic_json(project_path,project)
            if self.operation_status(root,version)=='started':self.event(root,'completed',[*outputs,f'00_MANIFEST/creative-result-v{version}.json',f'00_MANIFEST/creative-plan-receipt-v{version}.json'],version=version)
            self.pending.unlink(missing_ok=True)
            return {'status':'CREATIVE_PLAN_READY','task_id':task['task_id'],'project_root':str(root),'title':result['plan']['title'],'youtube_upload':False}
        except Exception as exc:
            code=str(exc)
            if code.startswith('gateway_') or code=='creative_completion_receipt_invalid':raise  # Retry acknowledgement only; successful model calls stay cached.
            if code not in {'skill_bundle_changed','creative_plan_review_failed','planning_candidates_exhausted','planning_recovery_conflict','planning_review_invalid','model_outcome_uncertain'}:code='model_outcome_uncertain' if 'model_' in code or 'prompt_hash' in code else 'local_runtime_failed'
            self.gateway.call('/v1/automation/tasks/'+task['task_id']+'/fail',{'claim_id':task['claim_id'],'code':code})
            if self.operation_status(root,version)=='started':self.event(root,'failed',version=version)
            project_path=root/'project.json';project=json.loads(project_path.read_text());project.update(status='CREATIVE_NEEDS_REVIEW',error_code=code);_atomic_json(project_path,project)
            self.pending.unlink(missing_ok=True)
            return {'status':'NEEDS_REVIEW','task_id':task['task_id'],'error_code':code,'project_root':str(root)}

    def media_event(self,root,operation,prompt,status,outputs=(),details=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',operation):raise ValueError('invalid_media_operation')
        operation=operation.casefold()  # production_history canonicalizes IDs this way.
        source=Path(prompt) if isinstance(prompt,str) and '\n' not in prompt and len(prompt)<4000 else None
        if source is None or not source.is_file():
            source=root/'.history/prompts'/f'{operation}-v1.txt';source.write_text(str(prompt))
        if not source.resolve().is_relative_to(root):raise ValueError('media_prompt_path_escape')
        statuses=self.history_call(root,'status').get('operations',{});old=statuses.get(operation,{}).get('state')
        if status=='started' and old=='started':return
        if status=='completed' and old=='completed':return
        args=['record','--operation',operation,'--operation-id',operation,'--prompt-file',str(source),'--tool','pi-vision' if operation.startswith('review') else 'codex-imagen','--method','Bounded private still-image production; actual prompt/reference hashes; no paid retry; known-output reconciliation only on resume','--details',json.dumps(details or {},ensure_ascii=False)]
        if status!='started' and old!='started':self.history_call(root,*args,'--status','started')
        for output in outputs:args.extend(['--output',str(output)])
        self.history_call(root,*args,'--status',status)
        print(json.dumps({'status':'MEDIA_PROGRESS','story_id':root.name,'operation':operation,'phase':status}),flush=True)
    def process_media(self,task):
        from .media_runtime import produce,_default_review
        if not SID.fullmatch(task.get('story_id','')) or not str(task.get('media_intent_id','')).isdigit():raise ValueError('invalid_media_task')
        root=self.base/task['story_id'];current=self.gateway.call('/v1/automation/media-tasks/'+task['media_intent_id'])
        if current.get('media_claim_id')!=task['media_claim_id']:raise RuntimeError('media_claim_conflict')
        if current['state']=='NEEDS_REVIEW':self.pending_media.unlink(missing_ok=True);return {'status':'MEDIA_NEEDS_REVIEW','story_id':task['story_id']}
        project_path=root/'project.json';project=json.loads(project_path.read_text())
        prompt=root/'.history/prompts/auto-media-brief-v1.txt'
        if not prompt.exists():prompt.write_text('User authorized automatic workflow using the validated creative plan. Private still-image video only, no publication. Typecast budget/duration verified before images. Actual reference-image and sampled final-frame reviews are AI-only, not human approval.\n'+json.dumps(task,ensure_ascii=False))
        args=['record','--operation','Automatic private media production from validated plan','--operation-id','auto-private-media-v1','--prompt-file',str(prompt),'--tool','codex-imagen+Typecast+FFmpeg','--method','One generation per asset; max2parallel independent scenes; cue-aligned timing; actual AI image/frame reviews; no YouTube']
        operation=self.history_call(root,'status').get('operations',{}).get('auto-private-media-v1',{}).get('state')
        if operation!='started' and current['state']!='PRIVATE_VIDEO_READY':self.history_call(root,*args,'--status','started')
        project.pop('error_code',None)
        project.update(status='MEDIA_RUNNING');_atomic_json(project_path,project)
        result_path=root/'10_DELIVERY/automatic-media-result-v1.json'
        try:
            media_config=self.config
            reconciliation=task.get('operator_reconciliation',{})
            retry_groups=reconciliation.get('review_retry_groups',[reconciliation.get('review_retry_group','')])
            if (reconciliation.get('reason') in ('review_transport_reconciliation','review_execution_reconciliation','deterministic_lettering_repair','review_model_adjudication') and isinstance(retry_groups,list)
                    and retry_groups and all(isinstance(g,str) and re.fullmatch(r'SH[0-9]{2}',g) for g in retry_groups)):
                media_config={**self.config,'_review_retry_groups':retry_groups}
                # Frozen successful legacy reviews remain attributed to Luna.
                media_config['vision_model']='gpt-6-luna'
                adjudication=reconciliation.get('review_adjudication_groups',[])
                if isinstance(adjudication,list) and adjudication and all(isinstance(g,str) and re.fullmatch(r'SH[0-9]{2}',g) for g in adjudication):
                    media_config['_review_adjudication_groups']=adjudication
                groups=reconciliation.get('review_execution_groups',[])
                if isinstance(groups,list) and groups and all(isinstance(g,str) and re.fullmatch(r'SH[0-9]{2}',g) for g in groups):
                    media_config={**media_config,'_review_execution_groups':groups}
            result=json.loads(result_path.read_text()) if result_path.exists() else produce(root,task,task['plan'],media_config,self.gateway,lambda *a,**kw:self.media_event(root,*a,**kw))
            if result.get('final_render_visual_review')!='AI_SAMPLED_FRAMES_PASS':
                frames=self.gateway.render_frames(result,root)
                frame_prompt='Inspect actual beginning, middle, and ending frames of this rendered Korean Short. Check readable Korean captions, compact channel/title, no broken glyphs/overlaps, and no severe camera-motion defects, clipping, flicker, or freeze visible in the samples. Return only JSON {"decision":"PASS" or "REVISE","issues":[]}. This is sampled-frame AI review, not whole-video playback or human listening.\n'+json.dumps({'title':task['plan']['title'],'source_sha256':result['sha256'],'frames':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in frames]},ensure_ascii=False)
                frame_source=root/'.history/prompts/review-final-frames-v1.txt';frame_source.write_text(frame_prompt)
                self.media_event(root,'review-final-frames',str(frame_source),'started')
                verdict=_default_review(root/'00_MANIFEST/.media-runtime','final-render-frames',frame_prompt,frames,self.config)
                if verdict!={'decision':'PASS','issues':[]}:raise RuntimeError('visual_review_rejected')
                _record_final_frame_qa(result,frames,self.config)
                self.media_event(root,'review-final-frames',str(frame_source),'completed',outputs=frames)
                _atomic_json(result_path,result)
            qa_checks=result.get('qa_checks')
            qa05=[check for check in qa_checks if check.get('check_id')=='QA-05'] if isinstance(qa_checks,list) else []
            if len(qa05)!=1 or qa05[0].get('status')!='PASS' or result.get('final_render_visual_review')!='AI_SAMPLED_FRAMES_PASS':
                raise RuntimeError('final_render_qa_evidence_missing')
            ack=self.gateway.call('/v1/automation/media-tasks/'+task['media_intent_id']+'/complete',{'media_claim_id':task['media_claim_id'],'result':result})
            if ack.get('state')!='PRIVATE_VIDEO_READY':raise RuntimeError('gateway_media_receipt_invalid')
            authoritative=ack.get('result')
            if isinstance(authoritative,dict):
                if any(authoritative.get(k)!=result.get(k) for k in ('sha256','render_job_id','tts_job_id')):
                    raise RuntimeError('gateway_media_receipt_invalid')
                result=authoritative;_atomic_json(result_path,result)
            _atomic_json(root/'10_DELIVERY/automatic-media-receipt-v1.json',ack)
            project=json.loads(project_path.read_text());project.update(status='PRIVATE_VIDEO_READY',final_video_path=result['video_relative_path'],media_result=result,publication={'authorized':False,'youtube_upload':False})
            selected=root/'00_MANIFEST/asset-specs-selected-v1.json'
            from .plan_revision import plan_version
            version=plan_version(task)
            specs=json.loads((selected if selected.exists() else root/f'00_MANIFEST/asset-specs-v{version}.json').read_text());registry=project.setdefault('asset_registry',[])
            media_assets=[*specs,{'asset_id':f'FINAL-VIDEO-v{version}','kind':'final_video','relative_path':result['video_relative_path']}]
            for item in media_assets:
                relative=item['relative_path'];path=root/relative
                asset_version=f"v{item.get('revision',1)}" if item in specs else f'v{version}'
                row={'id':item['asset_id'],'kind':item['kind'],'version':asset_version,
                     'status':'available','path':relative,
                     'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                     'size_bytes':path.stat().st_size,'authority':'derived'}
                if item.get('reference_ids'):row['source_asset_ids']=list(item['reference_ids'])
                if item['kind']=='character_image':
                    row.update(entity_type='character',entity_id=item['asset_id'])
                existing=next((i for i,a in enumerate(registry) if a.get('id')==row['id']),None)
                if existing is None:registry.append(row)
                else:registry[existing]=row
            _atomic_json(project_path,project)
            if self.history_call(root,'status').get('operations',{}).get('auto-private-media-v1',{}).get('state')=='started':self.history_call(root,*args,'--status','completed','--output',result['video_relative_path'],'--output','10_DELIVERY/automatic-media-result-v1.json','--output','10_DELIVERY/automatic-media-receipt-v1.json')
            from .project_archive import archive_project
            archive_project(root,task,result,self.gateway,skill_root=self.config['skill_root'])
            self.pending_media.unlink(missing_ok=True)
            return {'status':'PRIVATE_VIDEO_READY','project_root':str(root),'title':task['plan']['title'],'youtube_upload':False}
        except Exception as exc:
            error=str(exc)
            _atomic_json(root/'09_QA/automatic-media-failure-v1.json',{
                'exception_type':type(exc).__name__,
                'machine_code':error if re.fullmatch(r'[a-z_]+(?::[0-9]+)?',error) else 'unclassified_runtime_error'})
            if error.startswith('gateway_') or error in ('tts_pending','render_pending','worker_rpc_failed'):raise
            code='visual_review_rejected' if error=='visual_review_rejected' else 'media_outcome_uncertain' if any(s in error for s in ('uncertain','timeout','model_call')) else 'media_runtime_failed'
            self.gateway.call('/v1/automation/media-tasks/'+task['media_intent_id']+'/fail',{'media_claim_id':task['media_claim_id'],'code':code})
            for op,data in self.history_call(root,'status').get('operations',{}).items():
                if data.get('state')=='started':self.history_call(root,'resume','--operation-id',op,'--details','Media pipeline stopped; provider checkpoints retained, no blind resubmission')
            project=json.loads(project_path.read_text());project.update(status='MEDIA_NEEDS_REVIEW',error_code=code);_atomic_json(project_path,project)
            self.pending_media.unlink(missing_ok=True)
            return {'status':'MEDIA_NEEDS_REVIEW','error_code':code,'project_root':str(root)}


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--once',action='store_true');a=p.parse_args();config=json.loads(a.config.read_text());worker=Coordinator(config)
    lock=open(worker.state/'worker.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('creative_worker_already_running')
    while True:
        try:result=worker.once()
        except Exception as exc:
            code=str(exc)
            result={'status':'RUNTIME_UNAVAILABLE','error_code':code if re.fullmatch(r'[a-z_]{1,80}(?::[0-9]{3})?',code) else 'runtime_error'}
        print(json.dumps(result,ensure_ascii=False),flush=True)
        if a.once:break
        time.sleep(max(10,int(config.get('poll_seconds',30))))

if __name__=='__main__':main()
