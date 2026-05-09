"""
Jobs API — job status, real-time progress via SSE, and job management.
"""
import asyncio
import json
import logging
from typing import Optional, AsyncGenerator
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.models import TranscriptionJob, JobStatus
from app.schemas.schemas import JobSummary, JobDetail, PaginatedResponse

router = APIRouter(prefix="/jobs")
logger = logging.getLogger(__name__)


@router.get("", response_model=PaginatedResponse)
async def list_jobs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: Optional[JobStatus] = None,
    db: AsyncSession = Depends(get_db),
):
    """List all transcription jobs with pagination."""
    query = select(TranscriptionJob).order_by(desc(TranscriptionJob.created_at))
    
    if status:
        query = query.where(TranscriptionJob.status == status)
    
    # Total count
    count_result = await db.execute(
        select(func.count()).select_from(
            query.subquery()
        )
    )
    total = count_result.scalar()
    
    # Paginated results
    offset = (page - 1) * page_size
    result = await db.execute(query.offset(offset).limit(page_size))
    jobs = result.scalars().all()
    
    return PaginatedResponse(
        items=[JobSummary.model_validate(job) for job in jobs],
        total=total,
        page=page,
        page_size=page_size,
        pages=(total + page_size - 1) // page_size,
    )


@router.get("/{job_id}", response_model=JobDetail)
async def get_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get detailed job information."""
    result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return JobDetail.model_validate(job)


@router.get("/{job_id}/progress")
async def job_progress_sse(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Server-Sent Events (SSE) endpoint for real-time job progress.
    
    The client keeps this connection open. The server pushes progress
    events as the Celery worker publishes to Redis pub/sub.
    
    Usage (JavaScript):
        const evtSource = new EventSource(`/api/v1/jobs/${jobId}/progress`);
        evtSource.onmessage = (e) => {
          const data = JSON.parse(e.data);
          updateProgress(data.progress, data.stage);
        };
    """
    # Verify job exists
    result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    async def event_generator() -> AsyncGenerator[str, None]:
        """Stream progress events from Redis pub/sub."""
        import aioredis
        
        redis = await aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis.pubsub()
        channel = f"job_progress:{job_id}"
        
        await pubsub.subscribe(channel)
        
        # Send last known state immediately (for reconnecting clients)
        last_state = await redis.get(f"job_state:{job_id}")
        if last_state:
            yield f"data: {last_state}\n\n"
        
        # Stream until job completes or times out
        timeout_seconds = settings.JOB_TIMEOUT_SECONDS + 300
        elapsed = 0
        poll_interval = 0.5  # 500ms
        
        try:
            while elapsed < timeout_seconds:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=poll_interval,
                )
                
                if message and message["type"] == "message":
                    data = message["data"]
                    yield f"data: {data}\n\n"
                    
                    # Check if job is in terminal state
                    try:
                        parsed = json.loads(data)
                        if parsed.get("status") in ("completed", "failed", "cancelled"):
                            yield f"event: done\ndata: {data}\n\n"
                            break
                    except json.JSONDecodeError:
                        pass
                
                # Heartbeat every 15 seconds to keep connection alive
                if int(elapsed) % 15 == 0 and elapsed > 0:
                    yield f": heartbeat\n\n"
                
                elapsed += poll_interval
                await asyncio.sleep(0)  # Yield to event loop
                
        except asyncio.CancelledError:
            logger.debug(f"SSE connection closed for job {job_id}")
        finally:
            await pubsub.unsubscribe(channel)
            await redis.close()
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable Nginx buffering
            "Connection": "keep-alive",
        },
    )


@router.delete("/{job_id}")
async def delete_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Delete a job and all associated data."""
    result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Cancel if running
    if job.celery_task_id and job.status in (
        JobStatus.QUEUED, JobStatus.TRANSCRIBING, JobStatus.EXTRACTING_AUDIO
    ):
        from app.workers.celery_app import celery_app
        celery_app.control.revoke(job.celery_task_id, terminate=True)
    
    # Delete files
    import os
    for path in [job.storage_path, job.audio_path]:
        if path and os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
    
    await db.delete(job)
    await db.commit()
    
    return {"message": f"Job {job_id} deleted successfully"}
