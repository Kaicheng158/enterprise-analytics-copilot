CREATE SCHEMA rag;
REVOKE ALL ON SCHEMA rag FROM PUBLIC;

CREATE TABLE rag.documents (
    tenant_id text NOT NULL CHECK (length(tenant_id) > 0),
    document_id uuid NOT NULL,
    revision text NOT NULL CHECK (length(revision) > 0),
    title text NOT NULL,
    source_uri text NOT NULL,
    media_type text NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
    status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','ready','failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, document_id, revision)
);

CREATE TABLE rag.document_chunks (
    tenant_id text NOT NULL,
    chunk_id uuid NOT NULL,
    document_id uuid NOT NULL,
    document_revision text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    text_content text NOT NULL CHECK (length(text_content) > 0),
    locator text NOT NULL CHECK (length(locator) > 0),
    chunker_version text NOT NULL,
    content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    metadata jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
    PRIMARY KEY (tenant_id, chunk_id),
    UNIQUE (tenant_id, document_id, document_revision, ordinal),
    FOREIGN KEY (tenant_id, document_id, document_revision)
        REFERENCES rag.documents (tenant_id, document_id, revision) ON DELETE CASCADE
);

CREATE TABLE rag.embedding_profiles (
    profile_id uuid PRIMARY KEY,
    provider text NOT NULL,
    model text NOT NULL,
    model_revision text NOT NULL,
    preprocessing_version text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions BETWEEN 1 AND 16000),
    distance_metric text NOT NULL CHECK (distance_metric IN ('cosine','l2','inner_product')),
    UNIQUE (profile_id, dimensions),
    UNIQUE (provider, model, model_revision, preprocessing_version, dimensions, distance_metric)
);

-- No production model/dimension is selected. Validate each row against its profile.
-- No ANN index yet; future indexed partitions/casts require a selected profile.
CREATE TABLE rag.chunk_embeddings (
    tenant_id text NOT NULL,
    chunk_id uuid NOT NULL,
    profile_id uuid NOT NULL,
    dimensions integer NOT NULL,
    embedding public.vector NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, chunk_id, profile_id),
    FOREIGN KEY (tenant_id, chunk_id)
        REFERENCES rag.document_chunks (tenant_id, chunk_id) ON DELETE CASCADE,
    FOREIGN KEY (profile_id, dimensions)
        REFERENCES rag.embedding_profiles (profile_id, dimensions),
    CHECK (public.vector_dims(embedding) = dimensions),
    CHECK (public.vector_norm(embedding) > 0)
);
