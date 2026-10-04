"""Operator-only explicit document revision embedding. Makes paid API requests."""
import argparse
import json
import logging
import psycopg
from scripts.migrate import connect_kwargs
from backend.rag.openai_embedding import OpenAIEmbeddingProvider, load_embedding_settings
from backend.rag.embedding_store import persist_embeddings
from backend.rag.embedding import EmbeddingError

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tenant',required=True);p.add_argument('--document-id',required=True);p.add_argument('--revision',required=True)
    a=p.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        provider=OpenAIEmbeddingProvider(load_embedding_settings())
        with psycopg.connect(**connect_kwargs(),autocommit=True) as c:
            print(json.dumps(persist_embeddings(c,provider,tenant_id=a.tenant,document_id=a.document_id,revision=a.revision)))
    except (ValueError,psycopg.Error) as error:
        p.exit(1,'Embedding failed: '+(str(error) if isinstance(error,EmbeddingError) else type(error).__name__)+'\n')
