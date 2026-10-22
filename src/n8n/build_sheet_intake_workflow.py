from __future__ import annotations

import json
from pathlib import Path

SHEET_ID = "CONFIGURE_SOURCE_SHEET_ID"
GOOGLE_CREDENTIAL_ID = "CONFIGURE_GOOGLE_CREDENTIAL_ID"
GATEWAY_CREDENTIAL_ID = Path(__file__).with_name("gateway_credential_id.txt").read_text(encoding="utf-8").strip()


def build_workflow() -> dict:
    return {
        "id": "SFDailySheetIntake",
        "name": "Daily Sheet Intake",
        "active": False,
        "nodes": [
            {
                "parameters": {"rule": {"interval": [
                    {"field": "cronExpression", "expression": "0 */5 * * * *"},
                ]}},
                "id": "sheet-intake-schedule", "name": "Poll current Sheet every 5 minutes KST",
                "type": "n8n-nodes-base.scheduleTrigger", "typeVersion": 1.2, "position": [0, 0],
            },
            {"parameters": {}, "id": "sheet-intake-manual", "name": "Manual Trigger",
             "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1, "position": [0, 200]},
            {
                "parameters": {"method": "GET", "url": f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/%27%EC%BB%A4%EB%AE%A4%EB%8B%88%ED%8B%B0%20%EC%88%98%EC%A7%91%20%EC%9D%B4%EB%A0%A5%27!A1:W2000",
                               "authentication": "predefinedCredentialType", "nodeCredentialType": "googleDriveOAuth2Api",
                               "options": {"timeout": 30000}},
                "id": "sheet-intake-dashboard", "name": "Read community collection history",
                "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [260, 40],
                "credentials": {"googleDriveOAuth2Api": {"id": GOOGLE_CREDENTIAL_ID, "name": "Google Drive account"}},
            },
            {
                "parameters": {"method": "GET", "url": f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}/values/%27%EC%9D%BC%EC%9D%BC%20%EC%9A%94%EC%95%BD%27!A1:E2000",
                               "authentication": "predefinedCredentialType", "nodeCredentialType": "googleDriveOAuth2Api",
                               "options": {"timeout": 30000}},
                "id": "sheet-intake-summary", "name": "Read daily summary",
                "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [260, 220],
                "credentials": {"googleDriveOAuth2Api": {"id": GOOGLE_CREDENTIAL_ID, "name": "Google Drive account"}},
            },
            {
                "parameters": {"method": "POST", "url": "http://shortform-gateway:8080/v1/intake/sheets",
                               "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
                               "sendBody": True, "specifyBody": "json",
                               "jsonBody": "={{ { sheet_id: '" + SHEET_ID + "', dashboard_rows: $('Read dashboard').first().json.values, summary_rows: $('Read daily summary').first().json.values } }}",
                               "options": {"timeout": 30000}},
                "id": "sheet-intake-submit", "name": "Submit snapshot to gateway",
                "type": "n8n-nodes-base.httpRequest", "typeVersion": 4.2, "position": [540, 120],
                "credentials": {"httpHeaderAuth": {"id": GATEWAY_CREDENTIAL_ID, "name": "Shortform Gateway API"}},
            },
        ],
        "connections": {
            "Poll current Sheet every 5 minutes KST": {"main": [[{"node": "Read community collection history", "type": "main", "index": 0}]]},
            "Manual Trigger": {"main": [[{"node": "Read community collection history", "type": "main", "index": 0}]]},
            "Read community collection history": {"main": [[{"node": "Read daily summary", "type": "main", "index": 0}]]},
            "Read daily summary": {"main": [[{"node": "Submit snapshot to gateway", "type": "main", "index": 0}]]},
        },
        "settings": {"executionTimeout": 120, "saveExecutionProgress": False,
                     "saveDataSuccessExecution": "none", "saveDataErrorExecution": "none",
                     "timezone": "Asia/Seoul"},
        "staticData": None,
        "pinData": {},
        "tags": [],
    }


def main() -> None:
    print(json.dumps(build_workflow(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
