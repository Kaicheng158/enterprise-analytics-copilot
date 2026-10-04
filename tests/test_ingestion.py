import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from backend.rag.ingestion import LocalDocumentLoader, IngestionError, MAX_FILE_BYTES
from backend.rag.chunking import ChunkConfig, StructureChunker


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config=ChunkConfig(max_chars=80,overlap_chars=10)
        self.loader=LocalDocumentLoader(self.root,'test',self.config)

    def load(self,text,name='sample.md'):
        (self.root/name).write_text(text,encoding='utf-8')
        return self.loader.load(Path(name),tenant_id='synthetic')

    def check_chunks(self,doc):
        chunks=StructureChunker(self.config).split(doc)
        self.assertEqual(chunks,StructureChunker(self.config).split(doc))
        covered=0
        for i,c in enumerate(chunks):
            a,b=c.metadata['char_start'],c.metadata['char_end']
            self.assertEqual(c.text,doc.text[a:b]);self.assertLessEqual(len(c.text),80)
            self.assertEqual(a,0 if i==0 else covered-10)
            self.assertGreater(b,covered)
            covered=b
        self.assertEqual(covered,len(doc.text))
        return chunks

    def test_english_structure_and_exact_overlap(self):
        doc=self.load('# Overview\n\n'+('English sentence. '*3)+'\n\n# Details\n\n'+('Other facts. '*12))
        chunks=self.check_chunks(doc)
        self.assertIn(chunks[0].metadata['char_end'],[len('# Overview\n\n'),doc.text.index('# Details')])
        self.assertEqual(chunks[1].text[:10],chunks[0].text[-10:])

    def test_chinese_unicode_bom_and_line_endings(self):
        doc=self.load('\ufeff# 中文\r\n\r\n'+('指标变化 👩🏽‍💻 café e\u0301。'*30))
        self.assertFalse(doc.text.startswith('\ufeff'))
        self.assertIn('\r\n',doc.text)
        self.check_chunks(doc)

    def test_long_paragraph(self):
        doc=self.load('中a🙂'*1000,'long.txt')
        self.assertGreater(len(self.check_chunks(doc)),30)

    def test_fenced_code_not_treated_as_heading(self):
        doc=self.load('Intro\n\n```text\n# not a heading\n\nexample\n```\n\nEnd')
        chunks=self.check_chunks(doc)
        self.assertEqual(len(chunks),1)
        self.assertIsNone(chunks[0].metadata['section_at_start'])

    def test_identity_source_config_and_tenant(self):
        first=self.load('First text')
        self.assertEqual(first,self.loader.load(Path('sample.md'),tenant_id='synthetic'))
        changed=self.load('Second text')
        self.assertEqual(first.document_id,changed.document_id)
        self.assertNotEqual(first.revision,changed.revision)
        other=LocalDocumentLoader(self.root,'test',ChunkConfig(max_chars=100,overlap_chars=10)).load(Path('sample.md'),tenant_id='synthetic')
        self.assertEqual(other.document_id,changed.document_id)
        self.assertNotEqual(other.revision,changed.revision)
        self.assertNotEqual(changed.document_id,self.loader.load(Path('sample.md'),tenant_id='other').document_id)

    def test_empty_and_invalid_text(self):
        for raw,code in [(b'','empty_document'),(b' \n\t','empty_document'),(b'\xff','invalid_utf8'),(b'abc\x00','nul_character'),(b'x'*(MAX_FILE_BYTES+1),'file_too_large')]:
            with self.subTest(code=code):
                (self.root/'bad.txt').write_bytes(raw)
                with self.assertRaisesRegex(IngestionError,code):self.loader.load(Path('bad.txt'),tenant_id='test')

    def test_paths_symlinks_and_special_file(self):
        (self.root/'safe.txt').write_text('safe')
        (self.root/'link.txt').symlink_to(self.root/'safe.txt')
        (self.root/'linked').symlink_to(self.root,target_is_directory=True)
        os.mkfifo(self.root/'fifo.txt')
        for name in ['../safe.txt','/etc/passwd','link.txt','linked/safe.txt','fifo.txt','missing.txt','.secret.txt','safe.pdf']:
            with self.subTest(name=name),self.assertRaises(IngestionError):
                self.loader.load(Path(name),tenant_id='test')

    def test_read_failure_safe_and_config_validation(self):
        (self.root/'safe.txt').write_text('safe')
        with patch('backend.rag.ingestion.os.read',side_effect=PermissionError('sensitive path')),self.assertRaisesRegex(IngestionError,'^source_unreadable_or_unsafe$'):
            self.loader.load(Path('safe.txt'),tenant_id='test')
        for config in [{'max_chars':0},{'overlap_chars':400},{'max_chars':True},{'algorithm':'unreleased'}]:
            with self.subTest(config=config),self.assertRaises(ValueError):ChunkConfig(**config)

    def test_source_changed_during_read(self):
        target=self.root/'changing.txt';target.write_text('before')
        original=os.read
        changed=False
        def reading(fd,count):
            nonlocal changed
            if not changed:
                target.write_text('changed during read');changed=True
            return original(fd,count)
        with patch('backend.rag.ingestion.os.read',side_effect=reading),self.assertRaisesRegex(IngestionError,'source_changed_during_read'):
            self.loader.load(Path('changing.txt'),tenant_id='test')
