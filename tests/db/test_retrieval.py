import tempfile
from pathlib import Path
import unittest
import uuid
import psycopg
from psycopg import sql
from backend.rag.ingestion import LocalDocumentLoader
from backend.rag.storage import ingest
from backend.rag.embedding import EmbeddingBatch,EmbeddingError
from backend.rag.models import EmbeddingProfile,AccessScope
from backend.rag.embedding_store import persist_embeddings,profile_id
from backend.rag.retrieval import retrieve,RetrievalConfig
from scripts.migrate import connect_kwargs,apply_migrations


class FakeProvider:
    def __init__(self):
        self.profile=EmbeddingProfile('fake','semantic-fixture','v1',3,'fixture-v1')
        self.calls=0;self.error=False;self.mismatch=False;self.hook=None
    def vector(self,text):
        if 'revenue' in text.lower():return (1.,0.,0.)
        if 'staff' in text.lower():return (0.,1.,0.)
        return (0.,0.,1.)
    def embed_documents(self,texts):
        return EmbeddingBatch(tuple(self.vector(t) for t in texts),self.profile,0,0,0,'0',True)
    def embed_query(self,text):
        self.calls+=1
        if self.error:raise EmbeddingError('fake_provider_error')
        if self.hook:self.hook()
        profile=EmbeddingProfile('wrong','wrong','v1',3,'fixture-v1') if self.mismatch else self.profile
        return EmbeddingBatch((self.vector(text),),profile,0,0,0,'0',True)


class ExactRetrievalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kwargs=connect_kwargs();cls.name='eac_test_'+uuid.uuid4().hex
        with psycopg.connect(**cls.kwargs,autocommit=True) as c:c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.addClassCleanup(cls.drop)
        cls.c=psycopg.connect(**{**cls.kwargs,'dbname':cls.name},autocommit=True)
        cls.addClassCleanup(cls.c.close);apply_migrations(cls.c)
    @classmethod
    def drop(cls):
        with psycopg.connect(**cls.kwargs,autocommit=True) as c:c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.tenant=uuid.uuid4().hex
        self.loader=LocalDocumentLoader(self.root,'retrieval-test');self.provider=FakeProvider();self.docs=[]
        for filename,text in [('a.txt','Revenue is a synthetic metric.'),('b.txt','Revenue definition for another document.'),('c.txt','Staff count is a synthetic metric.')]:
            (self.root/filename).write_text(text)
            d=ingest(self.c,self.loader,Path(filename),tenant_id=self.tenant);self.docs.append(d)
            persist_embeddings(self.c,self.provider,tenant_id=self.tenant,document_id=d['document_id'],revision=d['revision'])
        self.scope=AccessScope(self.tenant,tuple(d['document_id'] for d in self.docs))
    def search(self,query='revenue',config=RetrievalConfig(top_k=5,max_distance=0.2),scope=None):
        return retrieve(self.c,self.provider,query,scope=scope or self.scope,config=config)

    def test_relevant_multi_document_source_rank_and_distance(self):
        rows=self.search()['results']
        self.assertEqual({r['document_id'] for r in rows},{d['document_id'] for d in self.docs[:2]})
        self.assertEqual([r['rank'] for r in rows],[1,2])
        for row in rows:
            self.assertEqual(row['distance'],0)
            self.assertTrue(row['chunk_id']);self.assertIn('Revenue',row['content'])
            self.assertIn('locator',row['source_metadata']);self.assertIn('source_uri',row['source_metadata'])

    def test_unrelated_threshold_returns_empty(self):
        self.assertEqual(self.search('weather')['results'],[])

    def test_top_k_and_threshold_inclusive(self):
        self.assertEqual(len(self.search(config=RetrievalConfig(1,0))['results']),1)
        rows=self.search(config=RetrievalConfig(3,1))['results']
        self.assertEqual(len(rows),3);self.assertEqual(rows[-1]['distance'],1)
        self.assertEqual(len(self.search(config=RetrievalConfig(3,None))['results']),3)

    def test_stable_tie_order(self):
        rows=self.search()['results']
        expected=sorted((r['document_id'],r['chunk_id']) for r in rows)
        self.assertEqual([(r['document_id'],r['chunk_id']) for r in rows],expected)
        self.assertEqual(rows,self.search()['results'])

    def test_old_revision_not_returned_even_if_embedded(self):
        old=self.docs[0]
        (self.root/'a.txt').write_text('Replacement source with no vectors yet.')
        ingest(self.c,self.loader,Path('a.txt'),tenant_id=self.tenant)
        self.assertNotIn(old['document_id'],[r['document_id'] for r in self.search()['results']])

    def test_scope_isolation_and_empty_no_api(self):
        scope=AccessScope(self.tenant,(self.docs[0]['document_id'],))
        self.assertEqual(len(self.search(scope=scope)['results']),1)
        calls=self.provider.calls
        for scope in [AccessScope('other',self.scope.allowed_document_ids),AccessScope(self.tenant,()),AccessScope(self.tenant,(str(uuid.uuid4()),))]:
            self.assertEqual(self.search(scope=scope)['results'],[])
        self.assertEqual(self.provider.calls,calls)

    def test_missing_profile_no_fallback(self):
        self.provider.profile=EmbeddingProfile('fake','not-indexed','v1',3,'fixture-v1')
        result=self.search()
        self.assertEqual(result['results'],[]);self.assertIsNone(result['query_embedding']);self.assertEqual(self.provider.calls,0)

    def test_query_profile_mismatch_rejected(self):
        self.provider.mismatch=True
        with self.assertRaisesRegex(EmbeddingError,'profile_mismatch'):self.search()

    def test_stored_profile_mismatch_rejected_before_api(self):
        pid=profile_id(self.provider.profile)
        with self.c.transaction(force_rollback=True):
            self.c.execute("UPDATE rag.embedding_profiles SET model_revision='corrupt' WHERE profile_id=%s",(pid,))
            with self.assertRaisesRegex(EmbeddingError,'stored_profile_mismatch'):self.search()
        self.assertEqual(self.provider.calls,0)

    def test_errors_do_not_write_database(self):
        before=self.c.execute('SELECT count(*) FROM rag.chunk_embeddings').fetchone()
        self.provider.error=True
        with self.assertRaisesRegex(EmbeddingError,'fake_provider_error'):self.search()
        self.assertEqual(before,self.c.execute('SELECT count(*) FROM rag.chunk_embeddings').fetchone())
        self.provider.error=False
        self.search()
        self.assertEqual(before,self.c.execute('SELECT count(*) FROM rag.chunk_embeddings').fetchone())

    def test_source_changed_during_query_uses_current_snapshot(self):
        def change():
            (self.root/'a.txt').write_text('New unembedded revision.')
            ingest(self.c,self.loader,Path('a.txt'),tenant_id=self.tenant)
        self.provider.hook=change
        rows=self.search()['results']
        self.assertEqual([r['document_id'] for r in rows],[self.docs[1]['document_id']])

    def test_corrupt_chunk_fails_closed(self):
        self.c.execute("UPDATE rag.document_chunks SET text_content='corrupted' WHERE tenant_id=%s",(self.tenant,))
        with self.assertRaisesRegex(EmbeddingError,'integrity_mismatch'):self.search()

    def test_invalid_query_before_provider(self):
        for query in ['', ' ',None,'a'*8001]:
            with self.assertRaises(EmbeddingError):self.search(query)
        self.assertEqual(self.provider.calls,0)
