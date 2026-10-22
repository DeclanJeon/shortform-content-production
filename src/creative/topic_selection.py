"""Transparent editorial ranking; never claims independent trend measurements."""
import re
from urllib.parse import urlsplit
from .contract import canonical_sha256
from .source_material import sheet_inspiration_document

VERSION='sheet-editorial-v1'
PII=re.compile(r'(?:01[016789][- .]?\d{3,4}[- .]?\d{4}|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})')
HARD_RISK=('주민등록','실명공개','신상털','불법촬영','몰카유포')
CATEGORIES=('일상','감동','유머','공감','인간관계','생활','가족','친구')


def title_key(title):return ''.join(c for c in title.casefold() if c.isalnum())


def rank_candidates(snapshot,rows,published=(),*,allow_sheet_inspiration=False):
    used_titles={title_key(x.get('title','')) for x in published}
    used_urls={x.get('source_url') for x in published}
    reports=[]
    for item in snapshot['items']:
        cells=rows[item['source_key']]
        title=item['title'];logline=cells[16].strip();script=cells[18].strip();notes=cells[19].strip();doc=cells[20].strip()
        reasons=[];blocked=[]
        if item['source_date']!=snapshot['date']:blocked.append('stale_source')
        if item['url'] in used_urls or title_key(title) in used_titles:blocked.append('duplicate_existing_story')
        if cells[22].strip().upper() == 'TRUE':blocked.append('source_already_used')
        text=' '.join((title,logline,script,cells[13] if allow_sheet_inspiration else ''))
        if PII.search(text):blocked.append('personal_contact_data')
        if any(x in text for x in HARD_RISK):blocked.append('privacy_or_illegal_capture_risk')
        try:
            parsed=urlsplit(doc)
            valid_doc=(parsed.scheme=='https' and parsed.hostname=='docs.google.com' and parsed.port in (None,443)
                       and re.fullmatch(r'/document/d/[A-Za-z0-9_-]{10,200}(?:/.*)?',parsed.path) and not parsed.username and not parsed.password)
        except ValueError:valid_doc=False
        inspiration=allow_sheet_inspiration and sheet_inspiration_document({'title':title,'source_summary':cells[13]}) is not None
        if not valid_doc and not inspiration:blocked.append('creative_document_unavailable')
        score=10+max(0,25-min(25,item['rank'])*2)
        reasons.extend(['current_day_snapshot','supplied_sheet_rank:'+str(item['rank'])])
        if any(x in item['category'] for x in CATEGORIES):score+=10;reasons.append('channel_category_fit')
        if 15<=len(logline)<=300:score+=15;reasons.append('usable_creative_logline')
        chars=len(''.join(script.split()))
        if 200<=chars<=650:score+=15;reasons.append('bounded_narration_draft')
        elif 30<=chars<=2000:score+=5;reasons.append('draft_requires_rewriting')
        if notes:score+=5;reasons.append('cast_notes_available')
        if valid_doc:score+=20;reasons.append('exportable_creative_document')
        elif inspiration:score+=10;reasons.append('accepted_external_inspiration_not_author_brief')
        complexity=sum(x in text for x in ('거울','반사광','정밀한광학','복잡한3D'))
        if complexity:score-=min(15,complexity*5);reasons.append('2d_feasibility_penalty')
        reports.append({'source_key':item['source_key'],'source_rank':item['rank'],
                        'score':max(0,score),'eligible':not blocked,'reasons':reasons,'exclusions':blocked,
                        'input_sha256':canonical_sha256({'item':item,'creative_cells':cells[16:21],'source_used':cells[22]})})
    reports.sort(key=lambda x:(not x['eligible'],-x['score'],x['source_rank'],x['source_key']))
    return {'policy_version':VERSION,'date':snapshot['date'],'snapshot_sha256':snapshot['snapshot_sha256'],
            'demand_evidence':'supplied_sheet_rank_not_independent_market_measurement','candidates':reports,
            'selected_source_key':next((x['source_key'] for x in reports if x['eligible']),None),
            'daily_default':12,'maximum_image_calls_per_story':27}
