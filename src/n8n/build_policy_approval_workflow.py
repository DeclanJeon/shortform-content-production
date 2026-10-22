"""Scheduled native HMAC signer for exact plans accepted by the originality policy."""
import json
from build_manual_plan_approval import build as manual_approval


def build():
    w=manual_approval('production:1:'+'0'*64,'auto-policy-template-only')
    trigger=w['nodes'][0];old=trigger['name']
    trigger.update(name='Every Five Minutes Policy Approval',type='n8n-nodes-base.scheduleTrigger',typeVersion=1.2,
                   parameters={'rule':{'interval':[{'field':'cronExpression','expression':'*/5 * * * *'}]}})
    w['connections'].pop(old)
    auth=next(n for n in w['nodes'] if n['name']=='Dispatch Signed Review to Gateway')['credentials']
    manual={'id':'manual-policy-check','name':'Manual Policy Approval Check','type':'n8n-nodes-base.manualTrigger','typeVersion':1,'position':[0,200],'parameters':{}}
    request={'id':'next-policy-approved-plan','name':'Read Next Policy Approved Plan','type':'n8n-nodes-base.httpRequest','typeVersion':4.2,'position':[200,0],
             'parameters':{'method':'GET','url':'http://shortform-gateway:8080/v1/automation/next-policy-approval',
                           'authentication':'genericCredentialType','genericAuthType':'httpHeaderAuth','options':{'timeout':30000}},'credentials':auth}
    gate={'id':'policy-approval-ready','name':'Policy Approval Ready?','type':'n8n-nodes-base.if','typeVersion':2.2,'position':[400,0],
          'parameters':{'conditions':{'options':{'caseSensitive':True,'typeValidation':'strict','version':2},'conditions':[
              {'leftValue':'={{ $json.ready === true }}','rightValue':True,'operator':{'type':'boolean','operation':'true','singleValue':True}}],'combinator':'and'},'options':{}}}
    stop={'id':'no-policy-approval','name':'No Policy Approved Plan','type':'n8n-nodes-base.code','typeVersion':2,'position':[600,300],
          'parameters':{'mode':'runOnceForAllItems','jsCode':'return [{json:{status:"NO_POLICY_APPROVAL_READY",reason:$input.first().json.reason??null}}];'}}
    seed=next(n for n in w['nodes'] if n['name']=='Build Exact Approved Request')
    seed['id']='bound-policy-decision'
    seed['notes']='Automated policy evidence, not a human review or per-plan user approval.'
    seed['parameters']['jsCode']='''const r=$input.first().json;const body=r.body;
if(r.ready!==true||r.policy_version!=='independent-fiction-v1'||!body||body.decision!=='approve'||
!/^production:[1-9][0-9]*:[0-9a-f]{64}$/.test(body.review_id)||!/^auto-policy:[1-9][0-9]*:[0-9a-f]{32}$/.test(body.event_id))throw new Error('invalid_policy_approval_target');
const raw=Buffer.from(JSON.stringify(body),'utf8');return [{json:{body},binary:{data:{data:raw.toString('base64'),mimeType:'application/json',fileName:'policy-approval.json'}}}];'''
    receipt=next(n for n in w['nodes'] if n['name']=='Bound Production Approval Receipt')
    receipt['parameters']['jsCode']="const r=$input.first().json;const b=$('Build Exact Approved Request').first().json.body;if(r.review_id!==b.review_id||r.state!=='PRODUCTION_APPROVED'||r.production_authorized!==true||r.youtube_upload!==false)throw new Error('policy_production_approval_not_accepted');return [{json:r}];"
    w['nodes'].extend([manual,request,gate,stop])
    def edge(a,b,output=0):
        groups=w['connections'].setdefault(a,{'main':[]})['main']
        while len(groups)<=output:groups.append([])
        groups[output].append({'node':b,'type':'main','index':0})
    for name in (trigger['name'],manual['name']):edge(name,request['name'])
    edge(request['name'],gate['name']);edge(gate['name'],seed['name']);edge(gate['name'],stop['name'],1)
    w.update(id='SFAutomaticPolicyApproval',name='SF Automatic Policy-bound Production Approval',active=False)
    w['settings'].update(timezone='Asia/Seoul',executionTimeout=120)
    return w


if __name__=='__main__':print(json.dumps(build(),ensure_ascii=False,indent=2))
