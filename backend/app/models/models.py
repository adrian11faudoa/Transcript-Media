"""
SQLAlchemy ORM models for TranscriptAI.
"""
import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import Optional, List

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, DateTime, 
    ForeignKey, Enum, Text, JSON, BigInteger, Index
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship, DeclarativeBase
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    pass


class JobStatus(str, PyEnum):
    PENDING = "pending"
    UPLOADING = "uploading"
    QUEUED = "queued"
    EXTRACTING_AUDIO = "extracting_audio"
    ENHANCING_AUDIO = "enhancing_audio"
    TRANSCRIBING = "transcribing"
    DIARIZING = "diarizing"
    GENERATING_TRANSCRIPT = "generating_transcript"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExportFormat(str, PyEnum):
    DOCX = "docx"
    PDF = "pdf"
    TXT = "txt"
    SRT = "srt"
    VTT = "vtt"
    JSON = "json"


class MediaType(str, PyEnum):
    MOVIE = "movie"
    TV_SERIES = "tv_series"
    INTERVIEW = "interview"
    PODCAST = "podcast"
    CLIP = "clip"
    OTHER = "other"


class TranscriptionJob(Base):
    """Core job entity tracking the full transcription pipeline."""
    __tablename__ = "transcription_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # File info
    original_filename = Column(String(500), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    file_extension = Column(String(10), nullable=False)
    storage_path = Column(String(1000))  # Path in storage backend
    audio_path = Column(String(1000))     # Extracted audio path
    
    # Media metadata (from FFprobe)
    duration_seconds = Column(Float)
    video_codec = Column(String(50))
    audio_codec = Column(String(50))
    resolution = Column(String(20))
    fps = Column(Float)
    media_type = Column(Enum(MediaType), default=MediaType.OTHER)
    
    # Processing config
    language = Column(String(10), default="auto")  # ISO 639-1 or "auto"
    whisper_model = Column(String(50))
    enable_diarization = Column(Boolean, default=True)
    enable_translation = Column(Boolean, default=False)
    target_language = Column(String(10))
    
    # Status tracking
    status = Column(Enum(JobStatus), default=JobStatus.PENDING, nullable=False, index=True)
    progress_percent = Column(Float, default=0.0)
    current_stage = Column(String(100))
    error_message = Column(Text)
    
    # Timing
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    
    # Celery task ID for tracking
    celery_task_id = Column(String(255))
    
    # Relationships
    transcript = relationship("Transcript", back_populates="job", uselist=False, cascade="all, delete-orphan")
    exports = relationship("TranscriptExport", back_populates="job", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_jobs_status_created", "status", "created_at"),
    )


class Transcript(Base):
    """The full transcript with all segments and metadata."""
    __tablename__ = "transcripts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("transcription_jobs.id", ondelete="CASCADE"), nullable=False, unique=True)
    
    # Detected language and confidence
    detected_language = Column(String(10))
    language_confidence = Column(Float)
    
    # Full text (denormalized for fast search)
    full_text = Column(Text)
    
    # Statistics
    word_count = Column(Integer, default=0)
    speaker_count = Column(Integer, default=0)
    duration_seconds = Column(Float)
    
    # AI-generated extras
    summary = Column(Text)
    keywords = Column(JSONB)       # List of keyword strings
    sentiment = Column(String(20)) # positive|neutral|negative
    sentiment_score = Column(Float)
    scene_segments = Column(JSONB) # AI scene segmentation data
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    job = relationship("TranscriptionJob", back_populates="transcript")
    segments = relationship("TranscriptSegment", back_populates="transcript", 
                           order_by="TranscriptSegment.start_time",
                           cascade="all, delete-orphan")


class TranscriptSegment(Base):
    """Individual timestamped speech segments (words or phrases)."""
    __tablename__ = "transcript_segments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    transcript_id = Column(UUID(as_uuid=True), ForeignKey("transcripts.id", ondelete="CASCADE"), nullable=False)
    
    # Timing
    start_time = Column(Float, nullable=False)  # seconds
    end_time = Column(Float, nullable=False)    # seconds
    
    # Content
    text = Column(Text, nullable=False)
    
    # Speaker diarization
    speaker_id = Column(String(50))   # e.g. "SPEAKER_00"
    speaker_name = Column(String(200)) # User-assigned name
    
    # Confidence score from Whisper
    confidence = Column(Float)
    
    # Word-level timestamps (stored as JSONB for efficiency)
    # Format: [{"word": "Hello", "start": 0.0, "end": 0.5, "score": 0.99}, ...]
    words = Column(JSONB)
    
    # Segment index for ordering
    segment_index = Column(Integer, nullable=False)
    
    transcript = relationship("Transcript", back_populates="segments")

    __table_args__ = (
        Index("ix_segments_transcript_time", "transcript_id", "start_time"),
    )


class TranscriptExport(Base):
    """Cached export files generated from a transcript."""
    __tablename__ = "transcript_exports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("transcription_jobs.id", ondelete="CASCADE"), nullable=False)
    
    format = Column(Enum(ExportFormat), nullable=False)
    storage_path = Column(String(1000), nullable=False)
    file_size_bytes = Column(BigInteger)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True))  # For temporary signed URLs

    job = relationship("TranscriptionJob", back_populates="exports")

    __table_args__ = (
        Index("ix_exports_job_format", "job_id", "format"),
    )
