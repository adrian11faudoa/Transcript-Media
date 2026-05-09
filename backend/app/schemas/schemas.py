"""
Pydantic v2 schemas for type-safe API communication.
"""
from datetime import datetime
from typing import Optional, List, Any, Dict
from uuid import UUID
from pydantic import BaseModel, Field, field_validator

from app.models.models import JobStatus, ExportFormat, MediaType


# ─── Upload Schemas ────────────────────────────────────────────────────────────

class UploadInitRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=500)
    file_size: int = Field(..., gt=0)
    media_type: MediaType = MediaType.OTHER
    language: str = Field(default="auto", pattern=r"^(auto|[a-z]{2})$")
    enable_diarization: bool = True
    enable_translation: bool = False
    target_language: Optional[str] = Field(default=None, pattern=r"^[a-z]{2}$")
    whisper_model: Optional[str] = None

    @field_validator("filename")
    @classmethod
    def validate_extension(cls, v: str) -> str:
        allowed = {"mp4", "mov", "avi", "mkv", "webm", "m4v", "flv"}
        ext = v.rsplit(".", 1)[-1].lower() if "." in v else ""
        if ext not in allowed:
            raise ValueError(f"File type '.{ext}' not supported. Allowed: {allowed}")
        return v


class UploadInitResponse(BaseModel):
    job_id: UUID
    upload_url: str  # Pre-signed URL or multipart endpoint
    chunk_size: int
    total_chunks: int


class ChunkUploadResponse(BaseModel):
    job_id: UUID
    chunk_index: int
    received_chunks: int
    total_chunks: int
    complete: bool


# ─── Job Schemas ───────────────────────────────────────────────────────────────

class JobProgressEvent(BaseModel):
    """SSE event payload for real-time progress updates."""
    job_id: str
    status: JobStatus
    progress: float = Field(ge=0, le=100)
    stage: str
    message: str
    timestamp: datetime


class JobSummary(BaseModel):
    id: UUID
    original_filename: str
    file_size_bytes: int
    duration_seconds: Optional[float]
    status: JobStatus
    progress_percent: float
    current_stage: Optional[str]
    error_message: Optional[str]
    media_type: MediaType
    language: str
    created_at: datetime
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class JobDetail(JobSummary):
    celery_task_id: Optional[str]
    video_codec: Optional[str]
    audio_codec: Optional[str]
    resolution: Optional[str]
    fps: Optional[float]
    whisper_model: Optional[str]
    enable_diarization: bool
    started_at: Optional[datetime]


# ─── Transcript Schemas ────────────────────────────────────────────────────────

class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    score: Optional[float] = None


class SegmentResponse(BaseModel):
    id: UUID
    start_time: float
    end_time: float
    text: str
    speaker_id: Optional[str]
    speaker_name: Optional[str]
    confidence: Optional[float]
    words: Optional[List[WordTimestamp]]
    segment_index: int

    class Config:
        from_attributes = True


class TranscriptResponse(BaseModel):
    id: UUID
    job_id: UUID
    detected_language: Optional[str]
    language_confidence: Optional[float]
    full_text: str
    word_count: int
    speaker_count: int
    duration_seconds: Optional[float]
    summary: Optional[str]
    keywords: Optional[List[str]]
    sentiment: Optional[str]
    sentiment_score: Optional[float]
    segments: List[SegmentResponse]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class SegmentUpdateRequest(BaseModel):
    """Update a transcript segment (from editor)."""
    text: Optional[str] = None
    speaker_name: Optional[str] = None
    start_time: Optional[float] = Field(default=None, ge=0)
    end_time: Optional[float] = Field(default=None, ge=0)


class TranscriptSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    job_id: Optional[UUID] = None


class TranscriptSearchResult(BaseModel):
    segment_id: UUID
    job_id: UUID
    start_time: float
    end_time: float
    text: str
    speaker_name: Optional[str]
    highlight: str  # Text with <mark> tags


# ─── Export Schemas ────────────────────────────────────────────────────────────

class ExportRequest(BaseModel):
    format: ExportFormat
    include_speakers: bool = True
    include_timestamps: bool = True
    include_confidence: bool = False
    max_line_length: Optional[int] = Field(default=None, ge=10, le=200)


class ExportResponse(BaseModel):
    export_id: UUID
    job_id: UUID
    format: ExportFormat
    download_url: str
    file_size_bytes: Optional[int]
    created_at: datetime


# ─── Generic ──────────────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None


class PaginatedResponse(BaseModel):
    items: List[Any]
    total: int
    page: int
    page_size: int
    pages: int
