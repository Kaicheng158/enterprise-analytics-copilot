"""Explicit current-document vector persistence. No search or query embedding."""
from dataclasses import asdict
import json
import uuid
from .chunking import digest, canonical
from .embedding import EmbeddingError, validate_vectors


def profile_id(profile):
    return str(uuid.uuid5(uuid.NAMESPACE_URL,canonical({**asdict(profile),'distance_metric':'cosine'})))


def persist_embeddings(connection, provider, *, tenant_id, document_id, revision):
    profile=provider.profile
    pid=profile_id(profile)
    key=(tenant_id,document_id,revision)
    expected_profile=(profile.provider,profile.model,profile.revision,profile.preprocessing_version,profile.dimensions,'cosine')
    # Require an explicit document revision, never a global corpus scan.
    with connection.transaction():
        stored_profile=connection.execute('SELECT provider,model,model_revision,preprocessing_version,dimensions,distance_metric FROM rag.embedding_profiles WHERE profile_id=%s',(pid,)).fetchone()
        if stored_profile is not None and stored_profile != expected_profile:
            raise EmbeddingError('stored_profile_mismatch')
        row=connection.execute('SELECT status,is_current FROM rag.documents WHERE tenant_id=%s AND document_id=%s AND revision=%s',key).fetchone()
        if row != ('ready',True): raise EmbeddingError('document_not_current_ready')
        chunks=connection.execute('''SELECT c.chunk_id,c.text_content,c.content_sha256,e.chunk_id
            FROM rag.document_chunks c LEFT JOIN rag.chunk_embeddings e
            ON e.tenant_id=c.tenant_id AND e.chunk_id=c.chunk_id AND e.profile_id=%s
            WHERE c.tenant_id=%s AND c.document_id=%s AND c.document_revision=%s ORDER BY c.ordinal''',(pid,*key)).fetchall()
    if not chunks: raise EmbeddingError('document_has_no_chunks')
    if any(digest(text)!=sha for _,text,sha,_ in chunks): raise EmbeddingError('chunk_integrity_mismatch')
    missing=[row for row in chunks if row[3] is None]
    if not missing: return {'profile_id':pid,'inserted':0,'reused':len(chunks),'requests':[]}
    results=[];vectors=[]
    # Bounded batches; all vectors for this document commit together after validation.
    for start in range(0,len(missing),16):
        subset=missing[start:start+16]
        result=provider.embed_documents(tuple(row[1] for row in subset))
        if result.profile != profile: raise EmbeddingError('embedding_profile_mismatch')
        validate_vectors(result.vectors,len(subset),profile.dimensions)
        vectors.extend(result.vectors)
        results.append({k:v for k,v in asdict(result).items() if k not in ('vectors','profile')})
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout='5s'")
        connection.execute("SET LOCAL statement_timeout='30s'")
        # Network is outside the transaction. Reject a stale source before any writes.
        row=connection.execute('SELECT status,is_current FROM rag.documents WHERE tenant_id=%s AND document_id=%s AND revision=%s FOR UPDATE',key).fetchone()
        if row != ('ready',True): raise EmbeddingError('document_changed_during_embedding')
        current=connection.execute('SELECT chunk_id,text_content,content_sha256 FROM rag.document_chunks WHERE tenant_id=%s AND document_id=%s AND document_revision=%s ORDER BY ordinal FOR SHARE',key).fetchall()
        if current != [row[:3] for row in chunks]: raise EmbeddingError('chunks_changed_during_embedding')
        connection.execute('''INSERT INTO rag.embedding_profiles
            (profile_id,provider,model,model_revision,preprocessing_version,dimensions,distance_metric)
            VALUES (%s,%s,%s,%s,%s,%s,'cosine') ON CONFLICT (profile_id) DO NOTHING''',
            (pid,profile.provider,profile.model,profile.revision,profile.preprocessing_version,profile.dimensions))
        stored=connection.execute('SELECT provider,model,model_revision,preprocessing_version,dimensions,distance_metric FROM rag.embedding_profiles WHERE profile_id=%s',(pid,)).fetchone()
        if stored != (profile.provider,profile.model,profile.revision,profile.preprocessing_version,profile.dimensions,'cosine'):
            raise EmbeddingError('stored_profile_mismatch')
        inserted=0
        for (chunk_id,_,_,_),vector in zip(missing,vectors):
            cursor=connection.execute('''INSERT INTO rag.chunk_embeddings
                (tenant_id,chunk_id,profile_id,dimensions,embedding) VALUES (%s,%s,%s,%s,%s::vector)
                ON CONFLICT (tenant_id,chunk_id,profile_id) DO NOTHING''',
                (tenant_id,chunk_id,pid,profile.dimensions,json.dumps(vector,allow_nan=False)))
            inserted+=cursor.rowcount
    return {'profile_id':pid,'inserted':inserted,'reused':len(chunks)-inserted,'requests':results}
