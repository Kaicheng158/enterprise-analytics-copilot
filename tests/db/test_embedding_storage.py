import tempfile
from pathlib import Path
import unittest
import uuid
import psycopg
from psycopg import sql
from backend.rag.ingestion import LocalDocumentLoader
from backend.rag.storage import ingest
from backend.rag.embedding import EmbeddingBatch,EmbeddingError
from backend.rag.models import EmbeddingProfile
from backend.rag.embedding_store import persist_embeddings
from scripts.migrate import connect_kwargs,apply_migrations


class FakeProvider:
    def __init__(self):
        self.profile=EmbeddingProfile('fake','deterministic','test-v1',3,'test-only')
        self.calls=0;self.hook=None;self.bad=False
    def embed_documents(self,texts):
        self.calls+=1
        if self.hook:self.hook()
        return EmbeddingBatch(tuple((1.,float(len(t)),0.) if not self.bad else (1.,) for t in texts),self.profile,0,0,0,'0',True)


class EmbeddingStorageTests(unittest.TestCase):
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
        self.root=Path(self.tmp.name);(self.root/'doc.txt').write_text('Synthetic document for fake embeddings.')
        self.tenant=uuid.uuid4().hex;self.loader=LocalDocumentLoader(self.root,'test')
        self.doc=ingest(self.c,self.loader,Path('doc.txt'),tenant_id=self.tenant)
        self.provider=FakeProvider()
    def embed(self,revision=None,tenant=None):
        return persist_embeddings(self.c,self.provider,tenant_id=tenant or self.tenant,document_id=self.doc['document_id'],revision=revision or self.doc['revision'])
    def rows(self):return self.c.execute('SELECT count(*) FROM rag.chunk_embeddings WHERE tenant_id=%s',(self.tenant,)).fetchone()[0]

    def test_persistence_idempotency_and_dimension(self):
        r=self.embed();self.assertEqual(r['inserted'],1)
        self.assertEqual(self.embed()['inserted'],0);self.assertEqual(self.provider.calls,1)
        self.assertEqual(self.c.execute('SELECT vector_dims(embedding) FROM rag.chunk_embeddings WHERE tenant_id=%s',(self.tenant,)).fetchone()[0],3)

    def test_invalid_vector_atomic_failure(self):
        self.provider.bad=True
        with self.assertRaises(EmbeddingError):self.embed()
        self.assertEqual(self.rows(),0)

    def test_tenant_and_revision_scope_before_api(self):
        with self.assertRaises(EmbeddingError):self.embed(tenant='wrong')
        with self.assertRaises(EmbeddingError):self.embed(revision='wrong')
        self.assertEqual(self.provider.calls,0)

    def test_source_change_during_api_discards_vectors(self):
        def change():
            (self.root/'doc.txt').write_text('Changed synthetic source')
            ingest(self.c,self.loader,Path('doc.txt'),tenant_id=self.tenant)
        self.provider.hook=change
        with self.assertRaisesRegex(EmbeddingError,'document_changed'):self.embed()
        self.assertEqual(self.rows(),0)

    def test_different_profiles_do_not_mix(self):
        first=self.embed()
        self.provider.profile=EmbeddingProfile('fake','another','test-v1',3,'test-only')
        second=self.embed()
        self.assertNotEqual(first['profile_id'],second['profile_id']);self.assertEqual(self.rows(),2)

    def test_source_new_revision_requires_new_vectors(self):
        self.embed()
        (self.root/'doc.txt').write_text('New text')
        nextdoc=ingest(self.c,self.loader,Path('doc.txt'),tenant_id=self.tenant)
        with self.assertRaises(EmbeddingError):self.embed()
        self.assertEqual(self.embed(revision=nextdoc['revision'])['inserted'],1)
        self.assertEqual(self.rows(),2)

    def test_database_failure_leaves_no_partial_vectors(self):
        self.c.execute("CREATE FUNCTION rag.reject_embedding() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test failure'; END $$")
        self.c.execute('CREATE TRIGGER reject_embedding BEFORE INSERT ON rag.chunk_embeddings FOR EACH ROW EXECUTE FUNCTION rag.reject_embedding()')
        try:
            with self.assertRaises(psycopg.Error):self.embed()
        finally:
            self.c.execute('DROP TRIGGER reject_embedding ON rag.chunk_embeddings');self.c.execute('DROP FUNCTION rag.reject_embedding()')
        self.assertEqual(self.rows(),0)

    def test_multiple_batches_and_later_failure_are_atomic(self):
        (self.root/'doc.txt').write_text('Synthetic long paragraph. '*600)
        self.doc=ingest(self.c,self.loader,Path('doc.txt'),tenant_id=self.tenant)
        self.assertGreater(self.doc['chunks'],16)
        original=self.provider.embed_documents
        def failure(texts):
            if self.provider.calls==1:raise EmbeddingError('fake_second_batch_failure')
            return original(texts)
        self.provider.embed_documents=failure
        with self.assertRaises(EmbeddingError):self.embed()
        self.assertEqual(self.rows(),0)
        self.provider.embed_documents=original
        self.provider.calls=0
        result=self.embed()
        self.assertEqual(result['inserted'],self.doc['chunks'])
        self.assertGreater(self.provider.calls,1)
