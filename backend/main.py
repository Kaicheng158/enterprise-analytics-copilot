import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from backend.llm import ChatResult, LLMProvider, ProviderError, get_provider

from backend.database import check_database

app = FastAPI()


@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, error: ProviderError):
    return JSONResponse(status_code=error.status_code,
                        content={"detail": {"code": error.code, "message": str(error)}})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/db")
def database_health() -> dict[str, str | int]:
    try:
        result = check_database()
    except psycopg.Error:
        raise HTTPException(status_code=503, detail="Database unavailable") from None
    return {"status": "ok", "database": "connected", "result": result}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)

    @field_validator("message")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message must not be blank")
        return value


@app.post("/chat", response_model=ChatResult)
def chat(request: ChatRequest, provider: LLMProvider = Depends(get_provider)) -> ChatResult:
    try:
        return provider.chat(request.message)
    except ProviderError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)}) from None
