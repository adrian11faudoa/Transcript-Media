"""
Transcript API — view, edit, search, and export transcripts.
"""
import logging
import os
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import get_db
from app.models.models import TranscriptionJob, Transcript, TranscriptSegment, TranscriptExport, ExportFormat
from app.schemas.schemas import (
    TranscriptResponse, SegmentResponse, SegmentUpdateRequest,
    TranscriptSearchResult, ExportRequest, ExportResponse,
)

router = APIRouter(prefix="/transcripts")
logger = logging.getLogger(__name__)

CONTENT_TYPE_MAP = {
    ExportFormat.TXT: "text/plain; charset=utf-8",
    ExportFormat.SRT: "text/plain; charset=utf-8",
    ExportFormat.VTT: "text/vtt; charset=utf-8",
    ExportFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ExportFormat.PDF: "application/pdf",
    ExportFormat.JSON: "application/json; charset=utf-8",
}

EXTENSION_MAP = {
    ExportFormat.TXT: "txt",
    ExportFormat.SRT: "srt",
    ExportFormat.VTT: "vtt",
    ExportFormat.DOCX: "docx",
    ExportFormat.PDF: "pdf",
    ExportFormat.JSON: "json",
}


@router.get("/{job_id}", response_model=TranscriptResponse)
async def get_transcript(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """Get the full transcript for a job, including all segments."""
    result = await db.execute(
        select(Transcript)
        .where(Transcript.job_id == job_id)
        .options(selectinload(Transcript.segments))
    )
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        # Check if job exists
        job_result = await db.execute(
            select(TranscriptionJob).where(TranscriptionJob.id == job_id)
        )
        job = job_result.scalar_one_or_none()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        raise HTTPException(
            status_code=404,
            detail=f"Transcript not yet available. Job status: {job.status}"
        )
    
    return TranscriptResponse.model_validate(transcript)


@router.patch("/{job_id}/segments/{segment_id}", response_model=SegmentResponse)
async def update_segment(
    job_id: UUID,
    segment_id: UUID,
    update: SegmentUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Update a transcript segment from the editor.
    Supports editing text, speaker name, and timestamps.
    """
    result = await db.execute(
        select(TranscriptSegment)
        .join(Transcript)
        .where(
            TranscriptSegment.id == segment_id,
            Transcript.job_id == job_id,
        )
    )
    segment = result.scalar_one_or_none()
    
    if not segment:
        raise HTTPException(status_code=404, detail="Segment not found")
    
    if update.text is not None:
        segment.text = update.text.strip()
    if update.speaker_name is not None:
        segment.speaker_name = update.speaker_name.strip()
    if update.start_time is not None:
        segment.start_time = update.start_time
    if update.end_time is not None:
        if update.end_time <= (update.start_time or segment.start_time):
            raise HTTPException(status_code=400, detail="end_time must be after start_time")
        segment.end_time = update.end_time
    
    await db.commit()
    await db.refresh(segment)
    
    # Update full_text on transcript
    transcript_result = await db.execute(
        select(Transcript)
        .where(Transcript.id == segment.transcript_id)
        .options(selectinload(Transcript.segments))
    )
    transcript = transcript_result.scalar_one_or_none()
    if transcript:
        transcript.full_text = " ".join(
            seg.text for seg in sorted(transcript.segments, key=lambda s: s.segment_index)
        )
        await db.commit()
    
    return SegmentResponse.model_validate(segment)


@router.get("/{job_id}/search")
async def search_transcript(
    job_id: UUID,
    q: str = Query(..., min_length=1, max_length=500),
    db: AsyncSession = Depends(get_db),
) -> list[TranscriptSearchResult]:
    """
    Search within a transcript's segments.
    Returns matching segments with highlighted text.
    """
    result = await db.execute(
        select(Transcript).where(Transcript.job_id == job_id)
    )
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    
    # Case-insensitive search
    segments_result = await db.execute(
        select(TranscriptSegment)
        .where(
            TranscriptSegment.transcript_id == transcript.id,
            TranscriptSegment.text.ilike(f"%{q}%"),
        )
        .order_by(TranscriptSegment.start_time)
        .limit(100)
    )
    segments = segments_result.scalars().all()
    
    results = []
    for seg in segments:
        # Create highlighted version
        text_lower = seg.text.lower()
        query_lower = q.lower()
        
        highlighted = seg.text
        idx = text_lower.find(query_lower)
        if idx != -1:
            end_idx = idx + len(q)
            highlighted = (
                seg.text[:idx] +
                f"<mark>{seg.text[idx:end_idx]}</mark>" +
                seg.text[end_idx:]
            )
        
        results.append(TranscriptSearchResult(
            segment_id=seg.id,
            job_id=job_id,
            start_time=seg.start_time,
            end_time=seg.end_time,
            text=seg.text,
            speaker_name=seg.speaker_name or seg.speaker_id,
            highlight=highlighted,
        ))
    
    return results


@router.post("/{job_id}/export")
async def export_transcript(
    job_id: UUID,
    request: ExportRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Generate and download a transcript export.
    Generates on-the-fly; caches to disk for repeat downloads.
    """
    from app.services.export.export_service import TranscriptExporter
    
    # Load transcript with segments
    result = await db.execute(
        select(Transcript)
        .where(Transcript.job_id == job_id)
        .options(selectinload(Transcript.segments))
    )
    transcript = result.scalar_one_or_none()
    
    if not transcript:
        raise HTTPException(status_code=404, detail="Transcript not found")
    
    # Load job for metadata
    job_result = await db.execute(
        select(TranscriptionJob).where(TranscriptionJob.id == job_id)
    )
    job = job_result.scalar_one_or_none()
    
    # Build segments list for exporter
    segments_data = [
        {
            "text": seg.text,
            "start_time": seg.start_time,
            "end_time": seg.end_time,
            "speaker_id": seg.speaker_id,
            "speaker_name": seg.speaker_name,
            "confidence": seg.confidence,
            "words": seg.words or [],
        }
        for seg in sorted(transcript.segments, key=lambda s: s.segment_index)
    ]
    
    metadata = {
        "filename": job.original_filename if job else "transcript",
        "language": transcript.detected_language or "Unknown",
        "duration": transcript.duration_seconds,
        "word_count": transcript.word_count,
        "speaker_count": transcript.speaker_count,
        "summary": transcript.summary,
        "keywords": transcript.keywords or [],
        "sentiment": transcript.sentiment,
    }
    
    exporter = TranscriptExporter(segments_data, metadata)
    
    # Generate export
    fmt = request.format
    
    try:
        if fmt == ExportFormat.TXT:
            content = exporter.export_txt(
                include_timestamps=request.include_timestamps,
                include_speakers=request.include_speakers,
            )
        elif fmt == ExportFormat.SRT:
            content = exporter.export_srt()
        elif fmt == ExportFormat.VTT:
            content = exporter.export_vtt()
        elif fmt == ExportFormat.DOCX:
            content = exporter.export_docx(
                include_timestamps=request.include_timestamps,
                include_speakers=request.include_speakers,
            )
        elif fmt == ExportFormat.PDF:
            content = exporter.export_pdf(
                include_timestamps=request.include_timestamps,
                include_speakers=request.include_speakers,
            )
        elif fmt == ExportFormat.JSON:
            content = exporter.export_json()
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}")
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    # Build safe filename
    safe_name = "".join(
        c if c.isalnum() or c in (" ", "-", "_") else "_"
        for c in (job.original_filename if job else "transcript")
    )
    safe_name = safe_name.rsplit(".", 1)[0]  # Remove extension
    filename = f"{safe_name}_transcript.{EXTENSION_MAP[fmt]}"
    
    content_type = CONTENT_TYPE_MAP[fmt]
    
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
        },
    )


# Health check route
@router.get("")
async def list_transcripts_redirect():
    """Redirect to jobs list."""
    return {"message": "Use /api/v1/jobs to list all jobs with transcripts"}
