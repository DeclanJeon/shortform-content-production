"""Offline public contracts: no provider authentication, media purchase or upload."""
import ast
import copy
from datetime import datetime,timezone,timedelta
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.dont_write_bytecode=True
sys.path.insert(0,str(ROOT/'src/distribution'))
from installer import DEFAULT,prepare,verify_source,dotenv
from instance import Instance
from lifecycle import accept_receipt,enable
from doctor import REQUIRED_CHECKS,execution_evidence,headers
from preflight_workflow import PUBLISH_HEADERS
from workflows import render


class InstallationContracts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'instance'
        prepare(ROOT/'src',self.root,copy.deepcopy(DEFAULT))

    def receipt(self,**changes):
        instance=Instance.load(self.root)
        body={'format':'shortform-readiness-v1','checked_at':datetime.now(timezone.utc).isoformat(),
            'fingerprint':instance.fingerprint(),'ready':True,
            'checks':[{'name':name,'status':'PASS'} for name in REQUIRED_CHECKS],**changes}
        value={**body,'signature':instance.sign(body)}
        (self.root/'doctor-receipt.json').write_text(json.dumps(value))
        return instance

    def test_source_is_verified_and_instance_starts_without_credentials_or_authority(self):
        # Given the public source and a newly prepared instance.
        # When reading its authoritative state.
        state=Instance.load(self.root).state
        # Then no automatic paid/public action was authorized by installation.
        self.assertFalse(state['automation_enabled'])
        self.assertFalse((self.root/'secrets/typecast_api_key').read_text())
        self.assertEqual(state['source_manifest_sha256'],verify_source(ROOT/'src'))
        environment=dotenv(self.root/'.env')
        self.assertNotIn('YOUTUBE_CHANNEL_ID',environment)
        self.assertNotIn('YOUTUBE_AUTOMATION_ENABLED',environment)
        self.assertNotIn('youtube_credential_id',DEFAULT)

    def test_prepare_rerun_preserves_native_encryption_and_gateway_keys(self):
        # Given generated per-instance secrets.
        before=(self.root/'.env').read_bytes()
        # When preparing the same source/settings again.
        prepare(ROOT/'src',self.root,copy.deepcopy(DEFAULT))
        # Then secrets are not rotated.
        self.assertEqual((self.root/'.env').read_bytes(),before)

    def test_new_install_path_cannot_overwrite_unmanaged_user_files(self):
        # Given existing unknown user data.
        path=Path(self.temp.name)/'notes';path.mkdir();(path/'important.txt').write_text('keep')
        # When treating it as an installation destination.
        with self.assertRaises(ValueError):prepare(ROOT/'src',path,copy.deepcopy(DEFAULT))
        # Then unknown files remain intact.
        self.assertEqual((path/'important.txt').read_text(),'keep')

    def test_exact_fresh_complete_readiness_is_accepted_without_claiming_real_oauth(self):
        # Given synthetic signed protocol evidence (not a live Google check).
        instance=self.receipt()
        # When parsing the activation boundary.
        result=accept_receipt(instance,datetime.now(timezone.utc))
        # Then only the exact complete check set is accepted.
        self.assertEqual({c['name'] for c in result['checks']},set(REQUIRED_CHECKS))

    def test_failed_or_missing_check_cannot_be_hidden_by_overall_ready(self):
        for defect in ('failed','missing'):
            with self.subTest(defect=defect):
                # Given an overall ready with a distinct evidence defect.
                checks=[{'name':name,'status':'PASS'} for name in REQUIRED_CHECKS]
                if defect=='failed':checks[0]['status']='FAIL'
                if defect=='missing':checks.pop()
                instance=self.receipt(checks=checks)
                # When parsing readiness.
                with self.assertRaises(ValueError):accept_receipt(instance,datetime.now(timezone.utc))

    def test_modified_receipt_signature_is_rejected(self):
        # Given a signed receipt changed after its checks.
        instance=self.receipt();p=self.root/'doctor-receipt.json';raw=json.loads(p.read_text());raw['ready']=False;p.write_text(json.dumps(raw))
        # When validating the changed evidence.
        with self.assertRaises(ValueError):accept_receipt(instance,datetime.now(timezone.utc))

    def test_expired_or_future_receipt_is_not_a_valid_activation(self):
        for delta in (-3600,3600):
            with self.subTest(delta=delta):
                # Given stale or future-dated evidence.
                instance=self.receipt(checked_at=(datetime.now(timezone.utc)+timedelta(seconds=delta)).isoformat())
                # When activating now.
                with self.assertRaises(ValueError):accept_receipt(instance,datetime.now(timezone.utc))

    def test_account_or_secret_change_invalidates_previous_evidence(self):
        # Given valid checks before account settings changed.
        instance=self.receipt();p=self.root/'install-state.json';state=json.loads(p.read_text());state['settings']['source_sheet_id']='anotherSheetFixture';p.write_text(json.dumps(state))
        # When using the old checks with new account bindings.
        with self.assertRaises(ValueError):accept_receipt(Instance.load(self.root),datetime.now(timezone.utc))

    def test_automatic_production_requires_explicit_consent(self):
        # Given an inactive core stack.
        # When requesting production without the consent flag.
        with self.assertRaises(ValueError):enable(self.root,consent=False)

    def test_native_execution_success_without_exact_hmac_is_not_account_readback(self):
        # Given actual CLI-shaped data, but the wrong challenge signature.
        output='Execution was successful:'+json.dumps({'data':{'status':'OWN_GOOGLE_RESOURCES_AND_QUEUE_VERIFIED','challenge':'fixture','signer_digest':'wrong'}})
        # When the read-only signer evidence is parsed.
        self.assertFalse(execution_evidence(output,'fixture','expected-fixture-digest'))

    def test_native_execution_requires_the_exact_challenge(self):
        # Given nested native CLI data for this exact check.
        output='Execution was successful:'+json.dumps({'data':{'main':[[{'json':{'status':'OWN_GOOGLE_RESOURCES_AND_QUEUE_VERIFIED','challenge':'fixture','signer_digest':'expected-fixture-digest'}}]]}})
        # When the bound challenge and digest match.
        self.assertTrue(execution_evidence(output,'fixture','expected-fixture-digest'))

    def test_native_cli_separator_and_raw_output_are_supported(self):
        # Given actual n8n2.43 framing and the documented --rawOutput form.
        data={'data':{'resultData':{'runData':{'HMAC':[{'data':{'main':[[{'json':{
            'status':'OWN_GOOGLE_RESOURCES_AND_QUEUE_VERIFIED','challenge':'fixture','signer_digest':'expected-fixture-digest'}}]]}}]}}}}
        raw=json.dumps(data)
        for output in ('Execution was successful:\n====================================\n'+raw,raw):
            with self.subTest(raw_output=output==raw):
                # When parsing native signer evidence.
                self.assertTrue(execution_evidence(output,'fixture','expected-fixture-digest'))

    def test_native_failed_execution_cannot_reuse_a_success_shaped_partial_item(self):
        # Given a failed execution containing an earlier valid-shaped item.
        data={'data':{'resultData':{'error':{'message':'fixture failure'},'runData':{
            'earlier':{'status':'OWN_GOOGLE_RESOURCES_AND_QUEUE_VERIFIED','challenge':'fixture','signer_digest':'expected-fixture-digest'}}}}}
        # When checking the overall native result.
        self.assertFalse(execution_evidence(json.dumps(data),'fixture','expected-fixture-digest'))

    def test_restore_requires_different_snapshot_project_and_port(self):
        from restore import validate_new_identity
        for project,port in ((DEFAULT['project'],25678),('other-studio',DEFAULT['n8n_port'])):
            with self.subTest(project=project),self.assertRaisesRegex(ValueError,'new_project_and_port'):
                validate_new_identity(DEFAULT,project,port)
        validate_new_identity(DEFAULT,'other-studio',25678)

    def test_fresh_setup_refuses_orphan_project_storage(self):
        from installer import preflight
        from types import SimpleNamespace
        from unittest.mock import patch
        def inventory(argv,**kwargs):
            value=DEFAULT['project']+'_database\\n' if argv[:3]==['docker','volume','ls'] else ''
            return SimpleNamespace(returncode=0,stdout=value)
        with patch('installer.subprocess.run',side_effect=inventory),self.assertRaisesRegex(ValueError,'resources_already_used'):
            preflight(Path(self.temp.name)/'fresh-destination',DEFAULT)

    def test_fresh_source_contains_signed_approval_replay_storage(self):
        # Native PostgreSQL callback/replay is tested separately; this cheap CI
        # guard prevents accidentally exporting the pre-migration fresh schema.
        sql=(ROOT/'src/db/schema.sql').read_text()
        self.assertIn('CREATE TABLE review_callback_events (',sql)
        self.assertIn('nonce text NOT NULL UNIQUE',sql)
        self.assertIn('event_id text PRIMARY KEY',sql)

    def test_shell_entrypoints_are_executable_from_a_clone(self):
        # Given the published checkout, commands in the quickstart must launch directly.
        import os
        for name in ('install.sh','shortform'):
            with self.subTest(name=name):self.assertTrue(os.access(ROOT/name,os.X_OK))

    def test_public_scan_rejects_private_snapshot_and_key_paths(self):
        # Given filenames which can contain recovery material without familiar key prefixes.
        import runpy
        scan=runpy.run_path(str(ROOT/'scripts/check_public.py'))['scan']
        for name in ('activation-before-crash.json','recovery.key','.env.old','runtime-recovery/auth.json'):
            with self.subTest(name=name),self.assertRaises(SystemExit):scan(name,'fixture content')

    def test_public_scan_rejects_embedded_random_runtime_secret(self):
        # Given an accidental settings dump whose API token is not an sk-/Bearer token.
        import runpy,secrets
        scan=runpy.run_path(str(ROOT/'scripts/check_public.py'))['scan']
        data=json.dumps({'API_TOKEN':secrets.token_urlsafe(32)})
        # When checking a generic JSON filename.
        with self.assertRaises(SystemExit):scan('settings-export.json',data)

    def test_sheet_headers_and_account_mappings_are_not_author_claims(self):
        # Given explicit fixture IDs and the canonical schema data.
        dashboard,summary=headers(ROOT/'src')
        config={**DEFAULT,'source_sheet_id':'fixtureSourceSheet','publish_sheet_id':'fixturePublishSheet',
            'archive_folder_id':'fixtureFolder','google_credential_id':'fixtureGoogle'}
        # When rendering configuration (no import or remote call).
        workflows=render(ROOT/'src',config)
        # Then all six remain inactive and the actual input schema is present.
        self.assertEqual(len(workflows),6)
        self.assertTrue(all(w['active'] is False for w in workflows))
        self.assertEqual((len(dashboard),len(summary)),(23,5))
        self.assertEqual(
            {w['id'] for w in workflows},
            {
                'SFDailySheetIntake',
                'SFAutomaticPrivateDriveArchive',
                'SFAutomaticProjectArchive',
                'SFAutomaticPolicyApproval',
                'SFAutomaticProductionProjection',
                'SFAutomaticPublishQueueHandoff',
            },
        )
        self.assertEqual(PUBLISH_HEADERS,[
            '발행ID','영상링크','제목','설명','태그','카테고리','공개설정','상태','유튜브ID',
            '발행URL','발행시각','오류','시도횟수','발행키','클레임시각',
        ])
        self.assertTrue(all(
            node.get('type') != 'n8n-nodes-base.youTube'
            and 'youTubeOAuth2Api' not in (node.get('credentials') or {})
            for workflow in workflows for node in workflow['nodes']
        ))
        source_files={entry['path'] for entry in json.loads((ROOT/'src/MANIFEST.json').read_text())['entries']}
        self.assertNotIn('api/manual_release.py',source_files)
        self.assertNotIn('worker/adapters/youtube_publish.py',source_files)
        self.assertNotIn('n8n/build_workflows.py',source_files)
        api_tree=ast.parse((ROOT/'src/api/main.py').read_text(encoding='utf-8'))
        imports={alias.name for node in ast.walk(api_tree) if isinstance(node,ast.ImportFrom)
            for alias in node.names}
        self.assertIn('create_publish_handoff_router',imports)
        self.assertNotIn('create_manual_release_router',imports)


if __name__=='__main__':unittest.main()
