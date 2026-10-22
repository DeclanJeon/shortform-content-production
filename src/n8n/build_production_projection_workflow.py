"""Generic Sheets upsert + exact readback for current production/publication state."""
import json
from pathlib import Path
from urllib.parse import quote
from build_google_storage_preflight import SHEET_ID
from build_automatic_archive_workflow import GATEWAY,GATEWAY_CREDENTIAL,DRIVE_CREDENTIAL
WORKFLOW_ID='SFAutomaticProductionProjection';TAB='자동제작 원장'


def build():
    ns=[];cs={}
    def node(name,kind,params,version=2):
        ns.append({'id':'production-projection-'+str(len(ns)),'name':name,'type':kind,'typeVersion':version,
                   'position':[(len(ns)%7)*220,(len(ns)//7)*180],'parameters':params});return name
    def edge(a,b,output=0):
        groups=cs.setdefault(a,{'main':[]})['main']
        while len(groups)<=output:groups.append([])
        groups[output].append({'node':b,'type':'main','index':0})
    def code(name,source):return node(name,'n8n-nodes-base.code',{'mode':'runOnceForAllItems','jsCode':source})
    def http(name,method,url,body=None,gateway=False):
        p={'method':method,'url':url,'options':{'timeout':60000}}
        if gateway:p.update(authentication='genericCredentialType',genericAuthType='httpHeaderAuth')
        else:p.update(authentication='predefinedCredentialType',nodeCredentialType='googleDriveOAuth2Api')
        if body is not None:p.update(sendBody=True,specifyBody='json',jsonBody=body)
        out=node(name,'n8n-nodes-base.httpRequest',p,4.2)
        ns[-1]['credentials']={'httpHeaderAuth':GATEWAY_CREDENTIAL} if gateway else {'googleDriveOAuth2Api':DRIVE_CREDENTIAL}
        return out
    def gate(name,expression):
        return node(name,'n8n-nodes-base.if',{'conditions':{'options':{'caseSensitive':True,'typeValidation':'strict','version':2},'conditions':[{'id':name,'leftValue':expression,'rightValue':True,'operator':{'type':'boolean','operation':'true','singleValue':True}}],'combinator':'and'},'options':{}},2.2)
    timer=node('Production Projection Timer','n8n-nodes-base.scheduleTrigger',{'rule':{'interval':[{'field':'cronExpression','expression':'*/5 * * * *'}]}},1.2)
    manual=node('Manual Projection Reconciliation','n8n-nodes-base.manualTrigger',{},1)
    claim=http('Claim Projection','POST',GATEWAY+'/v1/production-projections/claim',gateway=True)
    ready=gate('Projection Ready?','={{ $json.ready === true }}')
    tabs=http('Read Workbook Tabs','GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}?fields=sheets(properties(title))')
    choose=code('Choose Production Ledger',"const xs=$input.first().json.sheets;if(!Array.isArray(xs))throw new Error('workbook_metadata_invalid');return [{json:{exists:xs.some(x=>x.properties?.title==="+json.dumps(TAB,ensure_ascii=False)+")}}];")
    exists=gate('Production Ledger Exists?','={{ $json.exists === true }}')
    create=http('Create Only Production Ledger Tab','POST',f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate',json.dumps({'requests':[{'addSheet':{'properties':{'title':TAB,'gridProperties':{'rowCount':20000,'columnCount':17}}}}]},ensure_ascii=False))
    rows=http('Read Production Ledger Rows','GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/'+quote("'"+TAB+"'!A1:Q20000",safe=''))
    select=code('Choose Stable Story Row',"const c=$('Claim Projection').first().json;const rs=$input.first().json.values??[];if(rs.length>=20000||(rs.length&&JSON.stringify(rs[0])!==JSON.stringify(c.header)))throw new Error('production_ledger_schema');const hits=[];for(let i=1;i<rs.length;i++)if(rs[i][0]===c.story_id)hits.push(i+1);if(hits.length>1)throw new Error('duplicate_story_rows');return [{json:{...c,ledger_row:hits[0]??Math.max(2,rs.length+1)}}];")
    put=http('Upsert Exact Production Snapshot','POST',f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate',"={{ JSON.stringify({valueInputOption:'RAW',data:[{range:\"'"+TAB+"'!A1:Q1\",values:[$json.header]},{range:\"'"+TAB+"'!A\"+$json.ledger_row+':Q'+$json.ledger_row,values:[$json.values]}]}) }}")
    read=http('Read Back Production Snapshot','GET',"=https://sheets.googleapis.com/v4/spreadsheets/"+SHEET_ID+"/values/{{ encodeURIComponent(\"'"+TAB+"'!A\"+$('Choose Stable Story Row').first().json.ledger_row+':Q'+$('Choose Stable Story Row').first().json.ledger_row) }}")
    verify=code('Verify Exact Ledger Readback',"const c=$('Choose Stable Story Row').first().json;if(JSON.stringify($input.first().json.values?.[0])!==JSON.stringify(c.values))throw new Error('production_snapshot_readback_mismatch');return [{json:c}];")
    dashboard=http('Read Existing Community Collection History','GET',f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/'+quote("'커뮤니티 수집 이력'!A1:W2000",safe=''))
    locate=code('Locate Exact Source Status Cell',"const c=$('Choose Stable Story Row').first().json;const rs=$input.first().json.values;if(!Array.isArray(rs)||rs.length>=2000||rs[0]?.[0]!=='수집일'||rs[0]?.[14]!=='중복 키'||rs[0]?.[21]!=='제작 상태'||rs[0]?.[22]!=='사용됨')throw new Error('collection_history_schema');const hits=[];for(let i=1;i<rs.length;i++)if(rs[i][0]===c.source_date&&rs[i][14]===c.source_key)hits.push(i+1);if(hits.length!==1)throw new Error('source_row_not_unique');return [{json:{...c,source_row:hits[0]}}];")
    update=http('Update Source Status and Used Checkbox','POST',f'https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values:batchUpdate',"={{ JSON.stringify({valueInputOption:'RAW',data:[{range:\"'커뮤니티 수집 이력'!V\"+$json.source_row,values:[[$json.source_status]]},{range:\"'커뮤니티 수집 이력'!W\"+$json.source_row,values:[[true]]}]}) }}")
    back=http('Read Back Source Status and Used Checkbox','GET',"=https://sheets.googleapis.com/v4/spreadsheets/"+SHEET_ID+"/values/{{ encodeURIComponent(\"'커뮤니티 수집 이력'!V\"+$('Locate Exact Source Status Cell').first().json.source_row+':W'+$('Locate Exact Source Status Cell').first().json.source_row) }}")
    checked=code('Verify Source Readback',"const c=$('Locate Exact Source Status Cell').first().json;const v=$input.first().json.values?.[0]??[];if(v[0]!==c.source_status||(v[1]!==true&&v[1]!=='TRUE'))throw new Error('source_status_or_used_readback_mismatch');return [{json:c}];")
    ack=http('Acknowledge Verified Projection','POST',"="+GATEWAY+"/v1/production-projections/{{ $json.projection_id }}/ack",'={{ JSON.stringify({claim_id:$json.claim_id,snapshot_sha256:$json.snapshot_sha256,ledger_row:$json.ledger_row,source_row:$json.source_row,sheets_readback_verified:true}) }}',gateway=True)
    edge(timer,claim);edge(manual,claim);edge(claim,ready);edge(ready,tabs);edge(tabs,choose);edge(choose,exists);edge(exists,rows);edge(exists,create,1);edge(create,rows)
    for a,b in zip([rows,select,put,read,verify,dashboard,locate,update,back,checked],[select,put,read,verify,dashboard,locate,update,back,checked,ack]):edge(a,b)
    return {'id':WORKFLOW_ID,'name':'SF - Automatic Production and Publication Sheets Projection','active':False,'nodes':ns,'connections':cs,
        'settings':{'executionOrder':'v1','executionTimeout':300,'timezone':'Asia/Seoul','saveDataSuccessExecution':'none','saveDataErrorExecution':'none'},'pinData':{},'tags':[]}


if __name__=='__main__':
    (Path(__file__).resolve().parent/'workflows'/(WORKFLOW_ID+'.json')).write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n')
