import psycopg
from fastapi import FastAPI, HTTPException

from backend.database import check_database

app = FastAPI()


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
