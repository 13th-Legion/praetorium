"""Health check endpoints."""

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database import async_session

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def health():
    """Liveness ping — cheap, never touches the DB."""
    return {"status": "ok", "service": "praetorium"}


@router.get("/health/ready")
async def readiness():
    """Readiness probe — verifies the DB is reachable (SELECT 1).

    Returns 503 when the database is down or the schema is unusable, so the
    deploy health gate and Uptime Kuma detect a wedged-but-listening app
    (a static /health can't).
    """
    try:
        async with async_session() as db:
            await db.execute(text("SELECT 1"))
        return {"status": "ready", "db": "ok"}
    except Exception as e:
        logger.exception("Readiness probe failed")
        return JSONResponse(
            {"status": "not_ready", "db": "error", "detail": str(e)[:200]},
            status_code=503,
        )
