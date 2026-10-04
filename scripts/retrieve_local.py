"""Operator-only exact retrieval; no /chat route and no generation."""
import argparse
import json
import logging
import psycopg
from backend.rag.embedding import EmbeddingError
from backend.rag.models import AccessScope
from backend.rag.openai_embedding import OpenAIEmbeddingProvider,load_embedding_settings
from backend.rag.retrieval import retrieve,RetrievalConfig
from scripts.migrate import connect_kwargs

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--tenant',required=True)
    p.add_argument('--document-id',action='append',required=True,help='Operator-approved document; repeat for multiple documents')
    p.add_argument('--query',required=True)
    p.add_argument('--top-k',type=int,default=5)
    p.add_argument('--max-distance',type=float,default=None,help='Inclusive maximum cosine distance; omitted means no relevance cutoff')
    args=p.parse_args();logging.basicConfig(level=logging.INFO)
    try:
        config=RetrievalConfig(args.top_k,args.max_distance)
        provider=OpenAIEmbeddingProvider(load_embedding_settings())
        with psycopg.connect(**connect_kwargs(),autocommit=True) as c:
            print(json.dumps(retrieve(c,provider,args.query,scope=AccessScope(args.tenant,tuple(args.document_id)),config=config),ensure_ascii=False))
    except (ValueError,psycopg.Error) as error:
        p.exit(1,'Retrieval failed: '+(str(error) if isinstance(error,EmbeddingError) else type(error).__name__)+'\n')
