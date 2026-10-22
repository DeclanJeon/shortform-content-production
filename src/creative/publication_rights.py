"""Content-specific originality policy. Never upgrades the source's copyright licence."""
import re
from .contract import canonical_sha256, validate_plan
from .source_material import EXTERNAL_INSPIRATION

POLICY='independent-fiction-v1'
ORIGINAL='ORIGINAL_FICTION_VERIFIED'
LICENSED='PERMISSION_VERIFIED'
REVIEW_KEYS={'decision','issues','source_comparison_performed','source_expression_reused',
             'identifiable_real_person','original_plot_and_characters'}


def review_input(source,plan):
    return {'source':source,'plan':plan,'scope':'independent_original_fiction_not_source_licensing'}


def overlap_count(source,plan):
    def norm(s):return ''.join(c for c in s.casefold() if c.isalnum())
    fields=('title','source_summary','logline','draft_script','document_text') if source.get('source_material_kind')==EXTERNAL_INSPIRATION else ('title','source_summary')
    original=norm('\n'.join(str(source.get(k,'')) for k in fields))
    output=norm(plan.get('synopsis','')+'\n'+'\n'.join(x.get('text','') for x in plan.get('narration_segments',[])))
    grams={original[i:i+20] for i in range(max(0,len(original)-19))}
    return len(grams & {output[i:i+20] for i in range(max(0,len(output)-19))})


def assess(source,plan,source_document_sha,review,request_sha,checker):
    validate_plan(plan)
    if (not isinstance(review,dict) or set(review)!=REVIEW_KEYS
            or review['decision'] not in ('PASS','REVISE') or not isinstance(review['issues'],list) or len(review['issues'])>12
            or any(not isinstance(x,str) or len(x)>2000 for x in review['issues'])
            or any(type(review[k]) is not bool for k in REVIEW_KEYS-{'decision','issues'})
            or not re.fullmatch(r'[0-9a-f]{64}',source_document_sha)
            or not re.fullmatch(r'[0-9a-f]{64}',request_sha)
            or not isinstance(checker,str) or not 1<=len(checker)<=120):
        raise ValueError('originality_review_invalid')
    overlap=overlap_count(source,plan)
    passed=(review['decision']=='PASS' and review['issues']==[]
            and review['source_comparison_performed'] is True
            and review['source_expression_reused'] is False
            and review['identifiable_real_person'] is False
            and review['original_plot_and_characters'] is True and overlap==0)
    record={'policy_version':POLICY,'decision':'PASS' if passed else 'HOLD',
            'basis':'independent_original_fiction','plan_sha256':canonical_sha256(plan),
            'source_document_sha256':source_document_sha,'source_input_sha256':canonical_sha256(source),
            'review_input_sha256':canonical_sha256(review_input(source,plan)),
            'review_request_sha256':request_sha,'review':review,'overlapping_20grams':overlap,
            'deterministic_comparison_scope':'external_title_and_summary',
            'checker_version':checker,'human_review':'NOT_PERFORMED',
            'source_licence_verified':False}
    return {**record,'evidence_artifact_id':'policy:'+canonical_sha256(record)}


def valid_policy(parent):
    policy=parent.get('content_policy')
    if not isinstance(policy,dict) or parent.get('review')!={'decision':'PASS','issues':[]}:return False
    try:
        if canonical_sha256(parent['plan'])!=parent.get('plan_sha256'):return False
        expected=assess(parent['source'],parent['plan'],parent['source_sha256'],
                        policy['review'],policy['review_request_sha256'],policy['checker_version'])
        return policy==expected and expected['decision']=='PASS'
    except (KeyError,ValueError,TypeError):return False


def effective_rights(parent,source_row=None):
    """Originality evidence belongs to this exact plan; the source stays UNVERIFIED."""
    if valid_policy(parent):
        p=parent['content_policy']
        return ORIGINAL,{'rights_evidence_type':'original_creation',
                         'rights_evidence_artifact_id':p['evidence_artifact_id'],
                         'policy_version':POLICY,'plan_sha256':p['plan_sha256'],
                         'source_licence_verified':False}
    source_row=source_row or {}
    return source_row.get('rights_state','UNVERIFIED'),source_row.get('evidence',{})


def qa08(parent,source_row=None):
    state,evidence=effective_rights(parent,source_row)
    passed=rights_pass(state,evidence,parent.get('review'))
    policy=parent.get('content_policy',{})
    return {'check_id':'QA-08','status':'PASS' if passed else 'NEEDS_REVIEW',
            'evidence_artifact_ids':['sha256:'+parent['plan_sha256']]+([evidence['rights_evidence_artifact_id']] if evidence.get('rights_evidence_artifact_id') else []),
            'checker_version':policy.get('checker_version','verified-license-plan-review')+':'+POLICY}


def rights_pass(state,evidence,plan_review):
    return (state in (ORIGINAL,LICENSED)
        and isinstance(evidence,dict)
        and evidence.get('rights_evidence_type') in (
            ('original_creation',) if state==ORIGINAL else ('license','written_permission','public_domain'))
        and bool(evidence.get('rights_evidence_artifact_id'))
        and isinstance(plan_review,dict) and plan_review.get('decision')=='PASS'
        and plan_review.get('issues')==[])
