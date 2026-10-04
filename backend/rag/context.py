"""Pure deterministic evidence formatting; no role messages, I/O or model calls."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math


def serialize(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',', ':'),allow_nan=False)


@dataclass(frozen=True)
class ContextConfig:
    max_chars: int = 8000
    version: str = 'evidence-json-char-v1'

    def __post_init__(self):
        if type(self.max_chars) is not int or not 0 <= self.max_chars <= 1_000_000:
            raise ValueError('max_chars must be an integer in [0,1000000]')
        if self.version != 'evidence-json-char-v1':raise ValueError('Unsupported context version')


def render(blocks):
    if not blocks:return ''
    return serialize({'trust':'untrusted_evidence','sources':blocks})


def prepare(hit):
    h=deepcopy(hit)
    if type(h.get('rank')) is not int or h['rank']<1:raise ValueError('Invalid rank')
    if type(h.get('distance')) not in (int,float) or not math.isfinite(h['distance']):raise ValueError('Invalid distance')
    if not isinstance(h.get('content'),str) or not h['content']:raise ValueError('Invalid content')
    for key in ('document_id','chunk_id'):
        if not isinstance(h.get(key),str) or not h[key]:raise ValueError('Missing identity')
    source=h['source_metadata'];meta=source['chunk_metadata']
    for key in ('source_uri','document_revision','locator'):
        if not isinstance(source.get(key),str) or not source[key]:raise ValueError('Missing provenance')
    start,end=meta['char_start'],meta['char_end']
    if type(start) is not int or type(end) is not int or start<0 or end-start!=len(h['content']) or meta.get('offset_unit')!='unicode_codepoint':
        raise ValueError('Inconsistent source offsets')
    identity=serialize([h['document_id'],h['chunk_id'],source['document_revision'],source['source_uri']])
    label='S-'+hashlib.sha256(identity.encode()).hexdigest()[:16]
    provenance={k:h[k] for k in ('document_id','chunk_id','rank','distance','source_metadata')}
    return h,start,end,identity,label,provenance


class ContextBuilder:
    def __init__(self,config=ContextConfig()):self.config=config

    def build(self,hits):
        prepared=[prepare(h) for h in hits]
        prepared.sort(key=lambda p:(p[0]['rank'],p[0]['distance'],p[0]['document_id'],p[0]['chunk_id'],serialize(p[0])))
        identities={};blocks=[];audit=[];covered={};exact={};stopped=False
        for h,start,end,identity,label,provenance in prepared:
            signature=(identity,h['content'],start,end)
            if label in identities and identities[label]!=signature:raise ValueError('Conflicting chunk identity or label collision')
            identities[label]=signature
            entry={'source_label':label,**provenance}
            if stopped:
                audit.append({**entry,'status':'omitted_budget'});continue
            if h['content'] in exact:
                audit.append({**entry,'status':'duplicate','duplicate_of':exact[h['content']]});continue
            key=(h['document_id'],h['source_metadata']['document_revision'],h['source_metadata']['source_uri'])
            spans=[(start,end)];removed=[]
            for old_start,old_end,old_text,old_label in covered.get(key,[]):
                remaining=[]
                for a,b in spans:
                    left,right=max(a,old_start),min(b,old_end)
                    if left<right and h['content'][left-start:right-start]==old_text[left-old_start:right-old_start]:
                        removed.append({'char_start':left,'char_end':right,'covered_by':old_label})
                        if a<left:remaining.append((a,left))
                        if right<b:remaining.append((right,b))
                    else:remaining.append((a,b))
                spans=remaining
            if not spans:
                audit.append({**entry,'status':'overlap_covered','removed_overlap':removed});continue
            segments=[{'char_start':a,'char_end':b,'text':h['content'][a-start:b-start]} for a,b in spans]
            block={'source_label':label,**provenance,'segments':segments}
            candidate=render([*blocks,block])
            if len(candidate)>self.config.max_chars:
                audit.append({**entry,'status':'omitted_budget'});stopped=True;continue
            blocks.append(block)
            audit.append({**entry,'status':'included','removed_overlap':removed})
            for segment in segments:
                covered.setdefault(key,[]).append((segment['char_start'],segment['char_end'],segment['text'],label))
            if spans==[(start,end)]:exact[h['content']]=label
        text=render(blocks)
        return {'context':text,'blocks':blocks,'audit':audit,'budget':{'unit':'unicode_codepoints',
            'max_chars':self.config.max_chars,'used_chars':len(text),'includes':'full serialized context, metadata and delimiters',
            'audit_included':False},'version':self.config.version}
