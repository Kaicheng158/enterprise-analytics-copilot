"""Transactional source snapshots; no embedding writes or retrieval."""
from pathlib import Path
from psycopg.types.json import Jsonb
from .chunking import StructureChunker


def ingest(connection, loader, path, *, tenant_id):
    path = Path(path)
    document = loader.load(path, tenant_id=tenant_id)
    chunks = StructureChunker(loader.config).split(document)
    key = (document.tenant_id, document.document_id, document.revision)
    with connection.transaction():
        connection.execute("SET LOCAL lock_timeout = '5s'")
        connection.execute("SET LOCAL statement_timeout = '30s'")
        connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (document.document_id,))
        existing = connection.execute('SELECT content_sha256,metadata,status FROM rag.documents WHERE tenant_id=%s AND document_id=%s AND revision=%s',key).fetchone()
        if existing:
            rows=connection.execute('SELECT chunk_id,ordinal,text_content,locator,chunker_version,content_sha256,metadata FROM rag.document_chunks WHERE tenant_id=%s AND document_id=%s AND document_revision=%s ORDER BY ordinal',key).fetchall()
            expected=[(c.chunk_id,c.ordinal,c.text,c.locator,c.chunker_version,c.content_sha256,c.metadata) for c in chunks]
            actual=[(str(row[0]),*row[1:]) for row in rows]
            if existing != (document.content_sha256,document.metadata,'ready') or actual != expected:
                raise ValueError('Stored revision integrity mismatch; no automatic repair')
        else:
            connection.execute('''INSERT INTO rag.documents
                (tenant_id,document_id,revision,title,source_uri,media_type,content_sha256,metadata,status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending')''',
                (*key,document.title,document.source_uri,'text/plain' if path.suffix.lower()=='.txt' else 'text/markdown',document.content_sha256,Jsonb(document.metadata)))
            for c in chunks:
                connection.execute('''INSERT INTO rag.document_chunks
                    (tenant_id,chunk_id,document_id,document_revision,ordinal,text_content,locator,chunker_version,content_sha256,metadata)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (c.tenant_id,c.chunk_id,c.document_id,c.document_revision,c.ordinal,c.text,c.locator,c.chunker_version,c.content_sha256,Jsonb(c.metadata)))
        connection.execute('UPDATE rag.documents SET is_current=false WHERE tenant_id=%s AND document_id=%s AND is_current',key[:2])
        connection.execute("UPDATE rag.documents SET is_current=true,status='ready' WHERE tenant_id=%s AND document_id=%s AND revision=%s",key)
    return {'document_id':document.document_id,'revision':document.revision,'source':document.source_uri,
            'chunks':len(chunks),'reused':bool(existing)}
