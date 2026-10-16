"""Separate default-disabled RAG endpoint; existing /chat is unchanged."""
import logging
import time
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
import psycopg
from backend.llm import ProviderError
from backend.rag.embedding import EmbeddingError
from backend.rag.response import RagResponse
from backend.rag.runtime import authorized_release,get_service

router=APIRouter();logger=logging.getLogger('uvicorn.error')
class RagRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    query:str=Field(min_length=1,max_length=8000)
    @field_validator('query')
    @classmethod
    def nonblank(cls,v):
        if not v.strip():raise ValueError('Query must not be blank')
        return v

def enabled():
    if not authorized_release():raise HTTPException(503,detail={'code':'rag_disabled','message':'RAG is not enabled'})

@router.post('/rag/chat',response_model=RagResponse,dependencies=[Depends(enabled)])
def rag_chat(request:RagRequest,service=Depends(get_service)):
    started=time.monotonic()
    try:return service.run(request.query)
    except ProviderError as error:
        code=error.code;status=error.status_code
    except psycopg.Error:
        code='rag_database_unavailable';status=503
    except EmbeddingError:
        code='rag_embedding_failed';status=502
    except (ValueError,KeyError):
        code='rag_configuration_or_integrity_error';status=503
    logger.info('rag_request_error %s',{'status':'error','code':code,'latency_ms':round((time.monotonic()-started)*1000)})
    raise HTTPException(status,detail={'code':code,'message':'RAG request could not be completed'})
