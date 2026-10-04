"""Exact, scoped cosine retrieval. Returns source data, never LLM instructions."""
from dataclasses import dataclass, asdict
import json
import math
import time
import uuid
from .chunking import digest
from .embedding import EmbeddingError, QueryEmbeddingProvider, validate_vectors
from .embedding_store import profile_id
from .models import AccessScope


@dataclass(frozen=True)
class RetrievalConfig:
    top_k: int = 5
    max_distance: float | None = None  # No invented universal relevance threshold.

    def __post_init__(self):
        if type(self.top_k) is not int or not 1 <= self.top_k <= 100:
            raise ValueError('top_k must be an integer in [1,100]')
        if self.max_distance is not None and (type(self.max_distance) not in (int,float) or not math.isfinite(self.max_distance) or not 0 <= self.max_distance <= 2):
            raise ValueError('max_distance must be finite in [0,2] or None')


# Filter before scoring. MATERIALIZED prevents an approximate vector-index plan
# from replacing full distance computation over eligible rows.
ELIGIBLE = '''SELECT c.chunk_id,c.document_id,c.document_revision,c.text_content,
    c.content_sha256,c.locator,c.metadata,d.source_uri,d.title,e.embedding
    FROM rag.document_chunks c
    JOIN rag.documents d ON (d.tenant_id,d.document_id,d.revision)=
                            (c.tenant_id,c.document_id,c.document_revision)
    JOIN rag.chunk_embeddings e ON (e.tenant_id,e.chunk_id)=(c.tenant_id,c.chunk_id)
    WHERE c.tenant_id=%s AND c.document_id=ANY(%s::uuid[])
      AND d.is_current AND d.status='ready' AND e.profile_id=%s AND e.dimensions=%s'''


def scope_parameters(scope,profile):
    if not scope.tenant_id.strip(): raise EmbeddingError('invalid_access_scope')
    try:
        ids=[str(uuid.UUID(value)) for value in scope.allowed_document_ids]
    except (ValueError,AttributeError,TypeError):
        raise EmbeddingError('invalid_access_scope') from None
    return (scope.tenant_id,ids,profile_id(profile),profile.dimensions)


def check_profile(connection,profile):
    row=connection.execute('SELECT provider,model,model_revision,preprocessing_version,dimensions,distance_metric FROM rag.embedding_profiles WHERE profile_id=%s',(profile_id(profile),)).fetchone()
    expected=(profile.provider,profile.model,profile.revision,profile.preprocessing_version,profile.dimensions,'cosine')
    if row is not None and row != expected: raise EmbeddingError('stored_profile_mismatch')
    return row is not None


def exact_search(connection, batch, *, profile, scope: AccessScope, config=RetrievalConfig()):
    if batch.profile != profile: raise EmbeddingError('embedding_profile_mismatch')
    validate_vectors(batch.vectors,1,profile.dimensions)
    parameters=scope_parameters(scope,profile)
    if not parameters[1]: return []
    with connection.transaction():
        connection.execute("SET LOCAL statement_timeout='10s'")
        if not check_profile(connection,profile): return []
        sql='WITH eligible AS MATERIALIZED ('+ELIGIBLE+'''), scored AS (
            SELECT *, embedding <=> %s::vector AS distance FROM eligible)
            SELECT chunk_id,document_id,document_revision,text_content,content_sha256,
                   locator,metadata,source_uri,title,distance FROM scored
            WHERE (%s::double precision IS NULL OR distance <= %s::double precision)
            ORDER BY distance ASC,document_id ASC,chunk_id ASC LIMIT %s'''
        rows=connection.execute(sql,(*parameters,json.dumps(batch.vectors[0],allow_nan=False),config.max_distance,config.max_distance,config.top_k)).fetchall()
    results=[]
    for rank,row in enumerate(rows,1):
        chunk,document,revision,content,sha,locator,metadata,source,title,distance=row
        if digest(content)!=sha: raise EmbeddingError('chunk_integrity_mismatch')
        if distance is None or not math.isfinite(distance): raise EmbeddingError('invalid_cosine_distance')
        results.append({'chunk_id':str(chunk),'document_id':str(document),'content':content,
            'source_metadata':{'source_uri':source,'title':title,'document_revision':revision,
                               'locator':locator,'chunk_metadata':metadata},
            'rank':rank,'distance':distance})
    return results


def retrieve(connection,provider: QueryEmbeddingProvider,query: str,*,scope: AccessScope,config=RetrievalConfig()):
    if not isinstance(query,str) or not query.strip() or len(query)>8000:
        raise EmbeddingError('invalid_query')
    started=time.monotonic();profile=provider.profile
    parameters=scope_parameters(scope,profile)
    with connection.transaction():
        connection.execute("SET LOCAL statement_timeout='10s'")
        eligible=bool(parameters[1]) and check_profile(connection,profile) and connection.execute('SELECT EXISTS('+ELIGIBLE+')',parameters).fetchone()[0]
    telemetry=None
    if eligible:
        batch=provider.embed_query(query)
        hits=exact_search(connection,batch,profile=profile,scope=scope,config=config)
        telemetry={k:v for k,v in asdict(batch).items() if k not in ('vectors','profile')}
    else:
        hits=[]  # Empty authorized/current/profile-matched corpus never incurs API cost.
    return {'profile_id':profile_id(profile),'top_k':config.top_k,'max_distance':config.max_distance,
            'results':hits,'query_embedding':telemetry,'latency_ms':round((time.monotonic()-started)*1000)}
