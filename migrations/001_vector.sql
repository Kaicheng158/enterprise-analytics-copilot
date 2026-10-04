-- Immutable after application. Extension activation is per database.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public VERSION '0.8.7';
DO $$ BEGIN
    IF (SELECT extversion FROM pg_extension WHERE extname = 'vector') <> '0.8.7' THEN
        RAISE EXCEPTION 'Expected pgvector 0.8.7; review extension upgrade separately';
    END IF;
END $$;
