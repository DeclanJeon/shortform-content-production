"""Wizard unit contracts. Fake stage runner is NOT real OAuth/production proof."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode=True
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/distribution'))
from installer import DEFAULT,prepare,settings,atomic
from wizard import wizard,workspace
from wizard_prompt import identifier,Terminal,external_path


class Answers:
    def __init__(self,values,secret='NON_FUNCTIONAL_WIZARD_TEST'):
        self.values=iter(values);self.key=secret;self.messages=[]
    def say(self,message):self.messages.append(message)
    def ask(self,label,default=''):return next(self.values) or default
    def secret(self,label):return self.key


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.target=self.base/'instance';self.calls=[]
        pi=self.base/'pi';(pi/'dist').mkdir(parents=True)
        (pi/'package.json').write_text(json.dumps({'name':'@earendil-works/pi-coding-agent'}));(pi/'dist/cli.js').write_text('// fixture')
        skills=self.base/'host-skills'
        delegation=skills/'ai-delegation-loop/SKILL.md';delegation.parent.mkdir(parents=True)
        delegation.write_text('---\nname: ai-delegation-loop\n---\n')
        executor=skills/'Agent-Creative-Skills/skills/codex-imagen/scripts/codex-imagen.mjs'
        executor.parent.mkdir(parents=True);executor.write_text('// nonfunctional fixture')
        auth=self.base/'auth';auth.mkdir(mode=0o700)
        self.models=[str(pi),str(auth),str(skills),str(executor),'fixture-provider','fixture-writer','fixture-reviewer','fixture-vision','내 테스트 채널']
        self.account_values=['https://docs.google.com/spreadsheets/d/sourceSheet123/edit',
            'https://docs.google.com/spreadsheets/d/publishSheet123/edit','발행 대기','공개','22',
            'https://drive.google.com/drive/folders/folderFixture123','googleFixture123','voiceFixture123']

    def answers(self,activation='',**kwargs):
        # target, core fields, inspiration, install consent, OAuth complete, IDs,
        # runtime consent, runtime fields, explicit paid/publication consent.
        return Answers(['','','','','','n','y','y',*self.account_values,'y',*self.models,activation],**kwargs)

    def backend(self,action,args):
        self.calls.append((action,args))
        if action=='setup':prepare(ROOT/'src',self.target,settings(Path(args[args.index('--config')+1])))
        if action=='runtime':
            key=Path(args[args.index('--typecast-key-file')+1]);self.staged_key=key
            self.assertEqual(key.stat().st_mode&0o777,0o600)
            self.assertEqual(key.read_text().strip(),'NON_FUNCTIONAL_WIZARD_TEST')
        return 0

    def test_success_leaves_ready_off_without_exact_enable(self):
        # Given valid user inputs and successful stand-in stages.
        ui=self.answers('yes')
        # When the user does not type the explicit activation word.
        result=wizard(ROOT/'src',self.target,console=ui,execute=self.backend)
        # Then no paid/publication enable is requested and temporary secrets are removed.
        self.assertEqual(result['state'],'READY_NOT_ENABLED')
        self.assertNotIn('enable',[a for a,_ in self.calls]);self.assertFalse(self.staged_key.exists())
        folder=workspace(self.target)
        self.assertEqual(json.loads((folder/'accounts.json').read_text())['source_sheet_id'],'sourceSheet123')
        self.assertFalse(any('NON_FUNCTIONAL_WIZARD_TEST' in p.read_text() for p in folder.glob('*.json')))
        self.assertTrue(all(p.stat().st_mode&0o777==0o600 for p in folder.glob('*.json')))

    def test_exact_enable_passes_the_existing_consent_gate(self):
        # Given successful stand-in readiness and an explicit ENABLE response.
        result=wizard(ROOT/'src',self.target,console=self.answers('ENABLE'),execute=self.backend)
        # Then only the existing consent-bearing enable command may activate.
        self.assertEqual(result['state'],'AUTOMATION_ENABLED')
        self.assertEqual([args for a,args in self.calls if a=='enable'],[['--allow-paid-generation']])

    def test_failed_readiness_stops_before_activation(self):
        # Given a stand-in readiness failure, even with future ENABLE input.
        def run(action,args):return 2 if action=='doctor' else self.backend(action,args)
        result=wizard(ROOT/'src',self.target,console=self.answers('ENABLE'),execute=run)
        # Then activation is not attempted.
        self.assertEqual(result['state'],'READINESS_FAILED_NOT_ENABLED')
        self.assertNotIn('enable',[a for a,_ in self.calls])

    def test_oauth_handoff_can_resume_without_json_editing(self):
        # Given a fresh core, stop at the real user's browser OAuth handoff.
        first=Answers(['','','','','','n','y','n'])
        result=wizard(ROOT/'src',self.target,console=first,execute=self.backend)
        # Then the core remains inactive and inputs are retained for wizard resumption.
        self.assertEqual(result['state'],'WAITING_FOR_ACCOUNTS')
        self.assertEqual([a for a,_ in self.calls],['setup'])
        self.assertTrue((workspace(self.target)/'settings.json').is_file())

    def test_resume_keeps_current_account_mapping_not_stale_saved_settings(self):
        # Given an account-mapped installation and older pre-onboarding preferences.
        original={**DEFAULT,'project':'fixture-studio','source_sheet_id':'currentSheet',
            'publish_sheet_id':'currentPublishSheet'}
        prepare(ROOT/'src',self.target,original)
        folder=workspace(self.target);atomic(folder/'settings.json',json.dumps(DEFAULT))
        # When resuming to the OAuth handoff.
        result=wizard(ROOT/'src',self.target,console=Answers(['','y','n']),execute=self.backend,idle=lambda p:True)
        # Then setup uses the actual installed mapping, not the stale preferences.
        self.assertEqual(result['state'],'WAITING_FOR_ACCOUNTS')
        self.assertEqual(settings(folder/'settings.json')['source_sheet_id'],'currentSheet')

    def test_resume_uses_corrected_native_accounts_instead_of_old_wizard_cache(self):
        # Given current account B corrected using the supported native stage,
        # while a prior wizard still has mapping A in its private preferences.
        current={**DEFAULT,'project':'fixture-studio','source_sheet_id':'currentSourceSheet',
            'publish_sheet_id':'currentPublishSheet','publish_queue_tab':'발행 대기',
            'publish_privacy':'공개','publish_category_id':22,'archive_folder_id':'currentArchiveFolder',
            'google_credential_id':'currentGoogleCredential','typecast_voice_id':'currentVoice'}
        prepare(ROOT/'src',self.target,current)
        folder=workspace(self.target)
        old={'source_sheet_id':'oldSourceSheet','publish_sheet_id':'oldPublishSheet',
            'publish_queue_tab':'발행 대기','publish_privacy':'비공개','publish_category_id':22,
            'archive_folder_id':'oldArchiveFolder','google_credential_id':'oldGoogleCredential',
            'typecast_voice_id':'oldVoice'}
        atomic(folder/'accounts.json',json.dumps(old))
        # When accepting current defaults through account configuration.
        result=wizard(ROOT/'src',self.target,console=Answers(['','y','y']+['']*8+['n']),execute=self.backend,idle=lambda p:True)
        # Then the mapping passed to accounts is B, not the stale A.
        self.assertEqual(result['state'],'WAITING_FOR_RUNTIME')
        self.assertEqual(json.loads((folder/'accounts.json').read_text()),{k:current[k] for k in old})

    def test_traversal_cannot_place_target_or_private_auth_inside_checkout(self):
        # Given paths lexically outside but canonically inside the checkout.
        for leaf in ('private-instance','private-auth'):
            alias=ROOT.parent/'missing-sibling'/'..'/ROOT.name/leaf
            with self.subTest(leaf=leaf),self.assertRaisesRegex(ValueError,'outside_checkout'):
                external_path(str(alias),ROOT/'src')

    def test_target_traversal_stops_before_workspace_creation(self):
        alias=ROOT.parent/'missing-sibling'/'..'/ROOT.name/'private-instance'
        with self.assertRaisesRegex(ValueError,'outside_checkout'):
            wizard(ROOT/'src',self.target,console=Answers([str(alias)]),execute=self.backend)
        self.assertEqual(self.calls,[])

    def test_active_instance_is_never_reconfigured(self):
        # Given an already active installer-owned instance.
        prepare(ROOT/'src',self.target,DEFAULT)
        path=self.target/'install-state.json';state=json.loads(path.read_text());state['automation_enabled']=True;atomic(path,json.dumps(state))
        before=path.read_bytes()
        # When starting the wizard again.
        result=wizard(ROOT/'src',self.target,console=Answers(['']),execute=self.backend)
        # Then no configuration or native action changes it.
        self.assertEqual(result['state'],'ALREADY_ENABLED_NO_CHANGES')
        self.assertEqual(path.read_bytes(),before);self.assertEqual(self.calls,[])

    def test_busy_instance_is_held_without_wizard_files(self):
        prepare(ROOT/'src',self.target,DEFAULT)
        result=wizard(ROOT/'src',self.target,console=Answers(['']),execute=self.backend,idle=lambda p:False)
        self.assertEqual(result['state'],'DRAINING_NO_CHANGES');self.assertEqual(self.calls,[])
        self.assertFalse((self.base/'.instance-setup').exists())

    def test_noninteractive_terminal_is_refused_without_echo_fallback(self):
        with patch('wizard_prompt.sys.stdin.isatty',return_value=False),self.assertRaisesRegex(ValueError,'interactive_terminal_required'):
            Terminal()

    def test_cancelled_secret_input_removes_private_temporary_directory(self):
        # Given complete inputs up to a secret prompt that is cancelled.
        ui=self.answers()
        def cancelled(label):raise EOFError
        ui.secret=cancelled
        # When cancellation interrupts secret collection.
        with self.assertRaises(EOFError):wizard(ROOT/'src',self.target,console=ui,execute=self.backend)
        # Then no temporary key directory survives and readiness/activation never run.
        self.assertEqual(list(workspace(self.target).glob('secret-*')),[])
        self.assertNotIn('doctor',[a for a,_ in self.calls])

    def test_existing_unowned_workspace_is_not_overwritten(self):
        folder=self.base/'.instance-setup';folder.mkdir(mode=0o700);note=folder/'notes';note.write_text('keep')
        with self.assertRaisesRegex(ValueError,'workspace_collision'):workspace(self.target)
        self.assertEqual(note.read_text(),'keep')

    def test_secret_warning_cannot_fall_back_to_echoed_input(self):
        import getpass,warnings
        terminal=Terminal.__new__(Terminal)
        def unsafe(label):warnings.warn('fixture warning',getpass.GetPassWarning)
        with patch('wizard_prompt.getpass.getpass',side_effect=unsafe),self.assertRaisesRegex(ValueError,'secure_secret_input_unavailable'):
            terminal.secret('key')

    def test_symlinked_wizard_lock_is_refused(self):
        folder=workspace(self.target);outside=self.base/'keep';outside.write_text('keep');(folder/'wizard.lock').symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'wizard_input_link_forbidden'):
            wizard(ROOT/'src',self.target,console=Answers(['']),execute=self.backend)
        self.assertEqual(outside.read_text(),'keep');self.assertEqual(self.calls,[])

    def test_google_resource_id_rejects_non_google_url(self):
        value=identifier(Answers(['https://example.com/not-a-google-resource','https://docs.google.com/spreadsheets/d/sheetFixture123/edit']),'sheet')
        self.assertEqual(value,'sheetFixture123')


if __name__=='__main__':unittest.main()
