import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.llm import ChatResult, LLMProvider, ProviderError, get_provider

from backend.database import check_database
from backend.config import MAX_SYSTEM_CHARACTERS, MAX_USER_CHARACTERS

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
    model_config = ConfigDict(extra="forbid")
    user_message: str = Field(min_length=1, max_length=MAX_USER_CHARACTERS,
                              validation_alias=AliasChoices("user_message", "message"))
    system_message: str | None = Field(default=None, min_length=1, max_length=MAX_SYSTEM_CHARACTERS)

    @model_validator(mode="before")
    @classmethod
    def no_ambiguous_user_message(cls, data):
        if isinstance(data, dict) and "message" in data and "user_message" in data:
            raise ValueError("Use user_message or legacy message, not both")
        return data

    @field_validator("user_message", "system_message")
    @classmethod
    def non_blank(cls, value):
        if value is not None and not value.strip():
            raise ValueError("Message must not be blank")
        return value


@app.post("/chat", response_model=ChatResult)
def chat(request: ChatRequest, provider: LLMProvider = Depends(get_provider)) -> ChatResult:
    try:
        return provider.chat(request.user_message, system_message=request.system_message)
    except ProviderError as error:
        raise HTTPException(status_code=error.status_code, detail={"code": error.code, "message": str(error)}) from None
