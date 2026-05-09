"""Health and readiness check endpoints."""
import time
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.database import get_db
from app.core.config import settings

router = APIRouter()
_start_time = time.time()


@router.get("/health")
async def health_check():
    """Basic liveness check."""
    return {
        "status": "ok",
        "version": "1.0.0",
        "uptime_seconds": round(time.time() - _start_time),
    }


@router.get("/health/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Readiness check — verifies DB + Redis connectivity."""
    checks = {}

    # Database
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"

    # Redis
    try:
        from app.core.redis import get_redis
        r = get_redis()
        await r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"

    # GPU
    try:
        import torch
        checks["gpu"] = f"cuda:{torch.cuda.device_count()}" if torch.cuda.is_available() else "cpu-only"
    except ImportError:
        checks["gpu"] = "torch-not-installed"

    all_ok = all(v == "ok" for v in checks.values() if "gpu" not in "gpu")
    return {
        "status": "ready" if all_ok else "degraded",
        "checks": checks,
        "whisper_model": settings.WHISPER_MODEL,
        "device": settings.WHISPER_DEVICE,
    }
