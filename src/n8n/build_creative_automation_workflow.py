"""Opt-in upgrade of Daily Sheet Intake: freeze creative inputs and export Docs.
Existing native Google credential, fixed Google export host, no model credentials.
The local skill runtime consumes READY tasks independently; publication stays off.
"""
import json
from build_sheet_intake_workflow import build_workflow,GOOGLE_CREDENTIAL_ID,GATEWAY_CREDENTIAL_ID,SHEET_ID


def build():
    w=build_workflow();w['name']='Daily Sheet Intake + Skill-based Creative Preparation'
    def add(name,kind,p,version=2,credential=None):
        n={'id':'creative-'+str(len(w['nodes'])),'name':name,'type':kind,'typeVersion':version,'position':[300*len(w['nodes']),100],'parameters':p}
        if credential:n['credentials']=credential
        w['nodes'].append(n);return name
    def edge(a,b):w['connections'][a]={'main':[[{'node':b,'type':'main','index':0}]]}
    def api(name,method,path,body=None):
        p={'method':method,'url':'http://shortform-gateway:8080/v1/automation'+path,'authentication':'genericCredentialType','genericAuthType':'httpHeaderAuth','options':{'timeout':30000}}
        if body is not None:p.update(sendBody=True,specifyBody='json',jsonBody=body)
        return add(name,'n8n-nodes-base.httpRequest',p,4.2,{'httpHeaderAuth':{'id':GATEWAY_CREDENTIAL_ID,'name':'Shortform Gateway API'}})
    prepare=api('Prepare Frozen Creative Tasks','POST','/prepare',"={{ {sheet_id:'"+SHEET_ID+"',dashboard_rows:$('Read community collection history').first().json.values,summary_rows:$('Read daily summary').first().json.values} }}")
    pending=api('Read Pending Creative Documents','GET','/source-tasks')
    split=add('Split Pending Documents','n8n-nodes-base.code',{'mode':'runOnceForAllItems','jsCode':"const ts=$input.first().json.tasks;if(!Array.isArray(ts)||ts.length>12)throw new Error('source_task_list_invalid');return ts.map(t=>{if(!/^[1-9][0-9]*$/.test(t.task_id)||! /^[A-Za-z0-9_-]{10,200}$/.test(t.document_id))throw new Error('source_task_identity_invalid');return {json:t};});"})
    export=add('Export Frozen Creative Document','n8n-nodes-base.httpRequest',{'method':'GET','url':'=https://www.googleapis.com/drive/v3/files/{{ $json.document_id }}/export?mimeType=text%2Fplain','authentication':'predefinedCredentialType','nodeCredentialType':'googleDriveOAuth2Api','options':{'timeout':30000,'response':{'response':{'responseFormat':'text','outputPropertyName':'source_text'}}}},4.2,{'googleDriveOAuth2Api':{'id':GOOGLE_CREDENTIAL_ID,'name':'Google Drive account'}})
    attach=api('Attach Exact Creative Source','POST',"/tasks/{{ $('Split Pending Documents').item.json.task_id }}/source", "={{ {document_id:$('Split Pending Documents').item.json.document_id,text:$json.source_text} }}")
    # n8n expressions require '=' at the start of the entire parameter.
    w['nodes'][-1]['parameters']['url']='='+w['nodes'][-1]['parameters']['url']
    for a,b in [('Submit snapshot to gateway',prepare),(prepare,pending),(pending,split),(split,export),(export,attach)]:edge(a,b)
    w['settings']['executionTimeout']=600
    return w

if __name__=='__main__':print(json.dumps(build(),ensure_ascii=False,indent=2))
