"""Explicit DB tests: python -m unittest discover -s tests/db -v.
Creates/drops only a unique temporary database. No production fixture rows.
"""
import tempfile
import unittest
import uuid
from pathlib import Path
import psycopg
from psycopg import sql
from scripts.migrate import connect_kwargs, apply_migrations, ROOT


class StorageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kwargs = connect_kwargs()
        cls.name = 'eac_test_' + uuid.uuid4().hex
        with psycopg.connect(**cls.kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(cls.name)))
        cls.addClassCleanup(cls.cleanup_database)
        cls.db = psycopg.connect(**{**cls.kwargs, 'dbname': cls.name}, autocommit=True)
        cls.addClassCleanup(cls.db.close)
        apply_migrations(cls.db)

    @classmethod
    def cleanup_database(cls):
        with psycopg.connect(**cls.kwargs, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(cls.name)))

    def test_repeat_is_noop_and_extension_version(self):
        self.assertEqual(apply_migrations(self.db), [])
        self.assertEqual(self.db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0], '0.8.7')

    def test_changed_migration_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for p in (ROOT/'migrations').glob('*.sql'):
                (Path(directory)/p.name).write_bytes(p.read_bytes())
            p=Path(directory)/'001_vector.sql';p.write_text(p.read_text()+'\n-- tampered')
            with self.assertRaises(ValueError): apply_migrations(self.db,directory)

    def test_failed_migration_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            for p in (ROOT/'migrations').glob('*.sql'):
                (Path(directory)/p.name).write_bytes(p.read_bytes())
            (Path(directory)/'003_failure.sql').write_text('CREATE TABLE rag.rollback_probe(id int); SELECT 1/0;')
            with self.assertRaises(psycopg.errors.DivisionByZero): apply_migrations(self.db,directory)
        self.assertIsNone(self.db.execute("SELECT to_regclass('rag.rollback_probe')").fetchone()[0])
        self.assertEqual(self.db.execute('SELECT count(*) FROM app_migrations.applied').fetchone()[0],2)

    def test_vector_storage_constraints_and_cascade(self):
        doc,chunk,profile=[uuid.uuid4() for _ in range(3)]
        with self.db.transaction(force_rollback=True):
            self.db.execute("INSERT INTO rag.documents(tenant_id,document_id,revision,title,source_uri,media_type,content_sha256) VALUES ('test',%s,'1','Synthetic','test://fixture','text/plain',%s)",(doc,'0'*64))
            self.db.execute("INSERT INTO rag.document_chunks(tenant_id,chunk_id,document_id,document_revision,ordinal,text_content,locator,chunker_version,content_sha256) VALUES ('test',%s,%s,'1',0,'Synthetic','line 1','test',%s)",(chunk,doc,'1'*64))
            self.db.execute("INSERT INTO rag.embedding_profiles VALUES (%s,'test','synthetic','1','none',3,'l2')",(profile,))
            insert="INSERT INTO rag.chunk_embeddings VALUES (%s,%s,%s,%s,%s::vector,now())"
            for tenant,dims,vector,error in [('other',3,'[1,0,0]',psycopg.errors.ForeignKeyViolation),('test',2,'[1,0]',psycopg.errors.ForeignKeyViolation),('test',3,'[1,0]',psycopg.errors.CheckViolation),('test',3,'[0,0,0]',psycopg.errors.CheckViolation)]:
                with self.assertRaises(error):
                    with self.db.transaction(): self.db.execute(insert,(tenant,chunk,profile,dims,vector))
            self.db.execute(insert,('test',chunk,profile,3,'[1,0,0]'))
            self.assertEqual(self.db.execute("SELECT embedding <-> '[1,0,0]'::vector, embedding <-> '[0,1,0]'::vector FROM rag.chunk_embeddings").fetchone()[0],0)
            self.assertAlmostEqual(self.db.execute("SELECT embedding <-> '[0,1,0]'::vector FROM rag.chunk_embeddings").fetchone()[0],2**0.5)
            self.db.execute('DELETE FROM rag.documents WHERE document_id=%s',(doc,))
            self.assertEqual(self.db.execute('SELECT count(*) FROM rag.chunk_embeddings').fetchone()[0],0)
