import tempfile
from pathlib import Path
import unittest
import uuid
import psycopg
from psycopg import sql
from backend.rag.ingestion import LocalDocumentLoader
from backend.rag.chunking import ChunkConfig
from backend.rag.storage import ingest
from scripts.migrate import connect_kwargs, apply_migrations


class IngestionStorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kwargs=connect_kwargs();cls.name='eac_test_'+uuid.uuid4().hex
        with psycopg.connect(**cls.kwargs,autocommit=True) as c:
            c.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.addClassCleanup(cls.drop)
        cls.c=psycopg.connect(**{**cls.kwargs,'dbname':cls.name},autocommit=True)
        cls.addClassCleanup(cls.c.close);apply_migrations(cls.c)

    @classmethod
    def drop(cls):
        with psycopg.connect(**cls.kwargs,autocommit=True) as c:
            c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.path=Path('example.md')
        self.loader=LocalDocumentLoader(self.root,'test',ChunkConfig(max_chars=80,overlap_chars=10))
        self.tenant=uuid.uuid4().hex
        (self.root/self.path).write_text('# Synthetic\n\n'+('English 数据。 '*30),encoding='utf8')

    def run_ingest(self):return ingest(self.c,self.loader,self.path,tenant_id=self.tenant)

    def test_repeated_changed_and_reverted_source(self):
        first=self.run_ingest();again=self.run_ingest()
        self.assertFalse(first['reused']);self.assertTrue(again['reused'])
        self.assertEqual(first['revision'],again['revision'])
        original=(self.root/self.path).read_bytes()
        (self.root/self.path).write_text('# Updated\n\nNew synthetic data.')
        changed=self.run_ingest()
        self.assertEqual(first['document_id'],changed['document_id'])
        self.assertNotEqual(first['revision'],changed['revision'])
        rows=self.c.execute('SELECT revision,is_current FROM rag.documents WHERE tenant_id=%s',(self.tenant,)).fetchall()
        self.assertEqual(dict(rows),{first['revision']:False,changed['revision']:True})
        self.assertEqual(self.c.execute('SELECT count(*) FROM rag.document_chunks WHERE tenant_id=%s',(self.tenant,)).fetchone()[0],first['chunks']+changed['chunks'])
        (self.root/self.path).write_bytes(original)
        self.assertTrue(self.run_ingest()['reused'])
        self.assertEqual(self.c.execute('SELECT count(*) FROM rag.documents WHERE tenant_id=%s',(self.tenant,)).fetchone()[0],2)
        self.assertEqual(self.c.execute('SELECT count(*) FROM rag.chunk_embeddings').fetchone()[0],0)

    def test_failed_transaction_preserves_previous_current(self):
        first=self.run_ingest()
        (self.root/self.path).write_text('New text')
        # Inject a DB failure after document creation; whole ingest must roll back.
        self.c.execute("CREATE FUNCTION rag.reject_test_chunk() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected failure'; END $$")
        self.c.execute('CREATE TRIGGER reject_test_chunk BEFORE INSERT ON rag.document_chunks FOR EACH ROW EXECUTE FUNCTION rag.reject_test_chunk()')
        try:
            with self.assertRaises(psycopg.Error):self.run_ingest()
        finally:
            self.c.execute('DROP TRIGGER reject_test_chunk ON rag.document_chunks')
            self.c.execute('DROP FUNCTION rag.reject_test_chunk()')
        self.assertEqual(self.c.execute('SELECT revision,status,is_current FROM rag.documents WHERE tenant_id=%s',(self.tenant,)).fetchall(),[(first['revision'],'ready',True)])

    def test_corrupted_revision_rejected_and_delete_cascades(self):
        self.run_ingest()
        self.c.execute("UPDATE rag.document_chunks SET text_content='tampered' WHERE tenant_id=%s AND ordinal=0",(self.tenant,))
        with self.assertRaisesRegex(ValueError,'integrity mismatch'):self.run_ingest()
        self.c.execute('DELETE FROM rag.documents WHERE tenant_id=%s',(self.tenant,))
        self.assertEqual(self.c.execute('SELECT count(*) FROM rag.document_chunks WHERE tenant_id=%s',(self.tenant,)).fetchone()[0],0)

    def test_config_change_and_current_unique(self):
        first=self.run_ingest()
        self.loader=LocalDocumentLoader(self.root,'test',ChunkConfig(max_chars=100,overlap_chars=10))
        second=self.run_ingest()
        self.assertNotEqual(first['revision'],second['revision'])
        with self.assertRaises(psycopg.errors.UniqueViolation):
            self.c.execute('UPDATE rag.documents SET is_current=true WHERE tenant_id=%s',(self.tenant,))
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            self.c.execute("UPDATE rag.documents SET revision='new-id' WHERE tenant_id=%s AND revision=%s",(self.tenant,second['revision']))

    def test_concurrent_repeat_serializes(self):
        from concurrent.futures import ThreadPoolExecutor
        def work():
            with psycopg.connect(**{**self.kwargs,'dbname':self.name},autocommit=True) as c:
                return ingest(c,self.loader,self.path,tenant_id=self.tenant)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: work(),range(2)))
        self.assertEqual(sorted(r['reused'] for r in results),[False,True])
        self.assertEqual(self.c.execute('SELECT count(*) FROM rag.documents WHERE tenant_id=%s',(self.tenant,)).fetchone()[0],1)

    def test_empty_source_does_not_replace_current_revision(self):
        first=self.run_ingest()
        (self.root/self.path).write_text('   ')
        with self.assertRaisesRegex(ValueError,'empty_document'): self.run_ingest()
        self.assertEqual(self.c.execute('SELECT revision FROM rag.documents WHERE tenant_id=%s AND is_current',(self.tenant,)).fetchall(),[(first['revision'],)])
