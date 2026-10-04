"""Operator-only CLI; trusted root/tenant/namespace, never exposed through /chat."""
import argparse
import json
from pathlib import Path
import psycopg
from backend.rag.ingestion import LocalDocumentLoader, IngestionError
from backend.rag.chunking import ChunkConfig
from backend.rag.storage import ingest
from scripts.migrate import connect_kwargs

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--namespace',required=True)
    parser.add_argument('--tenant',required=True)
    parser.add_argument('--max-chars',type=int,default=600)
    parser.add_argument('--overlap-chars',type=int,default=80)
    parser.add_argument('path',type=Path)
    args=parser.parse_args()
    try:
        config=ChunkConfig(max_chars=args.max_chars,overlap_chars=args.overlap_chars)
        loader=LocalDocumentLoader(args.root,args.namespace,config)
        with psycopg.connect(**connect_kwargs(),autocommit=True) as connection:
            print(json.dumps(ingest(connection,loader,args.path,tenant_id=args.tenant)))
    except (ValueError, OSError, psycopg.Error) as error:
        parser.exit(1, f'Ingestion failed: {str(error) if isinstance(error, IngestionError) else type(error).__name__}\n')
