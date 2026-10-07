"""Additive diagnostics; the existing validator remains the sole acceptance authority."""
from copy import deepcopy
import re
from backend.llm import ProviderError
from backend.output import AnalyticsAnswer
from backend.rag.generation import GroundedGenerator, validate_sources

ATTRIBUTION_ERRORS = frozenset({"rag_invalid_citation", "rag_missing_attribution"})


def diagnose(answer, blocks):
    """Replay individual items through the ORIGINAL validator, in its original order.
    Return safe metadata and a separate synthetic-only detail object. No logging.
    """
    known={b['source_label'] for b in blocks}
    for field,values in answer.model_dump().items():
        for index,text in enumerate([values] if isinstance(values,str) else values):
            item={'summary':'','facts':[],'interpretation':[],'limitations':[]}
            item[field]=text if field=='summary' else [text]
            try:
                validate_sources(AnalyticsAnswer.model_validate(item),blocks)
            except ProviderError as error:
                tokens=re.findall(r"\[S-[^\]]*(?:\]|$)",text)
                parsed=[]
                for token in tokens:
                    label=token[1:-1] if token.endswith(']') else ''
                    valid=bool(re.fullmatch(r'S-[0-9a-f]{16}',label))
                    parsed.append({'token':token,'label':label,'format_valid':valid,'in_context':valid and label in known})
                safe={'stage':'attribution_validation','failed_field':field,'item_index':index,
                      'error_code':error.code,'matched_source_token_count':len(tokens),
                      'valid_known_label_count':sum(x['in_context'] for x in parsed),
                      'valid_unknown_label_count':sum(x['format_valid'] and not x['in_context'] for x in parsed),
                      'malformed_token_count':sum(not x['format_valid'] for x in parsed),
                      'user_attribution_detected':'[user]' in text,
                      'parsing_result':('missing_recognized_attribution' if error.code=='rag_missing_attribution' else 'invalid_source_citation')}
                private={'detected_source_labels':[x['label'] for x in parsed],
                         'attribution_parsing':parsed,'available_source_labels':sorted(known)}
                return safe,private
    return {'stage':'attribution_validation','parsing_result':'failure_not_reproduced'},{}


class _CaptureProvider:
    def __init__(self,provider):self.provider=provider;self.result=None
    def generate_messages(self,messages,metadata):
        self.result=self.provider.generate_messages(messages,metadata)
        return self.result


class DiagnosticGroundedGenerator:
    """Internal wrapper; each request has isolated capture state.

    Default is production: only body-free fixed diagnostic metadata is retained.
    synthetic_eval must be selected explicitly by trusted operator/eval code,
    never by HTTP input or inferred from document contents. It adds local error
    evidence only; no extra logging, provider call, repair or retry occurs.
    generator_type allows offline isolated candidates without changing defaults.
    """
    def __init__(self,provider,settings,*,capture_mode='production',generator_type=GroundedGenerator,**options):
        if capture_mode not in ('production','synthetic_eval'):raise ValueError('Invalid capture mode')
        # Preserve original settings compatibility check before any provider call.
        if hasattr(provider,'settings') and provider.settings!=settings:raise ValueError('Generation settings mismatch')
        self.provider=provider;self.settings=settings;self.mode=capture_mode
        self.generator_type=generator_type;self.options=options

    def generate(self,query,context):
        capture=_CaptureProvider(self.provider)
        try:
            return self.generator_type(capture,self.settings,**self.options).generate(query,context)
        except ProviderError as error:
            if error.code not in ATTRIBUTION_ERRORS or capture.result is None:raise
            safe,private=diagnose(capture.result.answer,context['blocks'])
            error.attribution_diagnostic=safe
            error.telemetry={**error.telemetry,"attribution_diagnostic":deepcopy(safe)}
            if self.mode=='synthetic_eval':
                # Kept OUT of telemetry/logger. Local eval caller must explicitly save it.
                error.synthetic_eval_evidence={
                    'diagnostic':deepcopy(safe),**private,
                    'rejected_structured_response':capture.result.answer.model_dump(),
                    'generation':capture.result.model_dump(exclude={'answer'}),
                    'note':'Parsed structured answer, not raw provider HTTP bytes. Synthetic opt-in only.'}
            raise
