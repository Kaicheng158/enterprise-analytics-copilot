"""Loopback-only static console for the constrained local Agent product slice."""

from ipaddress import ip_address
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse


FRONTEND_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "agent"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client is not None else ""
    try:
        allowed = ip_address(host).is_loopback
    except ValueError:
        allowed = False
    if not allowed:
        raise HTTPException(status_code=403, detail="Local console is loopback-only")


def add_local_agent_console(app: FastAPI) -> None:
    @app.get("/agent", include_in_schema=False)
    def page(request: Request):
        _require_loopback(request)
        return FileResponse(
            FRONTEND_ROOT / "index.html",
            media_type="text/html",
            headers=SECURITY_HEADERS,
        )

    @app.get("/agent/app.js", include_in_schema=False)
    def script(request: Request):
        _require_loopback(request)
        return FileResponse(
            FRONTEND_ROOT / "app.js",
            media_type="text/javascript",
            headers=SECURITY_HEADERS,
        )

    @app.get("/agent/styles.css", include_in_schema=False)
    def styles(request: Request):
        _require_loopback(request)
        return FileResponse(
            FRONTEND_ROOT / "styles.css",
            media_type="text/css",
            headers=SECURITY_HEADERS,
        )


__all__ = ["add_local_agent_console"]
