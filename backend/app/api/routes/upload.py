"""
Upload API — chunked multipart video upload with validation.

Flow:
  1. POST /upload/init → returns job_id + chunk info
  2. POST /upload/chunk/{job_id} → upload chunks (streaming)
  3. POST /upload/complete/{job_id} → assemble + enqueue
"""
import hashlib
import logging
import math
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.models import TranscriptionJob, JobStatus, MediaType
from app.schemas.schemas import UploadInitRequest, UploadInitResponse, ChunkUploadResponse
from app.workers.tasks import process_video_job

router = APIRouter(prefix="/upload")
logger = logging.getLogger(__name__)

UPLOAD_TEMP_DIR = os.path.join(settings.LOCAL_STORAGE_PATH, "temp_uploads")
os.makedirs(UPLOAD_TEMP_DIR, exist_ok=True)


@router.post("/init", response_model=UploadInitResponse)
async def init_upload(
    request: UploadInitRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Initialize a chunked upload session.
    Returns a job_id and the number of chunks to send.
    """
    max_size = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    
    if request.file_size > max_size:
        raise HTTPException(
            status_code=413,
            detail=f"File size {request.file_size / 1024 / 1024:.0f}MB exceeds maximum {settings.MAX_UPLOAD_SIZE_MB}MB"
        )
    
    ext = request.filename.rsplit(".", 1)[-1].lower()
    if ext not in settings.ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"File type '.{ext}' not supported."
        )
    
    job_id = uuid.uuid4()
    
    # Create DB job record
    job = TranscriptionJob(
        id=job_id,
        original_filename=request.filename,
        file_size_bytes=request.file_size,
        file_extension=ext,
        status=JobStatus.UPLOADING,
        media_type=request.media_type,
        language=request.language,
        enable_diarization=request.enable_diarization,
        enable_translation=request.enable_translation,
        target_language=request.target_language,
        whisper_model=request.whisper_model or settings.WHISPER_MODEL,
    )
    db.add(job)
    await db.commit()
    
    chunk_size = settings.CHUNK_SIZE_BYTES
    total_chunks = math.ceil(request.file_size / chunk_size)
    
    # Create chunk staging directory
    staging_dir = os.path.join(UPLOAD_TEMP_DIR, str(job_id))
    os.makedirs(staging_dir, exist_ok=True)
    
    logger.info(f"Upload initialized: job={job_id}, file={request.filename}, chunks={total_chunks}")
    
    return UploadInitResponse(
        job_id=job_id,
        upload_url=f"/api/v1/upload/chunk/{job_id}",
        chunk_size=chunk_size,
        total_chunks=total_chunks,
    )


@router.post("/chunk/{job_id}", response_model=ChunkUploadResponse)
async def upload_chunk(
    job_id: uuid.UUID,
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept a single file chunk and save to staging directory.
    Client sends chunks sequentially or in parallel.
    """
    from sqlalchemy import select
    
    result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != JobStatus.UPLOADING:
        raise HTTPException(status_code=409, detail=f"Job is not in upload state (current: {job.status})")
    
    staging_dir = os.path.join(UPLOAD_TEMP_DIR, str(job_id))
    if not os.path.exists(staging_dir):
        raise HTTPException(status_code=400, detail="Upload session expired. Please restart.")
    
    chunk_path = os.path.join(staging_dir, f"chunk_{chunk_index:06d}")
    
    # Stream chunk to disk to handle large files without memory pressure
    chunk_size_written = 0
    with open(chunk_path, "wb") as f:
        while True:
            data = await file.read(65536)  # 64KB read buffer
            if not data:
                break
            f.write(data)
            chunk_size_written += len(data)
    
    # Count received chunks
    received = len([
        name for name in os.listdir(staging_dir)
        if name.startswith("chunk_")
    ])
    
    logger.debug(f"Chunk {chunk_index}/{total_chunks} received for job {job_id} ({chunk_size_written} bytes)")
    
    return ChunkUploadResponse(
        job_id=job_id,
        chunk_index=chunk_index,
        received_chunks=received,
        total_chunks=total_chunks,
        complete=received >= total_chunks,
    )


@router.post("/complete/{job_id}")
async def complete_upload(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Assemble all chunks into a final file and enqueue the transcription job.
    """
    from sqlalchemy import select
    
    result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    staging_dir = os.path.join(UPLOAD_TEMP_DIR, str(job_id))
    if not os.path.exists(staging_dir):
        raise HTTPException(status_code=400, detail="Upload session not found")
    
    chunks = sorted([
        f for f in os.listdir(staging_dir)
        if f.startswith("chunk_")
    ])
    
    if not chunks:
        raise HTTPException(status_code=400, detail="No chunks received")
    
    # Assemble final file
    final_dir = os.path.join(settings.LOCAL_STORAGE_PATH, "videos")
    os.makedirs(final_dir, exist_ok=True)
    
    final_filename = f"{job_id}.{job.file_extension}"
    final_path = os.path.join(final_dir, final_filename)
    
    logger.info(f"Assembling {len(chunks)} chunks for job {job_id}")
    
    with open(final_path, "wb") as outfile:
        for chunk_name in chunks:
            chunk_path = os.path.join(staging_dir, chunk_name)
            with open(chunk_path, "rb") as chunk_file:
                while True:
                    data = chunk_file.read(65536)
                    if not data:
                        break
                    outfile.write(data)
    
    # Verify file size
    actual_size = os.path.getsize(final_path)
    logger.info(f"File assembled: {final_path} ({actual_size / 1024 / 1024:.1f} MB)")
    
    # Update job
    job.storage_path = final_path
    job.status = JobStatus.QUEUED
    await db.commit()
    
    # Enqueue Celery task
    task = process_video_job.apply_async(
        args=[str(job_id)],
        queue="transcription",
        task_id=str(uuid.uuid4()),
    )
    
    job.celery_task_id = task.id
    await db.commit()
    
    # Cleanup staging dir async
    import shutil
    try:
        shutil.rmtree(staging_dir, ignore_errors=True)
    except Exception:
        pass
    
    logger.info(f"Job {job_id} enqueued as Celery task {task.id}")
    
    return {
        "job_id": str(job_id),
        "status": "queued",
        "task_id": task.id,
        "message": "Upload complete. Transcription job queued.",
    }


@router.delete("/cancel/{job_id}")
async def cancel_upload(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Cancel an in-progress upload or job."""
    from sqlalchemy import select
    
    result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    cancellable_statuses = {JobStatus.UPLOADING, JobStatus.QUEUED, JobStatus.PENDING}
    if job.status not in cancellable_statuses:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel job in status: {job.status}"
        )
    
    # Revoke Celery task if queued
    if job.celery_task_id:
        from app.workers.celery_app import celery_app
        celery_app.control.revoke(job.celery_task_id, terminate=False)
    
    job.status = JobStatus.CANCELLED
    await db.commit()
    
    # Cleanup staging files
    staging_dir = os.path.join(UPLOAD_TEMP_DIR, str(job_id))
    import shutil
    shutil.rmtree(staging_dir, ignore_errors=True)
    
    return {"job_id": str(job_id), "status": "cancelled"}
