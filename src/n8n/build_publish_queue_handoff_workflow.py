"""Build the creator-to-publisher Sheets handoff; this workflow never uploads."""
import json
from urllib.parse import quote

WORKFLOW_ID = "SFAutomaticPublishQueueHandoff"
GATEWAY = "http://shortform-gateway:8080"
PUBLISH_SHEET_ID = ""
QUEUE_TAB = "발행 대기"
PUBLISH_PRIVACY = "공개"
CATEGORY_ID = "22"
GOOGLE_CREDENTIAL = {"id": "CONFIGURE_GOOGLE_CREDENTIAL_ID", "name": "Google Drive account"}
GATEWAY_CREDENTIAL = {"id": "sf-gateway-api", "name": "Shortform Gateway API"}
PUBLISH_HEADERS = [
    "발행ID", "영상링크", "제목", "설명", "태그", "카테고리", "공개설정", "상태",
    "유튜브ID", "발행URL", "발행시각", "오류", "시도횟수", "발행키", "클레임시각",
]

READ_OPTIONS = {"timeout": 30000}


def _node(name, kind, parameters, version=2):
    return {
        "id": f"handoff-{name.lower().replace(' ', '-')}",
        "name": name,
        "type": kind,
        "typeVersion": version,
        "position": [220 * len(_NODES), 120],
        "parameters": parameters,
    }


_NODES = []


def _add(name, kind, parameters, version=2, credentials=None):
    item = _node(name, kind, parameters, version)
    if credentials:
        item["credentials"] = credentials
    _NODES.append(item)
    return name


def _http(name, method, url, *, body=None, google=False, version=4.2):
    parameters = {"method": method, "url": url, "options": dict(READ_OPTIONS)}
    credential = None
    if google:
        parameters.update(
            authentication="predefinedCredentialType",
            nodeCredentialType="googleDriveOAuth2Api",
        )
        credential = {"googleDriveOAuth2Api": GOOGLE_CREDENTIAL}
    else:
        parameters.update(
            authentication="genericCredentialType",
            genericAuthType="httpHeaderAuth",
        )
        credential = {"httpHeaderAuth": GATEWAY_CREDENTIAL}
    if body is not None:
        parameters.update(sendBody=True, specifyBody="json", jsonBody=body)
    return _add(name, "n8n-nodes-base.httpRequest", parameters, version, credential)


def _if(name, expression):
    return _add(name, "n8n-nodes-base.if", {
        "conditions": {
            "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
            "conditions": [{
                "id": name,
                "leftValue": expression,
                "rightValue": True,
                "operator": {"type": "boolean", "operation": "true", "singleValue": True},
            }],
            "combinator": "and",
        },
        "options": {},
    }, 2.2)


def _code(name, source):
    return _add(name, "n8n-nodes-base.code", {
        "mode": "runOnceForAllItems",
        "jsCode": source,
    })


def _edge(connections, source, target, output=0):
    groups = connections.setdefault(source, {"main": []})["main"]
    while len(groups) <= output:
        groups.append([])
    groups[output].append({"node": target, "type": "main", "index": 0})


def build():
    global _NODES
    _NODES = []
    queue_endpoint = f"https://sheets.googleapis.com/v4/spreadsheets/{PUBLISH_SHEET_ID}/values/"
    queue_range = quote(f"'{QUEUE_TAB}'!A1:O20000", safe="")
    queue_append_range = quote(f"'{QUEUE_TAB}'!A:O", safe="")
    schedule = _add("Every Five Minutes", "n8n-nodes-base.scheduleTrigger", {
        "rule": {"interval": [{"field": "cronExpression", "expression": "*/5 * * * *"}]},
    }, 1.2)
    manual = _add("Manual Handoff Check", "n8n-nodes-base.manualTrigger", {}, 1)
    claim = _http("Claim Verified Archive", "POST", GATEWAY + "/v1/publish-handoffs/claim")
    ready = _if("Handoff Ready?", "={{ $json.state === 'HANDOFF_IN_PROGRESS' }}")
    read = _http("Read Publisher Queue", "GET", queue_endpoint + queue_range, google=True)
    expected_headers = json.dumps(PUBLISH_HEADERS, ensure_ascii=False)
    choose_code = (
        "const h=$('Claim Verified Archive').first().json;const rows=$input.first().json.values;"
        "const expectedHeaders=" + expected_headers + ";"
        "if(!Array.isArray(rows)||!Array.isArray(rows[0])||JSON.stringify(rows[0].slice(0,15))!==JSON.stringify(expectedHeaders))"
        "throw new Error('publish_queue_header_mismatch');"
        "const found=[];for(let i=1;i<rows.length;i++)if(rows[i]?.[0]===h.publish_id)"
        "found.push({row_number:i+1,row_values:[...rows[i],...Array(Math.max(0,15-rows[i].length)).fill('')].slice(0,15)});"
        "if(found.length>1)throw new Error('publish_id_duplicate');"
        "if(found.length===1){const x=found[0];"
        "if(JSON.stringify(x.row_values.slice(0,7))!==JSON.stringify(h.row_values.slice(0,7))"
        "||!['대기','진행중','완료','실패'].includes(x.row_values[7]))"
        "throw new Error('publish_handoff_existing_row_conflict');"
        "return [{json:{...h,...x,expected_row_values:h.row_values,expected_headers:expectedHeaders,action:'existing'}}];}"
        "return [{json:{...h,expected_row_values:h.row_values,expected_headers:expectedHeaders,action:'append'}}];"
    )
    choose = _code("Choose Queue Action", choose_code)
    append_needed = _if("Append Required?", "={{ $json.action === 'append' }}");
    append = _http(
        "Append Publisher Queue Row",
        "POST",
        queue_endpoint + queue_append_range + ":append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
        body="={{ JSON.stringify({values:[$json.row_values]}) }}",
        google=True,
    )
    readback = _http("Read Back Publisher Queue", "GET", queue_endpoint + queue_range, google=True)
    verify_appended = _code("Verify Appended Queue Row", "const h=$('Choose Queue Action').first().json;const rows=$input.first().json.values??[];if(JSON.stringify(rows[0]?.slice(0,15))!==JSON.stringify(h.expected_headers))throw new Error('publish_queue_header_changed');const hits=[];for(let i=1;i<rows.length;i++)if(rows[i]?.[0]===h.publish_id)hits.push({row_number:i+1,row_values:[...rows[i],...Array(Math.max(0,15-rows[i].length)).fill('')].slice(0,15)});if(hits.length!==1)throw new Error('publish_handoff_readback_not_unique');const r=hits[0];if(JSON.stringify(r.row_values.slice(0,7))!==JSON.stringify(h.expected_row_values.slice(0,7))||!['대기','진행중','완료','실패'].includes(r.row_values[7]))throw new Error('publish_handoff_readback_mismatch');return [{json:{...h,...r}}];");
    verify_existing = _code("Verify Existing Queue Row", "const h=$input.first().json;if(h.action!=='existing'||!Number.isSafeInteger(h.row_number)||JSON.stringify(h.row_values.slice(0,7))!==JSON.stringify(h.expected_row_values.slice(0,7))||!['대기','진행중','완료','실패'].includes(h.row_values[7]))throw new Error('publish_handoff_existing_row_invalid');return [{json:h}];");
    ack = _http(
        "Acknowledge Queue Handoff",
        "POST",
        "={{ '" + GATEWAY + "/v1/publish-handoffs/' + $json.handoff_id + '/ack' }}",
        body="={{ JSON.stringify({claim_id:$json.claim_id,publish_id:$json.publish_id,sheet_id:$json.sheet_id,queue_tab:$json.queue_tab,row_number:$json.row_number,row_values:$json.row_values}) }}",
    )
    held = _code("No Verified Archive Ready", "return [{json:{state:'NO_HANDOFF_READY',reason:$input.first().json.reason??null}}];")
    connections = {}
    for trigger in (schedule, manual):
        _edge(connections, trigger, claim)
    _edge(connections, claim, ready)
    _edge(connections, ready, read)
    _edge(connections, ready, held, 1)
    _edge(connections, read, choose)
    _edge(connections, choose, append_needed)
    _edge(connections, append_needed, append)
    _edge(connections, append_needed, verify_existing, 1)
    _edge(connections, append, readback)
    _edge(connections, readback, verify_appended)
    _edge(connections, verify_appended, ack)
    _edge(connections, verify_existing, ack)
    return {
        "id": WORKFLOW_ID,
        "name": "SF Automatic Verified Render To Publisher Queue Handoff",
        "active": False,
        "nodes": _NODES,
        "connections": connections,
        "settings": {
            "executionOrder": "v1",
            "executionTimeout": 300,
            "timezone": "Asia/Seoul",
            "saveDataSuccessExecution": "none",
            "saveDataErrorExecution": "none",
        },
        "pinData": {},
        "tags": [],
    }
