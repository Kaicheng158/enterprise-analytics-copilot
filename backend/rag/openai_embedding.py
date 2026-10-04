"""Minimal official OpenAI embedding adapter; stdlib HTTP, no SDK."""
from dataclasses import dataclass, field
from decimal import Decimal
from http.client import HTTPException
import json
import logging
import os
from pathlib import Path
import random
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPRedirectHandler
from dotenv import load_dotenv
from .embedding import EmbeddingBatch, EmbeddingError, validate_vectors
from .models import EmbeddingProfile

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
URL = 'https://api.openai.com/v1/embeddings'
PRICE_PER_MILLION = Decimal('0.02')
PRICING_VERSION = 'openai-embedding-2026-10-04'
MAX_BATCH = 16
MAX_INPUT_BYTES = 8191  # Conservative upper bound for byte-tokenized UTF-8 input.
logger = logging.getLogger('rag.embedding')


@dataclass(frozen=True)
class EmbeddingSettings:
    api_key: str = field(repr=False)
    model: str = MODEL
    dimensions: int = DIMENSIONS
    timeout_seconds: float = 30
    max_retries: int = 2

    def __post_init__(self):
        if not self.api_key.strip(): raise EmbeddingError('embedding_key_missing')
        if self.model != MODEL or type(self.dimensions) is not int or self.dimensions != DIMENSIONS:
            raise EmbeddingError('unsupported_embedding_config')
        if not 0 < self.timeout_seconds <= 60 or type(self.max_retries) is not int or not 0 <= self.max_retries <= 2:
            raise EmbeddingError('invalid_embedding_retry_config')


def load_embedding_settings():
    load_dotenv(Path(__file__).resolve().parents[2]/'.env', override=False)
    try:
        dimensions = int(os.getenv('OPENAI_EMBEDDING_DIMENSIONS', str(DIMENSIONS)))
    except ValueError:
        raise EmbeddingError('unsupported_embedding_config') from None
    return EmbeddingSettings(os.getenv('OPENAI_API_KEY',''), os.getenv('OPENAI_EMBEDDING_MODEL',MODEL), dimensions)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward credentials to redirected endpoints.


def request_payload(request, timeout):
    with build_opener(NoRedirect()).open(request, timeout=timeout) as response:
        raw=response.read(4_000_001)
        if len(raw)>4_000_000: raise EmbeddingError('embedding_response_too_large')
        return json.loads(raw)


class OpenAIEmbeddingProvider:
    def __init__(self, settings): self.settings=settings

    @property
    def profile(self):
        return EmbeddingProfile('openai',self.settings.model,'api-alias:'+self.settings.model,
                                self.settings.dimensions,'document-raw-utf8-v1')

    def embed_query(self, text):
        # This OpenAI model uses identical raw-text preprocessing for both roles.
        return self.embed_documents((text,))

    def embed_documents(self,texts):
        if not 1 <= len(texts) <= MAX_BATCH or any(not isinstance(t,str) or not t.strip() or len(t.encode('utf-8'))>MAX_INPUT_BYTES for t in texts):
            raise EmbeddingError('embedding_invalid_input')
        started=time.monotonic()
        for attempt in range(self.settings.max_retries+1):
            try:
                request=Request(URL,data=json.dumps({'model':self.settings.model,'input':list(texts),
                    'dimensions':self.settings.dimensions,'encoding_format':'float'}).encode(),
                    headers={'Authorization':'Bearer '+self.settings.api_key,'Content-Type':'application/json'})
                data=request_payload(request,self.settings.timeout_seconds)
                if not isinstance(data,dict) or data.get('model') != self.settings.model:
                    raise EmbeddingError('embedding_model_mismatch')
                items=data['data']
                if not isinstance(items,list) or len(items)!=len(texts): raise EmbeddingError('embedding_count_mismatch')
                by_index={}
                for item in items:
                    index=item['index']
                    if type(index) is not int or index in by_index or not 0<=index<len(texts):
                        raise EmbeddingError('embedding_invalid_index')
                    by_index[index]=tuple(item['embedding'])
                vectors=tuple(by_index[i] for i in range(len(texts)))
                validate_vectors(vectors,len(texts),self.settings.dimensions)
                tokens=data['usage']['prompt_tokens'];total=data['usage']['total_tokens']
                if type(tokens) is not int or type(total) is not int or tokens<=0 or total!=tokens:
                    raise EmbeddingError('embedding_invalid_usage')
                batch=EmbeddingBatch(vectors,self.profile,tokens,round((time.monotonic()-started)*1000),attempt,
                                     str(Decimal(tokens)*PRICE_PER_MILLION/Decimal(1_000_000)),attempt==0)
                self._log('success',batch.latency_ms,attempt,tokens,batch.estimated_cost_usd,batch.cost_complete)
                return batch
            except HTTPError as error:
                # Examine only a bounded error code for quota classification; never log body.
                quota=False
                if error.code==429:
                    try:
                        body=json.loads(error.read(8192));code=body.get('error',{}).get('code')
                        quota=code in ('insufficient_quota','billing_hard_limit_reached')
                    except (ValueError,TypeError,AttributeError): pass
                retryable=error.code in (429,500,502,503,504) and not quota
                safe='embedding_quota_exhausted' if quota else {401:'embedding_auth_error',403:'embedding_permission_error',429:'embedding_rate_limited'}.get(error.code,'embedding_http_error')
                error.close()
                if retryable and attempt<self.settings.max_retries:
                    time.sleep(min(2**attempt,4)+random.uniform(0,0.25));continue
                self._log(safe,round((time.monotonic()-started)*1000),attempt)
                raise EmbeddingError(safe) from None
            except (URLError,TimeoutError,socket.timeout,OSError,HTTPException):
                self._log('embedding_network_error',round((time.monotonic()-started)*1000),attempt)
                raise EmbeddingError('embedding_network_error') from None
            except (KeyError,TypeError,ValueError,OverflowError) as error:
                safe=str(error) if isinstance(error,EmbeddingError) else 'embedding_invalid_response'
                self._log(safe,round((time.monotonic()-started)*1000),attempt)
                raise EmbeddingError(safe) from None

    def _log(self,status,latency,retries,tokens=None,cost=None,complete=False):
        logger.info('embedding_request %s',json.dumps({'provider':self.profile.provider,'model':self.profile.model,
            'dimensions':self.profile.dimensions,'status':status,'input_tokens':tokens,'latency_ms':latency,
            'retry_count':retries,'estimated_cost_usd':cost,'cost_complete':complete,'pricing_version':PRICING_VERSION}))
