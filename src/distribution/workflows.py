"""Render inactive canonical workflows for the installer's own account settings."""
import argparse
import importlib
import json
from pathlib import Path
import sys
sys.dont_write_bytecode=True


def render(source,c):
    sys.dont_write_bytecode=True
    required=('source_sheet_id','publish_sheet_id','publish_queue_tab','publish_privacy',
              'publish_category_id','archive_folder_id','google_credential_id')
    if any(not c.get(k) for k in required):raise ValueError('account_mapping_required')
    sys.path.insert(0,str(Path(source)/'n8n'))
    sheet=importlib.import_module('build_sheet_intake_workflow')
    sheet.SHEET_ID=c['source_sheet_id'];sheet.GOOGLE_CREDENTIAL_ID=c['google_credential_id'];sheet.GATEWAY_CREDENTIAL_ID='sf-gateway-api'
    archive=importlib.import_module('build_automatic_archive_workflow')
    archive.GATEWAY_CREDENTIAL={'id':'sf-gateway-api','name':'Shortform Gateway API'}
    archive.DRIVE_CREDENTIAL={'id':c['google_credential_id'],'name':'Google Drive account'}
    creative=importlib.import_module('build_creative_automation_workflow')
    creative.SHEET_ID=c['source_sheet_id'];creative.GOOGLE_CREDENTIAL_ID=c['google_credential_id'];creative.GATEWAY_CREDENTIAL_ID='sf-gateway-api'
    projection=importlib.import_module('build_production_projection_workflow')
    projection.SHEET_ID=c['source_sheet_id'];projection.DRIVE_CREDENTIAL=archive.DRIVE_CREDENTIAL;projection.GATEWAY_CREDENTIAL=archive.GATEWAY_CREDENTIAL
    handoff=importlib.import_module('build_publish_queue_handoff_workflow')
    handoff.PUBLISH_SHEET_ID=c['publish_sheet_id']
    handoff.QUEUE_TAB=c['publish_queue_tab']
    handoff.PUBLISH_PRIVACY=c['publish_privacy']
    handoff.CATEGORY_ID=str(c['publish_category_id'])
    handoff.GOOGLE_CREDENTIAL=archive.DRIVE_CREDENTIAL
    handoff.GATEWAY_CREDENTIAL=archive.GATEWAY_CREDENTIAL
    handoff.PUBLISH_HEADERS=importlib.import_module('preflight_workflow').PUBLISH_HEADERS
    values=[creative.build(),
            archive.build(),importlib.import_module('build_project_archive_workflow').get_workflow(),
            importlib.import_module('build_policy_approval_workflow').build(),
            projection.build(),handoff.build()]
    for w in values:
        w['active']=False
        w['settings'].update(saveDataSuccessExecution='none',saveDataErrorExecution='none')
        if 'CONFIGURE_' in json.dumps(w) or 'CONFIGURE-' in json.dumps(w):raise ValueError('unbound_workflow_reference')
        for node in w['nodes']:
            credentials=node.get('credentials') or {}
            parameters=json.dumps(node.get('parameters',{})).lower()
            if (node.get('type')=='n8n-nodes-base.youTube'
                    or 'youTubeOAuth2Api' in credentials
                    or 'youtube/v3' in parameters
                    or 'youtube.com/upload' in parameters
                    or '/manual-releases/' in parameters):
                raise ValueError('publisher_workflow_forbidden_in_creator_project')
    return values


def main():
    from installer import settings,dotenv,atomic
    p=argparse.ArgumentParser();p.add_argument('--target',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    a=p.parse_args();target=a.target.resolve(strict=True);c=settings(a.config)
    state=json.loads((target/'install-state.json').read_text())
    if c['project']!=state['settings']['project'] or c['n8n_port']!=state['settings']['n8n_port']:raise ValueError('instance_identity_change_forbidden')
    values=render(target/'source',c)
    # Render before modifying any instance configuration. Never activate workflows here.
    for w in values:atomic(target/'workflows'/(w['id']+'.json'),json.dumps(w,ensure_ascii=False,indent=2)+'\n',mode=0o644)
    env=dotenv(target/'.env');env.update(SOURCE_SHEET_ID=c['source_sheet_id'],
        PUBLISH_SHEET_ID=c['publish_sheet_id'],PUBLISH_QUEUE_TAB=c['publish_queue_tab'],
        PUBLISH_PRIVACY=c['publish_privacy'],PUBLISH_CATEGORY_ID=str(c['publish_category_id']),
        AUTOMATIC_ARCHIVE_FOLDER_ID=c['archive_folder_id'],TYPECAST_VOICE_ID=c['typecast_voice_id'])
    atomic(target/'.env',''.join(k+'='+v+'\n' for k,v in env.items()))
    state['settings']=c;state['workflow_state']='RENDERED_INACTIVE_NOT_IMPORTED'
    atomic(target/'install-state.json',json.dumps(state,indent=2)+'\n')
    print(json.dumps({'state':state['workflow_state'],'workflows':len(values),'automation_enabled':False}))


if __name__=='__main__':main()
