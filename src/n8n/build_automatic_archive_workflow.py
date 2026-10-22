"""Build the inactive automatic private Google Drive archive consumer."""
from __future__ import annotations

import json
from pathlib import Path

WORKFLOW_ID = "SFAutomaticPrivateDriveArchive"; GATEWAY = "http://shortform-gateway:8080"
GATEWAY_CREDENTIAL = {
    "id": "CONFIGURE_GATEWAY_CREDENTIAL_ID",
    "name": "Shortform Gateway API",
}; DRIVE_CREDENTIAL = {"id": "CONFIGURE_GOOGLE_CREDENTIAL_ID", "name": "Google Drive account"}


def build() -> dict[str, object]:
    nodes: list[dict[str, object]] = []; connections: dict[str, dict[str, list[list[dict[str, object]]]]] = {}

    def node(name: str, kind: str, parameters: dict[str, object], version: float = 2) -> str:
        item: dict[str, object] = {
            "id": f"automatic-archive-{len(nodes)}", "name": name, "type": kind,
            "typeVersion": version, "position": [220 * (len(nodes) % 8), 180 * (len(nodes) // 8)],
            "parameters": parameters,
        }
        nodes.append(item)
        return name

    def code(name: str, source: str) -> str:
        return node(name, "n8n-nodes-base.code", {"mode": "runOnceForAllItems", "jsCode": source})

    def request(
        name: str,
        method: str,
        url: str,
        *,
        gateway: bool = False,
        drive: bool = False,
        parameters: dict[str, object] | None = None,
    ) -> str:
        values: dict[str, object] = {
            "method": method,
            "url": url,
            "options": {"timeout": 60000},
            **(parameters or {}),
        }
        item_name = node(name, "n8n-nodes-base.httpRequest", values, 4.2); item = nodes[-1]
        item["retryOnFail"] = False
        if gateway:
            values.update(authentication="genericCredentialType", genericAuthType="httpHeaderAuth")
            item["credentials"] = {"httpHeaderAuth": GATEWAY_CREDENTIAL}
        if drive:
            values.update(authentication="predefinedCredentialType", nodeCredentialType="googleDriveOAuth2Api")
            item["credentials"] = {"googleDriveOAuth2Api": DRIVE_CREDENTIAL}
        return item_name

    def gate(name: str, expression: str) -> str:
        return node(
            name,
            "n8n-nodes-base.if",
            {
                "conditions": {
                    "options": {"caseSensitive": True, "typeValidation": "strict", "version": 2},
                    "conditions": [{
                        "id": name, "leftValue": expression, "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true", "singleValue": True},
                    }],
                    "combinator": "and",
                },
                "options": {},
            },
            2.2,
        )

    def edge(source: str, target: str, output: int = 0) -> None:
        groups = connections.setdefault(source, {"main": []})["main"]
        while len(groups) <= output:
            groups.append([])
        groups[output].append({"node": target, "type": "main", "index": 0})

    schedule = node(
        "Every Five Minutes",
        "n8n-nodes-base.scheduleTrigger",
        {"rule": {"interval": [{"field": "cronExpression", "expression": "*/5 * * * *"}]}},
        1.2,
    )
    manual = node("Manual Archive Check", "n8n-nodes-base.manualTrigger", {}, 1)
    claim = request("Claim Archive", "POST", f"{GATEWAY}/v1/automatic-archives/claim", gateway=True)
    validate = code(
        "Validate Archive Claim",
        """const x=$input.first()?.json??{};
if(x.state==='NO_ARCHIVE_READY')return [{json:{ready:false,publication:false,youtube_upload:false}}];
if(x.state!=='ARCHIVE_IN_PROGRESS'||!Number.isSafeInteger(x.archive_id)||x.archive_id<=0||
typeof x.claim_id!=='string'||!x.claim_id||typeof x.folder_id!=='string'||!x.folder_id||
typeof x.file_name!=='string'||!/^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$/.test(x.file_name)||
typeof x.sha256!=='string'||!/^[a-f0-9]{64}$/.test(x.sha256)||
typeof x.md5!=='string'||!/^[a-f0-9]{32}$/.test(x.md5)||
!Number.isSafeInteger(x.size_bytes)||x.size_bytes<=0||typeof x.artifact_url!=='string'||
x.publication!==false||x.youtube_upload!==false)throw new Error('archive_claim_invalid');
return [{json:{...x,ready:true}}];""",
    )
    ready = gate("Archive Ready?", "={{ $json.ready === true }}")
    has_id = gate("Drive ID Bound?", "={{ typeof $json.drive_file_id === 'string' && $json.drive_file_id.length >= 10 }}")
    generate = request(
        "Generate Drive ID",
        "GET",
        "https://www.googleapis.com/drive/v3/files/generateIds?count=1&space=drive&type=files",
        drive=True,
    )
    generated = code(
        "Validate Generated Drive ID",
        """const ids=$input.first()?.json?.ids;
if(!Array.isArray(ids)||ids.length!==1||typeof ids[0]!=='string'||!/^[A-Za-z0-9_-]{10,200}$/.test(ids[0]))
throw new Error('drive_id_generation_failed');
const c=$('Validate Archive Claim').first().json;return [{json:{...c,drive_file_id:ids[0]}}];""",
    )
    bind = request(
        "Bind Drive ID Before Upload",
        "POST",
        "={{ '" + GATEWAY + "/v1/automatic-archives/' + $json.archive_id + '/bind' }}",
        gateway=True,
        parameters={
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({claim_id:$json.claim_id,drive_file_id:$json.drive_file_id}) }}",
        },
    )
    canonical = code(
        "Canonical Archive Identity",
        """const x=$input.first()?.json??{};
if(typeof x.drive_file_id!=='string'||!/^[A-Za-z0-9_-]{10,200}$/.test(x.drive_file_id)||
typeof x.claim_id!=='string'||!Number.isSafeInteger(x.archive_id))throw new Error('canonical_drive_identity_missing');
return [{json:x}];""",
    )
    inspect = request(
        "Inspect Canonical Drive File",
        "GET",
        "={{ 'https://www.googleapis.com/drive/v3/files/' + $json.drive_file_id + '?fields=id,name,mimeType,size,md5Checksum,parents,trashed' }}",
        drive=True,
        parameters={"options": {"timeout": 30000, "response": {"response": {
            "neverError": True, "fullResponse": True, "responseFormat": "json",
        }}}},
    )
    classify = code(
        "Classify Drive File State",
        """const c=$('Canonical Archive Identity').first().json;const r=$input.first()?.json??{};
if(r.statusCode===404)return [{json:{...c,action:'create'}}];
if(r.statusCode!==200||!r.body)throw new Error('drive_metadata_outcome_unknown');
const x=r.body;const identity=x.id===c.drive_file_id&&x.name===c.file_name&&x.mimeType==='video/mp4'&&
Array.isArray(x.parents)&&x.parents.length===1&&x.parents[0]===c.folder_id&&x.trashed===false;
if(!identity)throw new Error('drive_file_identity_conflict');
const size=Number(x.size??0);
if(size===0)return [{json:{...c,action:'upload'}}];
if(size!==c.size_bytes||x.md5Checksum!==c.md5)throw new Error('drive_nonempty_checksum_conflict');
return [{json:{...c,action:'ack',google_file:{...x,size}}}];""",
    )
    create_gate = gate("Create Metadata?", "={{ $json.action === 'create' }}")
    create = request(
        "Create Canonical Drive Metadata",
        "POST",
        "https://www.googleapis.com/drive/v3/files?fields=id",
        drive=True,
        parameters={
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({id:$json.drive_file_id,name:$json.file_name,mimeType:'video/mp4',parents:[$json.folder_id]}) }}",
        },
    )
    inspect_created = request(
        "Inspect Created Drive File",
        "GET",
        "={{ 'https://www.googleapis.com/drive/v3/files/' + $('Canonical Archive Identity').first().json.drive_file_id + '?fields=id,name,mimeType,size,md5Checksum,parents,trashed' }}",
        drive=True,
        parameters={"options": {"timeout": 30000, "response": {"response": {
            "neverError": True, "fullResponse": True, "responseFormat": "json",
        }}}},
    )
    require_empty = code(
        "Require Empty Canonical File",
        """const c=$('Canonical Archive Identity').first().json;const r=$input.first()?.json??{};const x=r.body??{};
if(r.statusCode!==200||x.id!==c.drive_file_id||x.name!==c.file_name||x.mimeType!=='video/mp4'||
Number(x.size??0)!==0||!Array.isArray(x.parents)||x.parents.length!==1||x.parents[0]!==c.folder_id||x.trashed!==false)
throw new Error('created_drive_metadata_invalid');return [{json:{...c,action:'upload'}}];""",
    )
    upload_gate = gate("Upload Required?", "={{ $json.action === 'upload' }}")
    download = request(
        "Download Verified Artifact",
        "GET",
        "={{ '" + GATEWAY + "' + $json.artifact_url }}",
        gateway=True,
        parameters={"options": {"timeout": 60000, "response": {"response": {
            "neverError": True, "responseFormat": "file", "outputPropertyName": "data", "fullResponse": True,
        }}}},
    )
    verify_download = code(
        "Verify Artifact Headers",
        """const c=$('Canonical Archive Identity').first().json;const i=$input.first()??{};const h=i.json?.headers??{};
const header=n=>h[n]??h[n.toLowerCase()]??h[n.toUpperCase()];const b=i.binary?.data;
if(i.json?.statusCode!==200||!b||header('x-artifact-sha256')!==c.sha256||
Number(header('x-artifact-size-bytes'))!==c.size_bytes||header('x-artifact-codec')!=='h264')
throw new Error('archive_artifact_verification_failed');return [{json:c,binary:{data:b}}];""",
    )
    upload = request(
        "Upload Bytes To Canonical ID",
        "PATCH",
        "={{ 'https://www.googleapis.com/upload/drive/v3/files/' + $json.drive_file_id + '?uploadType=media&fields=id' }}",
        drive=True,
        parameters={
            "sendBody": True,
            "contentType": "binaryData",
            "inputDataFieldName": "data",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "Content-Type", "value": "video/mp4"}]},
        },
    )
    readback = request(
        "Read Google Checksum",
        "GET",
        "={{ 'https://www.googleapis.com/drive/v3/files/' + $('Canonical Archive Identity').first().json.drive_file_id + '?fields=id,name,mimeType,size,md5Checksum,parents,trashed' }}",
        drive=True,
    )
    verify_google = code(
        "Verify Google Checksum",
        """const c=$('Canonical Archive Identity').first().json;const x=$input.first()?.json??{};
const size=Number(x.size);if(x.id!==c.drive_file_id||x.name!==c.file_name||x.mimeType!=='video/mp4'||size!==c.size_bytes||
x.md5Checksum!==c.md5||!Array.isArray(x.parents)||x.parents.length!==1||x.parents[0]!==c.folder_id||x.trashed!==false)
throw new Error('google_checksum_readback_mismatch');return [{json:{...c,google_file:{...x,size}}}];""",
    )
    ack = request(
        "Acknowledge Verified Archive",
        "POST",
        "={{ '" + GATEWAY + "/v1/automatic-archives/' + $json.archive_id + '/ack' }}",
        gateway=True,
        parameters={
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify({claim_id:$json.claim_id,sha256:$json.sha256,google_file:$json.google_file}) }}",
        },
    )

    for trigger in (schedule, manual):
        edge(trigger, claim)
    edge(claim, validate); edge(validate, ready); edge(ready, has_id)
    edge(has_id, canonical); edge(has_id, generate, 1); edge(generate, generated); edge(generated, bind); edge(bind, canonical)
    edge(canonical, inspect); edge(inspect, classify); edge(classify, create_gate)
    edge(create_gate, create); edge(create, inspect_created); edge(inspect_created, require_empty); edge(require_empty, download)
    edge(create_gate, upload_gate, 1); edge(upload_gate, download); edge(upload_gate, ack, 1)
    edge(download, verify_download); edge(verify_download, upload); edge(upload, readback); edge(readback, verify_google); edge(verify_google, ack)

    return {
        "id": WORKFLOW_ID,
        "name": "SF Automatic Private Drive Archive",
        "active": False,
        "nodes": nodes,
        "connections": connections,
        "settings": {
            "executionOrder": "v1", "executionTimeout": 900,
            "saveDataSuccessExecution": "none", "saveDataErrorExecution": "all", "timezone": "Asia/Seoul",
        },
        "pinData": {},
        "tags": [],
    }


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
