"""Manual-only recovery of an existing user's plan-bound production approval.

Uses native n8n HMAC credentials. No media generation or YouTube permission.
"""
import json
import re


GATEWAY="http://shortform-gateway:8080/v1"
GATEWAY_AUTH={"httpHeaderAuth":{"id":"sf-gateway-api","name":"Shortform Gateway API"}}
SIGNER={"crypto":{"id":"sf-review-hmac","name":"Shortform Review HMAC"}}


def _review_workflow():
    trigger={"id":"trigger","name":"Approval Webhook","type":"n8n-nodes-base.webhook","typeVersion":2,
        "position":[200,300],"parameters":{"httpMethod":"POST","path":"shortform/review-decision-v1",
            "responseMode":"lastNode","options":{"rawBody":True}}}
    nonce={"id":"review-nonce","name":"Generate Review Nonce","type":"n8n-nodes-base.crypto","typeVersion":2,
        "position":[460,200],"parameters":{"action":"generate","dataPropertyName":"review_nonce",
            "encodingType":"hex","stringLength":32}}
    prep_code=r'''const item = $input.first();
const envelope = item?.json ?? {};
const body = envelope.body && typeof envelope.body === 'object' ? envelope.body : envelope;
const rawBody = item?.binary?.data;
const nonce = envelope.review_nonce;
const reviewIdPattern = /^production:[1-9][0-9]*:[0-9a-f]{64}$/;
if (!rawBody) throw new Error('signed_raw_body_missing');
if (typeof body.review_id !== 'string' || !reviewIdPattern.test(body.review_id)
    || !['approve','reject','revise'].includes(body.decision)
    || typeof body.event_id !== 'string' || typeof nonce !== 'string' || !/^[0-9a-f]{32}$/.test(nonce)) {
  throw new Error('invalid_review_decision');
}
const timestamp = String(Math.floor(Date.now()/1000));
const rawBytes = await this.helpers.getBinaryDataBuffer(0,'data');
const rawText = rawBytes.toString('utf8');
if (!Buffer.from(rawText,'utf8').equals(rawBytes)) throw new Error('review_body_not_utf8');
const signingMessage = `v1\n${timestamp}\n${nonce}\n${body.event_id}\n${body.decision}\n${rawText}`;
return [{json:{review_id:body.review_id,decision:body.decision,event_id:body.event_id,
  review_timestamp:timestamp,review_nonce:nonce,signing_message:signingMessage},binary:item.binary}];'''
    prep={"id":"prepare","name":"Validate and Prepare Contract","type":"n8n-nodes-base.code",
        "typeVersion":2,"position":[700,200],"parameters":{"mode":"runOnceForAllItems","jsCode":prep_code}}
    signature={"id":"review-hmac","name":"Sign Review Decision","type":"n8n-nodes-base.crypto",
        "typeVersion":2,"position":[960,200],"parameters":{"action":"hmac","binaryData":False,
            "type":"SHA256","value":"={{ $json.signing_message }}",
            "dataPropertyName":"review_signature","encoding":"hex"},"credentials":SIGNER}
    request={"id":"request","name":"Dispatch Signed Review to Gateway","type":"n8n-nodes-base.httpRequest",
        "typeVersion":4.2,"position":[1220,200],"parameters":{"method":"POST","url":GATEWAY+"/reviews/decision",
            "authentication":"genericCredentialType","genericAuthType":"httpHeaderAuth","sendHeaders":True,
            "headerParameters":{"parameters":[
                {"name":"X-Review-Timestamp","value":"={{ $json.review_timestamp }}"},
                {"name":"X-Review-Nonce","value":"={{ $json.review_nonce }}"},
                {"name":"X-Review-Event-ID","value":"={{ $json.event_id }}"},
                {"name":"X-Review-Signature","value":"={{ 'sha256=' + $json.review_signature }}"},
            ]},"sendBody":True,"contentType":"binaryData","inputDataFieldName":"data",
            "options":{"timeout":30000}},"credentials":GATEWAY_AUTH}
    return {"id":"SF04-ProductionApprovalBase","name":"SF04 - Production Approval Intake",
        "active":False,"nodes":[trigger,nonce,prep,signature,request],
        "connections":{
            "Approval Webhook":{"main":[[{"node":"Generate Review Nonce","type":"main","index":0}]]},
            "Generate Review Nonce":{"main":[[{"node":"Validate and Prepare Contract","type":"main","index":0}]]},
            "Validate and Prepare Contract":{"main":[[{"node":"Sign Review Decision","type":"main","index":0}]]},
            "Sign Review Decision":{"main":[[{"node":"Dispatch Signed Review to Gateway","type":"main","index":0}]]},
        },"settings":{"executionOrder":"v1","saveDataSuccessExecution":"none","saveDataErrorExecution":"none"},
        "pinData":{},"tags":[]}


def build(review_id, event_id):
    match = re.fullmatch(r'production:([1-9][0-9]*):[0-9a-f]{64}', review_id)
    if not match or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{7,127}', event_id):
        raise ValueError('invalid_bound_approval_identity')
    result = _review_workflow()
    trigger = result['nodes'][0]
    old_name = trigger['name']
    trigger.update(name='Manual Existing Approval Recovery', type='n8n-nodes-base.manualTrigger', typeVersion=1, parameters={})
    trigger.pop('credentials', None)
    trigger.pop('webhookId', None)
    result['connections'].pop(old_name)
    body = {'review_id': review_id, 'decision': 'approve', 'event_id': event_id}
    seed = {'id': 'bound-existing-user-consent', 'name': 'Build Exact Approved Request',
            'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [200, 200],
            'parameters': {'mode': 'runOnceForAllItems', 'jsCode':
                'const body='+json.dumps(body)+';const raw=Buffer.from(JSON.stringify(body),"utf8");'
                'return [{json:{body},binary:{data:{data:raw.toString("base64"),mimeType:"application/json",fileName:"approval.json"}}}];'},
            'notes': 'Recovers existing user production consent only. No human QA assertion and no publication authority.'}
    result['nodes'].insert(1, seed)
    def edge(source, target):
        result['connections'][source] = {'main': [[{'node': target, 'type': 'main', 'index': 0}]]}
    edge(trigger['name'], seed['name'])
    edge(seed['name'], 'Generate Review Nonce')
    receipt = {'id': 'bound-approval-receipt', 'name': 'Bound Production Approval Receipt',
               'type': 'n8n-nodes-base.code', 'typeVersion': 2, 'position': [1500, 200],
               'parameters': {'mode': 'runOnceForAllItems', 'jsCode':
                   'const r=$input.first().json;if(r.review_id!=='+json.dumps(review_id)+
                   '||r.state!=="PRODUCTION_APPROVED"||r.production_authorized!==true||r.youtube_upload!==false)'
                   'throw new Error("bound_production_approval_not_accepted");return [{json:r}];'}}
    result['nodes'].append(receipt)
    edge('Dispatch Signed Review to Gateway', receipt['name'])
    result.update(id='SFManualPlanApproval'+match[1], name='SF Manual Authorized Plan Approval '+match[1], active=False)
    result['settings'].update(executionTimeout=60, saveDataSuccessExecution='none', saveDataErrorExecution='none')
    return result
