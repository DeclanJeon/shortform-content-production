"""Manual, read-only own-account and native HMAC verification. Never dispatches media."""
from __future__ import annotations

import json
from urllib.parse import quote

from instance import JsonObject

PUBLISH_HEADERS=[
    '발행ID','영상링크','제목','설명','태그','카테고리','공개설정','상태','유튜브ID',
    '발행URL','발행시각','오류','시도횟수','발행키','클레임시각',
]


def build(settings: JsonObject, challenge: str, dashboard_headers: list[str],
          summary_headers: list[str]) -> JsonObject:
    nodes=[{'id':'manual','name':'Read Only Setup Check','type':'n8n-nodes-base.manualTrigger',
        'typeVersion':1,'position':[0,0],'parameters':{}}]
    connections={}
    previous='Read Only Setup Check'
    def add(node: JsonObject) -> None:
        nonlocal previous
        connections[previous]={'main':[[{'node':node['name'],'type':'main','index':0}]]}
        nodes.append(node);previous=node['name']
    google=settings['google_credential_id'];sid=settings['source_sheet_id']
    publish_sid=settings['publish_sheet_id'];folder=settings['archive_folder_id']
    queue=settings['publish_queue_tab']
    for name,url,kind,cid in [
        ('Private Folder',f'https://www.googleapis.com/drive/v3/files/{folder}?fields=id,mimeType,trashed,permissions(type),capabilities(canAddChildren)','googleDriveOAuth2Api',google),
        ('Source Sheet Permission',f'https://www.googleapis.com/drive/v3/files/{sid}?fields=id,mimeType,trashed,capabilities(canEdit)','googleDriveOAuth2Api',google),
        ('Publish Sheet Permission',f'https://www.googleapis.com/drive/v3/files/{publish_sid}?fields=id,mimeType,trashed,capabilities(canEdit)','googleDriveOAuth2Api',google),
        ('Dashboard Header',f'https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/'+quote("'커뮤니티 수집 이력'!A1:W1",safe=''),'googleDriveOAuth2Api',google),
        ('Summary Header',f'https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/'+quote("'일일 요약'!A1:E1",safe=''),'googleDriveOAuth2Api',google),
        ('Publish Queue Header','https://sheets.googleapis.com/v4/spreadsheets/'+publish_sid+'/values/'+quote("'"+queue+"'!A1:O1",safe=''),'googleDriveOAuth2Api',google),
    ]:
        add({'id':name.replace(' ','-'),'name':name,'type':'n8n-nodes-base.httpRequest','typeVersion':4.2,
            'position':[len(nodes)*220,0],'parameters':{'method':'GET','url':url,
                'authentication':'predefinedCredentialType','nodeCredentialType':kind,'options':{'timeout':30000}},
            'credentials':{kind:{'id':cid,'name':'Own setup account'}}})
    assertions={'folder':folder,'sheet':sid,'publish_sheet':publish_sid,'queue':queue,
        'dashboard_headers':dashboard_headers,'summary_headers':summary_headers,
        'publish_headers':PUBLISH_HEADERS,'publish_privacy':settings['publish_privacy'],'challenge':challenge}
    code='''const expected=EXPECTED;
const folder=$('Private Folder').first().json; const sheet=$('Source Sheet Permission').first().json;
const publish=$('Publish Sheet Permission').first().json;
const d=$('Dashboard Header').first().json.values?.[0]; const s=$('Summary Header').first().json.values?.[0];
const q=$('Publish Queue Header').first().json.values?.[0];
if(folder.id!==expected.folder||folder.mimeType!=='application/vnd.google-apps.folder'||folder.trashed===true||folder.capabilities?.canAddChildren!==true||!Array.isArray(folder.permissions)||folder.permissions.some(x=>['anyone','domain'].includes(x.type)))throw new Error('private_writable_folder_not_verified');
if(sheet.id!==expected.sheet||sheet.mimeType!=='application/vnd.google-apps.spreadsheet'||sheet.trashed===true||sheet.capabilities?.canEdit!==true)throw new Error('source_sheet_write_permission_not_verified');
if(publish.id!==expected.publish_sheet||publish.mimeType!=='application/vnd.google-apps.spreadsheet'||publish.trashed===true||publish.capabilities?.canEdit!==true)throw new Error('publish_sheet_write_permission_not_verified');
if(JSON.stringify(d)!==JSON.stringify(expected.dashboard_headers)||JSON.stringify(s)!==JSON.stringify(expected.summary_headers))throw new Error('source_sheet_headers_not_ready');
if(JSON.stringify(q)!==JSON.stringify(expected.publish_headers))throw new Error('publish_queue_header_not_ready');
if(!['공개','비공개','비공개목록'].includes(expected.publish_privacy))throw new Error('publish_privacy_invalid');
return [{json:{status:'OWN_GOOGLE_RESOURCES_AND_QUEUE_VERIFIED',challenge:expected.challenge}}];'''.replace('EXPECTED',json.dumps(assertions,ensure_ascii=False))
    add({'id':'verify','name':'Verify Own Resources','type':'n8n-nodes-base.code','typeVersion':2,
        'position':[1320,0],'parameters':{'mode':'runOnceForAllItems','jsCode':code}})
    add({'id':'hmac','name':'Verify Native Signer','type':'n8n-nodes-base.crypto','typeVersion':2,
        'position':[1540,0],'parameters':{'action':'hmac','binaryData':False,'type':'SHA256',
            'value':'={{ $json.challenge }}','dataPropertyName':'signer_digest','encoding':'hex'},
        'credentials':{'crypto':{'id':'sf-review-hmac','name':'Own review signer'}}})
    return {'id':'SFSetupReadOnlyCheck','name':'Setup - Read-only creator resources and signer check',
        'active':False,'nodes':nodes,'connections':connections,'settings':{
            'executionTimeout':180,'saveDataSuccessExecution':'none','saveDataErrorExecution':'none'}}
