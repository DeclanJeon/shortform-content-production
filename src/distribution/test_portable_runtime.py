"""Offline skill bundle and prompt routing contracts; no provider or account calls."""
import copy
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import uuid
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT/'distribution'))
from creative import pi_runtime
from creative.contract import canonical_sha256, validate_plan
from creative.deliverables import collect, write_documents
from creative.project_archive import build_source
from creative.narrative import CRITERIA, validate_visual_proof
from creative.planning_contract import assemble_grouped, grouped_schema
from creative.production import build_asset_specs, materialize_plan
from creative.storyboard_project import (
    finalize_generation_attempts, project_from_plan, render_storyboard_artifacts,
    update_project_for_media,
)
from creative.skills import SKILLS, load_bundle, stage_bundle, stage_for_operation
from instance import Instance
import runtime

spec = importlib.util.spec_from_file_location('embed_skills', ROOT.parent/'build/embed_skills.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def grouped_fixture():
    """Populate the real grouped schema; fixture prose is not a quality assertion."""
    def value(schema):
        if 'const' in schema:
            return schema['const']
        if 'enum' in schema:
            return schema['enum'][0]
        match schema['type']:
            case 'object':
                return {key:value(schema['properties'][key]) for key in schema['required']}
            case 'array':
                return [value(schema['items']) for _ in range(schema['minItems'])]
            case 'string':
                return '가' * schema['minLength']
            case 'boolean':
                return False
            case 'number':
                return max(1, schema.get('minimum', 1))
        raise AssertionError(schema)
    raw = value(grouped_schema(True))
    for scene in raw['scenes']:
        scene['beat_ids'] = [f'B{i+1:02}' for i in range(6)]
    clause = '친구가 길을 못 찾았대. 그래서 먼저 나가 보기로 했지.'
    for i, beat in enumerate(raw['beats']):
        beat['id'] = f'B{i+1:02}'
        beat['function'] = ('hook','setup','development','turn','payoff','closing')[i]
        panel = beat['panels'][0]
        beat['panels'] = [copy.deepcopy(panel) for _ in range(3)]
        for j, panel in enumerate(beat['panels']):
            index = i*3+j
            panel['narration'] = clause
            shot = panel['shot']
            shot['action'] = f'{index+1}번째 장면에서 친구가 골목의 표지판을 보고 방향을 확인한다.'
            shot['shot_size'] = ('close','medium','wide','detail')[index%4]
            shot['performance']['critical'] = index in (3,12)
            shot['performance']['visible_reaction'] = f'{index+1}번째 표지판을 보고 고개를 돌려 친구에게 손을 흔든다.'
            shot['performance']['listening']['pause_after_seconds'] = 0.4 if index in (3,12) else 0
    raw['estimated_duration_seconds'] = 18
    script = {'master_text':' '.join([clause]*18),
              'delivery':'친구에게 이유를 설명하듯 차근차근 말한다.',
              'emotional_arc':'서운한 마음이 이해로 바뀌고 먼저 손을 내민다.'}
    return raw, script


def pitches_fixture():
    return {'candidates':[
        {'id':f'P{i+1}', 'logline':text*2, 'outline':text*4, 'hook':text,
         'estimated_image_count':20, 'scores':{'channel_fit':4,'originality':4,'feasibility':4}}
        for i,text in enumerate(('길을 잃은 친구에게 먼저 다가가는 이야기.',
                                 '오래된 우산을 고쳐 이웃에게 돌려주는 이야기.',
                                 '실수로 사라진 편지를 찾아 진심을 전하는 이야기.'))],
        'selection_reasons':['supplied editorial fixture']}


class PortableSkillContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base/'source'
        self.source.mkdir()
        shutil.copytree(ROOT/'skills', self.source/'skills')
        shutil.copytree(ROOT/'content', self.source/'content')
        self.repo = self.base/'repository'
        shutil.copytree(ROOT/'content', self.repo/'implementation/content')
        # Synthetic host contract fixtures, not redistributed host instructions.
        self.host = self.base/'host'
        self.module = self.host/'ai-delegation-loop/SKILL.md'
        self.module.parent.mkdir(parents=True)
        self.module.write_text('---\nname: ai-delegation-loop\n---\n')
        self.executor = self.host/'codex-imagen/scripts/codex-imagen.mjs'
        self.executor.parent.mkdir(parents=True)
        self.executor.write_text('// offline executor fixture\n')

    def bundle(self):
        return load_bundle(self.source/'skills', self.repo,
                           host_skill_root=self.host, image_executor_script=self.executor)

    def test_embedded_stage_files_are_scoped_and_host_files_are_not_copied(self):
        # Given the installed subset and a host without any content skills.
        bundle = self.bundle()
        # When resolving each machine-readable stage.
        scopes = {stage:stage_bundle(bundle, stage) for stage in ('topic','narrative','storyboard')}
        # Then no all-skills prompt exists, and future-stage instructions stay out.
        self.assertNotIn('text', bundle)
        paths = {stage:{f['path'] for f in scope['files']} for stage,scope in scopes.items()}
        self.assertIn('contents-production-skills/topic-demand-selection/SKILL.md', paths['topic'])
        self.assertNotIn('contents-production-skills/topic-demand-selection/SKILL.md', paths['narrative'])
        for name in ('orchestrating-video-preproduction','developing-video-synopses'):
            self.assertIn(f'contents-production-skills/{name}/SKILL.md', paths['narrative'])
            self.assertNotIn(f'contents-production-skills/{name}/SKILL.md', paths['topic'])
        self.assertIn('contents-production-skills/storyboarding-video/SKILL.md', paths['storyboard'])
        self.assertIn('contents-production-skills/video-production-assets/references/storyboard-contract.md', paths['storyboard'])
        self.assertNotIn('contents-production-skills/storyboarding-video/SKILL.md', paths['topic'])
        self.assertNotIn('contents-production-skills/storyboarding-video/SKILL.md', paths['narrative'])
        self.assertTrue((self.source/'skills/contents-production-skills/storyboarding-video/SKILL.md').is_file())
        self.assertEqual(set(bundle['stage_limitations']), {'storyboard'})
        self.assertFalse((self.source/'skills/ai-delegation-loop').exists())
        self.assertFalse((self.source/'skills/codex-imagen').exists())
        for scope in scopes.values():
            self.assertLessEqual(len(scope['text'].encode()), 300_000)
            self.assertIn('host/ai-delegation-loop/SKILL.md', {f['path'] for f in scope['files']})

    def test_every_embedded_file_is_hashed_and_bundle_stays_bounded(self):
        # Given the generated manifest.
        manifest = json.loads((self.source/'skills/BUNDLE_MANIFEST.json').read_text())
        # When checking the actual embedded inventory.
        paths = {entry['path'] for entry in manifest['files']}
        # Then only selected skills and references/scripts are shipped, once each.
        self.assertEqual(len(paths), len(manifest['files']))
        self.assertEqual(manifest['upstream_commit'], builder.EXPECTED_SOURCE_COMMIT)
        self.assertEqual(manifest['upstream_package_version'], '2.30')
        self.assertEqual(set(manifest['included_skills']), set(SKILLS))
        self.assertEqual(set(manifest['runtime_scripts']), {
            'storyboard_validation', 'storyboard_renderer', 'storyboard_splitter',
        })
        runtime_paths={path for paths in manifest['runtime_scripts'].values() for path in paths}
        self.assertTrue(runtime_paths <= paths)
        self.assertLess(sum(entry['size_bytes'] for entry in manifest['files']), builder.MAX_BUNDLE_BYTES)
        for entry in manifest['files']:
            raw = (self.source/'skills'/entry['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), entry['sha256'])
            self.assertEqual(len(raw), entry['size_bytes'])
            self.assertNotIn('community-shorts-stills-pipeline', entry['path'])
            self.assertNotIn('higgsfield', entry['path'])

    def test_pin_changes_for_host_executor_host_module_and_source_policy_drift(self):
        # Given a pin covering all three dependency kinds.
        before = self.bundle()['revision']
        for path in (self.executor, self.module, self.repo/'implementation/content/NARRATIVE_DELIVERY_POLICY.md'):
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original+b'\n')
                # When any one dependency changes.
                changed = self.bundle()['revision']
                # Then the gateway pin must change.
                self.assertNotEqual(before, changed)
                path.write_bytes(original)

    def test_embedded_drift_and_missing_files_fail_closed(self):
        # Given a modified or missing selected skill.
        path = self.source/'skills/contents-production-skills/developing-video-synopses/SKILL.md'
        original = path.read_bytes()
        for data in (b'drift', None):
            with self.subTest(missing=data is None):
                path.unlink()
                if data is not None:
                    path.write_bytes(data)
                # When loading or idempotently rebuilding an existing bundle.
                with self.assertRaises((ValueError, FileNotFoundError)):
                    self.bundle()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    builder.verify_existing(self.source/'skills')
                path.write_bytes(original)

    def test_unlisted_files_links_and_traversal_fail_closed(self):
        # Given extra untrusted bytes, a link, or a traversal in the manifest.
        extra = self.source/'skills/extra.md'
        extra.write_text('unlisted')
        with self.assertRaisesRegex(ValueError, 'inventory_drift'):
            self.bundle()
        extra.unlink()
        extra.symlink_to(self.module)
        with self.assertRaises(ValueError):
            self.bundle()
        extra.unlink()
        path = self.source/'skills/BUNDLE_MANIFEST.json'
        manifest = json.loads(path.read_text())
        manifest['files'][0]['path'] = 'contents-production-skills/../../escape'
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.bundle()

    def test_missing_or_misnamed_host_module_never_uses_embedded_substitute(self):
        # Given a bundle without explicit host configuration.
        with patch.dict('os.environ', {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'explicit_host'):
                load_bundle(self.source/'skills', self.repo)
        # When the configured module is missing or misnamed.
        self.module.unlink()
        with self.assertRaises(FileNotFoundError):
            self.bundle()
        self.module.write_text('---\nname: replacement-loop\n---\n')
        # Then exact-name resolution still fails.
        with self.assertRaisesRegex(ValueError, 'skill_name_mismatch'):
            self.bundle()

    def test_build_is_reproducible_and_rejects_dirty_source_and_output(self):
        # Given a frozen Git boundary and a local checkout of only selected inputs.
        upstream = self.base/'contents-production-skills'
        shutil.copytree(self.source/'skills/contents-production-skills', upstream)
        upstream_manifest = json.dumps({'version':'2.30', 'skills':list(SKILLS)}).encode()
        (upstream/'manifest.json').write_bytes(upstream_manifest)
        committed = {p.relative_to(upstream).as_posix():p.read_bytes() for p in upstream.rglob('*') if p.is_file()}
        def git(argv, **kwargs):
            if argv[-2:] == ['rev-parse', 'HEAD']:
                return SimpleNamespace(stdout=builder.EXPECTED_SOURCE_COMMIT)
            self.assertEqual(argv[3], 'show')
            return SimpleNamespace(stdout=committed[argv[4].split(':',1)[1]])
        projects = [self.base/name for name in ('first','second')]
        for project in projects:
            (project/'src').mkdir(parents=True)
        # When building twice and repeating an unchanged build.
        with patch.object(builder.subprocess, 'run', side_effect=git):
            for project in projects:
                self.assertEqual(builder.embed(upstream,project)['state'], 'SKILL_BUNDLE_EMBEDDED')
            self.assertEqual(builder.embed(upstream,projects[0])['state'], 'SKILL_BUNDLE_ALREADY_CURRENT')
            for path in (projects[0]/'src/skills').rglob('*'):
                if path.is_file():
                    self.assertEqual(path.read_bytes(), (projects[1]/'src/skills'/path.relative_to(projects[0]/'src/skills')).read_bytes())
            changed = upstream/'developing-video-synopses/SKILL.md'
            changed.write_bytes(changed.read_bytes()+b'\n')
            # Then dirty source cannot pass even with the same HEAD.
            with self.assertRaisesRegex(ValueError, 'skill_source_drift'):
                builder.embed(upstream,projects[0])
            changed.write_bytes(committed['developing-video-synopses/SKILL.md'])
            output = projects[0]/'src/skills/contents-production-skills/developing-video-synopses/SKILL.md'
            output.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'embedded_skill_drift'):
                builder.embed(upstream,projects[0],rebuild=True)

    def test_runtime_pin_matches_environment_resolved_embedded_loader(self):
        # Given host-only dependencies and the same mounted content-policy bytes.
        before = runtime.bundle_revision(self.source, self.host, self.executor)
        environment = {'CREATIVE_HOST_SKILL_ROOT':str(self.host),
                       'CREATIVE_CODEX_IMAGEN_SCRIPT':str(self.executor)}
        # When the unchanged coordinator/doctor two-argument caller loads them.
        with patch.dict('os.environ', environment, clear=True):
            bundle = load_bundle(self.source/'skills', self.repo)
        # Then installer and runtime pins agree.
        self.assertEqual(before, bundle['revision'])

    def test_configuration_mounts_embedded_and_host_dependencies_separately(self):
        # Given an inactive instance; source verification belongs to the lead's manifest refresh.
        pi = self.base/'pi'
        (pi/'dist').mkdir(parents=True)
        (pi/'dist/cli.js').write_text('// fixture')
        (pi/'package.json').write_text(json.dumps({'name':'@earendil-works/pi-coding-agent'}))
        auth = self.base/'auth'
        auth.mkdir()
        settings = {'pi_runtime_path':str(pi), 'skill_root':str(self.host),
                    'image_executor_script':str(self.executor), 'pi_auth_dir':str(auth),
                    'provider':'fixture', 'writer_model':'writer', 'reviewer_model':'reviewer',
                    'vision_model':'vision', 'channel_name':'fixture'}
        path = self.base/'runtime-spec.json'
        path.write_text(json.dumps(settings))
        instance = Instance(self.base, {'automation_enabled':False,'settings':{'project':'fixture'}})
        # When configuring without running Docker or reading credentials.
        with patch.object(runtime.Instance,'load',return_value=instance), patch.object(runtime.os,'getuid',return_value=1000), patch.object(runtime,'socket_group',return_value=1000):
            result = runtime.configure_runtime(self.base,path)
        # Then embedded history remains at /skills and executor/module remain host-only.
        config = json.loads((self.base/'creative-runtime.json').read_text())
        compose = json.loads((self.base/'runtime-compose.json').read_text())
        service = compose['services']['coordinator']
        mounts = {m['target']:m for m in service['volumes']}
        self.assertEqual(mounts['/skills']['source'], str(self.source/'skills'))
        self.assertEqual(mounts['/host-skills']['source'], str(self.host))
        self.assertTrue(mounts['/skills']['read_only'])
        self.assertTrue(mounts['/host-skills']['read_only'])
        self.assertEqual(config['skill_root'], '/skills')
        self.assertEqual(config['codex_imagen_script'], '/host-skills/codex-imagen/scripts/codex-imagen.mjs')
        self.assertEqual(service['environment']['CREATIVE_CODEX_IMAGEN_SCRIPT'],config['codex_imagen_script'])
        self.assertEqual(result['skill_revision'],self.bundle()['revision'])
        self.assertFalse(result['automation_enabled'])

    def test_active_local_creator_with_unknown_source_sheet_blocks_activation(self):
        from doctor import no_other_creator

        instance=SimpleNamespace(state={'settings':{
            'project':'shortform-content','source_sheet_id':'configuredSheet123',
        }})
        cases=[
            ('unknown source is conservatively blocked',
             ['AUTOMATIC_CREATIVE_ENABLED=true'],False),
            ('different known source does not conflict',
             ['AUTOMATIC_CREATIVE_ENABLED=true','SOURCE_SHEET_ID=otherSheet123'],True),
            ('same source blocks',
             ['AUTOMATIC_CREATIVE_ENABLED=true','SOURCE_SHEET_ID=configuredSheet123'],False),
            ('disabled creator does not conflict',
             ['AUTOMATIC_CREATIVE_ENABLED=false'],True),
        ]
        for label,environment,expected in cases:
            with self.subTest(label=label):
                entry={'Config':{
                    'Env':environment,
                    'Labels':{'com.docker.compose.project':'shortform'},
                }}
                with patch('doctor.subprocess.run',side_effect=[
                    SimpleNamespace(stdout='other-container'),
                    SimpleNamespace(stdout=json.dumps([entry])),
                ]):
                    self.assertEqual(no_other_creator(instance),expected)

    def test_v3_plan_renders_paginated_sheets_and_verified_clean_panel_splits(self):
        from PIL import Image
        from creative.media_runtime import VisualReviewRejected
        from creative.visual_repair import MAX_IMAGE_REVISION, MAX_REPAIR_ROUNDS
        from creative.visual_repair import repair_rejected_assets

        self.assertEqual(MAX_REPAIR_ROUNDS,5)
        self.assertEqual(MAX_IMAGE_REVISION,2)
        exhausted=VisualReviewRejected(
            ['SH01: fixture still fails review'],['SH01'],self.base/'unused-proof.png')
        with self.assertRaises(VisualReviewRejected):
            repair_rejected_assets(
                self.base,[{'asset_id':'SH01','revision':2}],{},self.base,{},None,
                exhausted,eligible_asset_ids={'SH01'})
        story_id='20261021-auto-0123456789ab'
        raw, script = grouped_fixture()
        plan = assemble_grouped({**raw,'narrative':script})
        validate_plan(plan)
        project_root = self.base/'full-storyboard-project'
        project_root.mkdir()
        skeleton = {
            'schema_version':'1.1','project_id':story_id,'version':'v001',
            'target_duration_s':None,'fps':None,'aspect_ratio':None,
            'scenes':[],'characters':[],'shots':[],'claims':[],'artifacts':[],
            'asset_registry':[],
        }
        (project_root/'project.json').write_text(json.dumps(skeleton),encoding='utf-8')
        materialize_plan(plan,project_root,version=1)
        specs=build_asset_specs(plan)
        for index,spec in enumerate(specs,1):
            path=project_root/spec['relative_path']
            path.parent.mkdir(parents=True,exist_ok=True)
            with Image.new('RGB',(540,960),(index*7%255,index*13%255,index*19%255)) as image:
                image.save(path,format='PNG')
        repaired=next(spec for spec in specs if spec['kind']=='scene_image')
        first_path=repaired['relative_path']
        first_prompt=f"06_GENERATION_SPECS/{repaired['asset_id']}-prompt-v1.txt"
        revised_path=f"03_SCENE_PACKS/{repaired['asset_id']}-v2.png"
        revised_prompt=f"06_GENERATION_SPECS/{repaired['asset_id']}-prompt-v2.txt"
        revised_image_path=project_root/revised_path
        with Image.new('RGB',(540,960),(31,97,163)) as image:
            image.save(revised_image_path,format='PNG')
        prompt_path=project_root/first_prompt
        prompt_sha=hashlib.sha256(prompt_path.read_bytes()).hexdigest()
        revised_prompt_path=project_root/revised_prompt
        revised_prompt_path.write_text(prompt_path.read_text(encoding='utf-8')+
                                       '\nFixture correction: clearer foreground framing.\n',
                                       encoding='utf-8')
        revised_prompt_sha=hashlib.sha256(revised_prompt_path.read_bytes()).hexdigest()
        source_ids=list(repaired.get('reference_ids') or [])
        repaired.update(revision=2,relative_path=revised_path,
                        prompt_relative_path=revised_prompt)
        repaired['attempts']=[
            {'attempt':1,'asset_id':repaired['asset_id'],'relative_path':first_path,
             'prompt_relative_path':first_prompt,'prompt_sha256':prompt_sha,
             'source_asset_ids':source_ids,'result':'reject','failure_class':'creative',
             'observed_failures':['fixture: the receipt is obscured'],
             'changes':[],'preserve':['selected story','cast identity','shot order'],
             'review_evidence_sha256':'a'*64},
            {'attempt':2,'asset_id':f"{repaired['asset_id']}-v2",
             'relative_path':revised_path,'prompt_relative_path':revised_prompt,
             'prompt_sha256':revised_prompt_sha,'source_asset_ids':source_ids,
             'result':'conditional','observed_failures':[],
             'changes':['clear the foreground receipt while preserving the action'],
             'preserve':['selected story','cast identity','shot order'],
             'review_evidence_sha256':'a'*64},
        ]
        projected=project_from_plan(skeleton,plan,specs,project_root,1)
        aligned=[{'shot_id':shot['id'],'start_seconds':shot['start_s'],
                  'end_seconds':shot['end_s']} for shot in projected['shots']]
        projected=update_project_for_media(projected,plan,specs,project_root,aligned,1)
        history_events=[]
        def event(operation,prompt,status,**kwargs):
            history_events.append((operation,status,kwargs))
        build=render_storyboard_artifacts(
            projected,plan,project_root,str(self.source/'skills'),1,event=event)

        scripts = ROOT/'skills/contents-production-skills/video-production-assets/scripts'
        sys.path.insert(0,str(scripts))
        from validate_project import validate as validate_project_ledger
        from validate_storyboard import validate_storyboard
        from asset_gate import validate_storyboard_extractions

        final_project=json.loads((project_root/'project.json').read_text(encoding='utf-8'))
        self.assertEqual(validate_project_ledger(final_project,'plan',project_root),[])
        self.assertEqual(validate_storyboard(final_project,project_root,True),[])
        self.assertEqual(validate_storyboard_extractions(final_project,project_root),[])
        self.assertEqual(len(projected['storyboard']['panels']),len(plan['shots']))
        self.assertEqual(final_project['preproduction']['mode'],'image_backed')
        self.assertEqual(build['panel_count'],len(plan['shots']))
        self.assertEqual(build['scene_count'],1)
        self.assertEqual(len(build['sheets']),3)
        self.assertEqual(len(build['panel_paths']),len(plan['shots']))
        self.assertEqual([status for _,status,_ in history_events],
                         ['started','completed','started','completed'])
        from creative import media_runtime
        from creative.narrative_visual import review_sequence
        review_calls=[]
        critical=[shot['id'] for shot in plan['shots'] if shot['performance']['critical']]
        def fixture_review(workbase,key,prompt,paths,config):
            review_calls.append(list(paths))
            if len(review_calls)==1:
                return {'decision':'PASS','issues':[],
                        'readings':[{'shot_id':shot['id'],'visible_action':'fixture visible action',
                                     'visible_emotion':'fixture visible emotion'} for shot in plan['shots']]}
            return {'decision':'PASS','issues':[],
                    'checks':[{'shot_id':shot_id,'status':'PASS',
                               'evidence':'fixture extracted panel visible evidence'}
                              for shot_id in critical]}
        media_work=project_root/'00_MANIFEST/.media-runtime'
        (media_work/'private').mkdir(parents=True,exist_ok=True)
        with patch.object(media_runtime,'_default_review',side_effect=fixture_review):
            visual=review_sequence(
                project_root,plan,[spec for spec in specs if spec['kind']=='scene_image'],
                build['panel_paths'],media_work,
                {'provider':'fixture','vision_model':'fixture'},event=None)
        self.assertEqual(len(review_calls),2)
        self.assertEqual(visual[0]['source_stage'],'extracted_storyboard_panels')
        self.assertTrue(visual[0]['checker_version'].endswith('image-only-storyboard-v2'))
        self.assertEqual(len(visual[0]['source_panels']),len(plan['shots']))
        self.assertEqual([panel['id'] for panel in visual[0]['source_panels']],
                         [shot['id'] for shot in plan['shots']])
        with Image.open(review_calls[0][0]) as image_only_board:
            self.assertEqual(image_only_board.getpixel((5,5)),(242,242,242))
        final_project=json.loads((project_root/'project.json').read_text(encoding='utf-8'))
        narrative_visual={
            'profile':'image-only-storyboard-v2',
            'source_stage':'extracted_storyboard_panels','decision':'PASS',
            'evidence_artifact_ids':visual[0]['evidence_artifact_ids'],
            'checker_version':visual[0]['checker_version'],
            'plan_sha256':canonical_sha256(plan),'human_review':'NOT_PERFORMED',
            'image_only_reading':visual[0]['image_only_reading'],
            'emotion_comparison':visual[0]['emotion_comparison'],
            'source_panels':visual[0]['source_panels'],
        }
        final_project=finalize_generation_attempts(final_project,narrative_visual)
        final_project['storyboard']['visual_review']=narrative_visual
        board_id=final_project['preproduction']['storyboard_artifact_id']
        next(a for a in final_project['artifacts'] if a['id']==board_id)['status']='reviewed'
        final_project['review']={'human_review':'NOT_PERFORMED'}
        (project_root/'project.json').write_text(json.dumps(final_project,ensure_ascii=False),encoding='utf-8')
        for directory in ('01_SOURCE','09_QA','10_DELIVERY'):
            (project_root/directory).mkdir(parents=True,exist_ok=True)
        (project_root/'00_MANIFEST/asset-specs-selected-v1.json').write_text(
            json.dumps(specs,ensure_ascii=False),encoding='utf-8')
        source_hash=hashlib.sha256(b'fixture source').hexdigest()
        (project_root/'01_SOURCE/source-snapshot.json').write_text(
            json.dumps({'story_id':story_id,'source_sha256':source_hash,
                        'source':{'document_text':'fixture source'}},ensure_ascii=False),encoding='utf-8')
        (project_root/'09_QA/production-approval.json').write_text(
            json.dumps({'state':'fixture-approved'},ensure_ascii=False),encoding='utf-8')
        video_bytes=b'fixture-video'
        video_sha=hashlib.sha256(video_bytes).hexdigest()
        (project_root/f'10_DELIVERY/{story_id}-v1.mp4').write_bytes(video_bytes)
        qa=[{'check_id':f'QA-{i:02}','status':'PASS',
             'evidence_artifact_ids':['sha256:'+'b'*64],'checker_version':'fixture'}
            for i in range(1,9)]
        media_result={'render_job_id':'00000000-0000-4000-8000-000000000001',
                      'tts_job_id':'00000000-0000-4000-8000-000000000002','sha256':video_sha,
                      'duration_seconds':18,'qa_checks':qa,
                      'narrative_review':{'profile':'fixture'},
                      'narrative_visual_review':final_project['storyboard']['visual_review']}
        (project_root/'10_DELIVERY/automatic-media-result-v1.json').write_text(
            json.dumps(media_result,ensure_ascii=False),encoding='utf-8')
        write_documents(project_root,plan,specs,media_result)
        project_path=project_root/'project.json'
        current_project=project_path.read_bytes()
        failed_review=json.loads(current_project)
        failed_review['storyboard']['visual_review']['decision']='REVISE'
        project_path.write_text(json.dumps(failed_review,ensure_ascii=False),encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError,'full_storyboard_archive_gate_failed'):
            collect(project_root,story_id,specs)
        project_path.write_bytes(current_project)
        archive_files={name for name,_ in collect(project_root,story_id,specs)}
        self.assertIn(first_path,archive_files)
        self.assertIn(revised_path,archive_files)
        self.assertIn(first_prompt,archive_files)
        self.assertIn(revised_prompt,archive_files)
        repaired_attempts=[row for row in final_project['generation_attempts']
                           if row['shot_id']==repaired['asset_id']]
        self.assertEqual([row['result'] for row in repaired_attempts],['reject','accept'])
        self.assertEqual(repaired_attempts[-1]['result_asset_id'],f"{repaired['asset_id']}-v2")
        self.assertIn('02_STORY/synopsis-v1.md',archive_files)
        self.assertIn('04_CHARACTER_BIBLE/style-world-bible-v1.md',archive_files)
        self.assertIn('04_STORYBOARDS/extracted-v1-a01/split-manifest.json',archive_files)
        self.assertTrue(any(name.endswith('storyboard_s01.png') and name.startswith('04_STORYBOARDS/sheets-')
                            for name in archive_files))
        self.assertEqual(sum(name.startswith('04_STORYBOARDS/extracted-v1-a01/scenes/') and '/panels/' in name
                             for name in archive_files),len(plan['shots']))
        archive,receipt=build_source(
            project_root,{'story_id':story_id,'plan_sha256':canonical_sha256(plan),
                          'plan_version':1},media_result,skill_root=str(self.source/'skills'))
        with zipfile.ZipFile(archive) as bundle:
            self.assertTrue(archive_files <= set(bundle.namelist()))
            bundle_manifest=json.loads(bundle.read('BUNDLE_MANIFEST.json'))
        self.assertEqual(bundle_manifest['retention_profile'],'deliverables-v2')
        self.assertEqual(receipt['file_count'],len(archive_files))
        bundle_root=self.base/'bundle-artifact-root'
        bundle_root.mkdir()
        audio_bytes=b'RIFF-fixture-voice'
        audio_path=bundle_root/'fixture-voice.wav'
        audio_path.write_bytes(audio_bytes)
        voice_captions=[{'start_seconds':0.0,'end_seconds':1.0,'text':script['master_text']}]
        jobs=[
            {'story_id':story_id,'stage':'render','status':'succeeded','run_id':'fixture-run',
             'result':{'job_id':'00000000-0000-4000-8000-000000000001','sha256':video_sha,
                       'size_bytes':len(video_bytes),'duration_seconds':18,'width':1080,'height':1920,
                       'video_codec':'h264','audio_codec':'aac'},
             'request':{'payload':{'caption_style':{},'render_manifest':{'scenes':[]}}},
             'approved_plan_sha256':canonical_sha256(plan),'approved_source_sha256':source_hash},
            {'story_id':story_id,'stage':'tts','status':'succeeded','run_id':'fixture-run',
             'result':{'sha256':hashlib.sha256(audio_bytes).hexdigest(),
                       'artifact_path':str(audio_path),'captions':voice_captions}},
        ]
        psycopg=ModuleType('psycopg');psycopg.connect=lambda *args,**kwargs:None
        psycopg_rows=ModuleType('psycopg.rows');psycopg_rows.dict_row=object()
        sys.path.insert(0,str(ROOT/'worker'))
        stage_spec=importlib.util.spec_from_file_location(
            'stage_project_bundle_fixture',ROOT/'worker/stage_project_bundle.py')
        stage_module=importlib.util.module_from_spec(stage_spec)
        with patch.dict(sys.modules,{'psycopg':psycopg,'psycopg.rows':psycopg_rows}):
            stage_spec.loader.exec_module(stage_module)
            staged=stage_module.stage({
                'story_id':story_id,'render_job_id':jobs[0]['result']['job_id'],
                'tts_job_id':jobs[1]['result'].get('job_id','00000000-0000-4000-8000-000000000002'),
                'zip_sha256':receipt['sha256'],
                'zip_base64':base64.b64encode(archive.read_bytes()).decode(),
            },bundle_root,jobs)
        self.assertEqual(staged['entry_count'],len(archive_files)+5)

    def test_visual_revision_restore_hashes_the_plan_versioned_base_prompt(self):
        from creative.visual_repair import restore_revisions

        root=self.base/'plan-v2-repair'
        (root/'06_GENERATION_SPECS').mkdir(parents=True)
        (root/'03_SCENE_PACKS').mkdir(parents=True)
        runtime=root/'00_MANIFEST/.media-runtime'
        runtime.mkdir(parents=True)
        base_prompt='06_GENERATION_SPECS/SH01-prompt-v2.txt'
        repair_prompt='06_GENERATION_SPECS/SH01-visual-repair-v2.txt'
        image_path='03_SCENE_PACKS/SH01-v2.png'
        (root/base_prompt).write_text('plan version two prompt',encoding='utf-8')
        (root/repair_prompt).write_text('single correction prompt',encoding='utf-8')
        (root/image_path).write_bytes(b'fixture image')
        entry={
            'revision':2,'prompt_relative_path':repair_prompt,'relative_path':image_path,
            'base_prompt_relative_path':base_prompt,
            'base_prompt_sha256':hashlib.sha256((root/base_prompt).read_bytes()).hexdigest(),
            'repair_prompt_sha256':hashlib.sha256((root/repair_prompt).read_bytes()).hexdigest(),
            'attempts':[
                {'attempt':1,'prompt_relative_path':base_prompt,'result':'reject'},
                {'attempt':2,'prompt_relative_path':repair_prompt,'result':'conditional'},
            ],
        }
        (runtime/'repair-index.json').write_text(
            json.dumps({'SH01':entry}),encoding='utf-8')
        restored=restore_revisions(
            root,[{'asset_id':'SH01','prompt_relative_path':base_prompt}],runtime)
        self.assertEqual(restored[0]['revision'],2)
        self.assertEqual(restored[0]['base_prompt_relative_path'],base_prompt)

    def test_v3_storyboard_rejects_scene_panel_state_and_character_drift(self):
        raw,script=grouped_fixture()
        plan=assemble_grouped({**raw,'narrative':script})
        validate_plan(plan)
        cases=[
            ('shot_scene_reference',lambda p:p['shots'][0].update(scene_id='SC24')),
            ('panel_shot_reference',lambda p:p['storyboard']['panels'][0].update(shot_id='SH24')),
            ('still_shot_state_change',lambda p:p['shots'][0].update(
                start_state='친구는 이미 우산을 손에 들고 있다.',
                end_state='친구는 우산을 이웃에게 건네고 있다.')),
            ('shot_scene_beat_reference',lambda p:p['scenes'][0].update(beat_ids=['B01'])),
            ('panel_character_reference',lambda p:p['storyboard']['panels'][0].update(
                visible_character_ids=['CH02'])),
        ]
        for expected,mutate in cases:
            with self.subTest(expected=expected):
                broken=copy.deepcopy(plan);mutate(broken)
                with self.assertRaisesRegex(ValueError,expected):
                    validate_plan(broken)

    def test_real_planning_calls_route_only_current_stage_and_preserve_review_gates(self):
        # Given the actual verified bundle and deterministic provider-boundary responses.
        bundle = self.bundle()
        raw, script = grouped_fixture()
        validate_plan(assemble_grouped({**raw,'narrative':script}))
        packets = []
        reviews = 0
        def executor(argv, **kwargs):
            nonlocal reviews
            packet = json.loads(kwargs['input'])
            packets.append(packet)
            scope = stage_bundle(bundle, stage_for_operation(packet['operation']))
            self.assertEqual(packet['skill_scope'], {'stage':scope['stage'],'files':scope['files']})
            # Exact shipped-copy equality, not a prose wording/snapshot assertion.
            system = Path(argv[argv.index('--system-prompt')+1]).read_bytes().decode('utf-8')
            self.assertEqual(system[-len(scope['text']):], scope['text'])
            match packet['operation']:
                case 'pitch':
                    value = pitches_fixture()
                case 'narrative_draft':
                    value = script
                case 'narrative_review':
                    value = {'decision':'PASS','issues':[],
                             'checks':[{'id':key,'status':'PASS','evidence':'offline fixture evidence only'} for key in CRITERIA]}
                case 'draft':
                    value = raw
                case 'review':
                    reviews += 1
                    value = {'decision':'REVISE','issues':['fixture transition issue']} if reviews == 1 else {'decision':'PASS','issues':[]}
                case 'patch':
                    value = {'base_plan_sha256':packet['plan_sha256'],
                             'edits':[{'path':'/shots/0/action','value':'첫 번째 표지판을 다시 바라보며 친구에게 길을 알려 주는 장면이다.'}]}
                case _:
                    self.fail(packet['operation'])
            return json.dumps(value,ensure_ascii=False)
        task = {'task_id':'fixture','source':{},'skill_revision':bundle['revision'],
                'selection_policy':{'pitches_required':True}}
        # When generating, independently reviewing, repairing, and reviewing again.
        result = pi_runtime.generate_plan(task,bundle,self.base/'planning',executor=executor)
        # Then all requests use their machine-selected stage without bypassing review.
        self.assertEqual([p['skill_scope']['stage'] for p in packets],
                         ['topic','narrative','narrative','storyboard','storyboard','storyboard','storyboard'])
        self.assertEqual(result['review']['decision'],'PASS')
        self.assertEqual(result['plan']['narrative'],script)
        self.assertEqual(result['provenance']['skill_revision'],bundle['revision'])
        self.assertEqual(result['provenance']['narrative_review']['script_sha256'],canonical_sha256(script))
        self.assertEqual(result['provenance']['call_count'],7)
        plan=result['plan'];critical=[shot['id'] for shot in plan['shots'] if shot['performance']['critical']]
        visual={
            'profile':'image-only-storyboard-v2','source_stage':'extracted_storyboard_panels',
            'decision':'PASS','plan_sha256':canonical_sha256(plan),
            'shot_ids':[shot['id'] for shot in plan['shots']],'human_review':'NOT_PERFORMED',
            'evidence_artifact_ids':['sha256:'+'1'*64,'sha256:'+'2'*64],
            'source_panels':[{'id':shot['id'],'sha256':'a'*64} for shot in plan['shots']],
            'image_only_reading':{'decision':'PASS','issues':[],
                'readings':[{'shot_id':shot['id'],'visible_action':'fixture action',
                             'visible_emotion':'fixture emotion'} for shot in plan['shots']]},
            'emotion_comparison':{'decision':'PASS','issues':[],
                'checks':[{'shot_id':shot_id,'status':'PASS',
                           'evidence':'fixture extracted panel evidence'} for shot_id in critical]},
        }
        validate_visual_proof(plan,result['provenance'],{
            'narrative_review':result['provenance']['narrative_review'],
            'narrative_visual_review':visual,
        })
        with self.assertRaises(ValueError):
            stage_for_operation('unassigned')


if __name__ == '__main__':
    unittest.main()
