-- Source/config revisions are immutable snapshots; one current snapshot per document.
ALTER TABLE rag.documents ADD COLUMN is_current boolean NOT NULL DEFAULT false;
CREATE UNIQUE INDEX documents_one_current
    ON rag.documents (tenant_id, document_id) WHERE is_current;
CREATE UNIQUE INDEX documents_source_revision
    ON rag.documents (tenant_id, source_uri, revision);
-- Existing chunk UNIQUE (tenant,document,revision,ordinal) already indexes parent FK.
-- Default ON UPDATE NO ACTION preserves identity; deletions cascade to chunks/vectors.
