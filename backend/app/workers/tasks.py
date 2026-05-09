"""
Celery worker configuration and task definitions.

Tasks:
  - process_video_job: Full pipeline orchestrator
  - extract_audio_task: FFmpeg extraction
  - transcribe_audio_task: WhisperX inference
  - generate_transcript_task: Build transcript from raw results
  - cleanup_temp_files_task: Garbage collection

Architecture:
  - One Celery queue per priority tier: high / default / low
  - Retry strategy: exponential backoff, max 3 retries
  - Task state published to Redis for SSE streaming
"""
import logging
import os
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Optional

from celery import Celery, Task, chain
from celery.utils.log import get_task_logger

from app.core.config import settings

logger = get_task_logger(__name__)


# ─── Celery Application ────────────────────────────────────────────────────────

celery_app = Celery(
    "transcriptai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    
    # Timezone
    timezone="UTC",
    enable_utc=True,
    
    # Task settings
    task_track_started=True,
    task_acks_late=True,           # Ack only after completion (prevents loss on crash)
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # One task per worker (GPU memory heavy)
    
    # Timeouts
    task_soft_time_limit=settings.JOB_TIMEOUT_SECONDS,
    task_time_limit=settings.JOB_TIMEOUT_SECONDS + 300,
    
    # Result expiry
    result_expires=86400,  # 24 hours
    
    # Routing
    task_routes={
        "app.workers.tasks.process_video_job": {"queue": "transcription"},
        "app.workers.tasks.extract_audio_task": {"queue": "processing"},
        "app.workers.tasks.transcribe_audio_task": {"queue": "ai"},
        "app.workers.tasks.generate_transcript_task": {"queue": "processing"},
        "app.workers.tasks.cleanup_temp_files_task": {"queue": "cleanup"},
    },
    
    task_queues={
        "transcription": {"exchange": "transcription", "routing_key": "transcription"},
        "processing": {"exchange": "processing", "routing_key": "processing"},
        "ai": {"exchange": "ai", "routing_key": "ai"},
        "cleanup": {"exchange": "cleanup", "routing_key": "cleanup"},
    },
)


# ─── Progress Publisher ────────────────────────────────────────────────────────

def publish_progress(
    job_id: str,
    status: str,
    progress: float,
    stage: str,
    message: str = "",
):
    """
    Publish job progress to Redis pub/sub channel.
    The FastAPI SSE endpoint subscribes to this channel per job_id.
    """
    import redis
    import json
    
    r = redis.from_url(settings.REDIS_URL, decode_responses=True)
    channel = f"job_progress:{job_id}"
    payload = json.dumps({
        "job_id": job_id,
        "status": status,
        "progress": round(progress, 1),
        "stage": stage,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    r.publish(channel, payload)
    
    # Also store last known state for late subscribers
    r.setex(f"job_state:{job_id}", 3600, payload)


# ─── Database Helper (sync version for Celery workers) ─────────────────────────

def get_sync_db():
    """Create a synchronous SQLAlchemy session for use in Celery tasks."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    
    # Convert asyncpg URL to psycopg2 for sync context
    db_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
    engine = create_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    return Session()


# ─── Main Pipeline Task ────────────────────────────────────────────────────────

@celery_app.task(
    bind=True,
    name="app.workers.tasks.process_video_job",
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=settings.JOB_TIMEOUT_SECONDS,
)
def process_video_job(self, job_id: str):
    """
    Master orchestrator for the full transcription pipeline.
    
    Stages:
      0%  → Job started
      5%  → Probing video
      10% → Extracting audio
      40% → Transcribing
      70% → Diarizing speakers
      85% → Building transcript
      95% → Saving to database
      100% → Complete
    """
    from app.models.models import TranscriptionJob, JobStatus
    from app.services.audio.ffmpeg_service import FFmpegAudioService
    from app.services.transcription.whisper_service import WhisperXService
    
    db = get_sync_db()
    temp_files = []
    
    try:
        # Load job
        job = db.query(TranscriptionJob).filter(
            TranscriptionJob.id == job_id
        ).first()
        
        if not job:
            logger.error(f"Job {job_id} not found")
            return
        
        if job.status == JobStatus.CANCELLED:
            logger.info(f"Job {job_id} was cancelled, skipping")
            return
        
        logger.info(f"Processing job {job_id}: {job.original_filename}")
        
        # Update status
        job.status = JobStatus.EXTRACTING_AUDIO
        job.started_at = datetime.now(timezone.utc)
        job.celery_task_id = self.request.id
        db.commit()
        
        publish_progress(job_id, "extracting_audio", 5, "Extracting audio", "Analyzing video file...")
        
        # ── Stage 1: Audio Extraction ──────────────────────────────────────────
        import asyncio
        
        ffmpeg = FFmpegAudioService(
            ffmpeg_path=settings.FFMPEG_PATH,
            ffprobe_path=settings.FFPROBE_PATH,
        )
        
        # Probe video metadata
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            metadata = loop.run_until_complete(
                ffmpeg.probe_video(job.storage_path)
            )
        except Exception as e:
            raise RuntimeError(f"Video probe failed: {e}")
        
        # Update job with metadata
        job.duration_seconds = metadata.duration_seconds
        job.video_codec = metadata.video_codec
        job.audio_codec = metadata.audio_codec
        job.resolution = metadata.resolution
        job.fps = metadata.fps
        db.commit()
        
        if not metadata.has_audio:
            raise ValueError("Video file contains no audio track")
        
        publish_progress(job_id, "extracting_audio", 10, "Extracting audio", 
                        f"Extracting audio from {metadata.duration_seconds:.0f}s video...")
        
        # Create temp dir for audio
        temp_dir = tempfile.mkdtemp(prefix=f"transcriptai_{job_id}_")
        temp_files.append(temp_dir)
        
        def sync_progress_cb(pct):
            publish_progress(
                job_id, "extracting_audio",
                10 + pct * 0.2,  # Scale 10-30%
                "Extracting audio",
                f"Processing audio... {pct:.0f}%",
            )
        
        async def run_extraction():
            return await ffmpeg.extract_audio(
                job.storage_path,
                temp_dir,
                normalize_audio=True,
                noise_reduce=False,
                progress_callback=sync_progress_cb,
            )
        
        audio_result = loop.run_until_complete(run_extraction())
        
        job.audio_path = audio_result.audio_path
        job.status = JobStatus.TRANSCRIBING
        db.commit()
        
        publish_progress(job_id, "transcribing", 35, "Transcribing", 
                        "Running AI speech recognition...")
        
        # ── Stage 2: Transcription ─────────────────────────────────────────────
        whisper = WhisperXService(
            model_name=job.whisper_model or settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE,
            compute_type=settings.WHISPER_COMPUTE_TYPE,
            batch_size=settings.WHISPER_BATCH_SIZE,
            hf_token=settings.HF_TOKEN,
        )
        
        job.status = JobStatus.DIARIZING if job.enable_diarization else JobStatus.TRANSCRIBING
        db.commit()
        
        async def run_transcription():
            def progress_cb(pct, msg):
                publish_progress(
                    job_id, "transcribing",
                    35 + pct * 0.4,  # Scale 35-75%
                    "Transcribing",
                    msg,
                )
            return await whisper.transcribe(
                audio_result.audio_path,
                language=job.language if job.language != "auto" else None,
                enable_diarization=job.enable_diarization,
                progress_callback=progress_cb,
            )
        
        transcription_result = loop.run_until_complete(run_transcription())
        
        publish_progress(job_id, "generating_transcript", 80, "Building transcript", 
                        "Generating transcript document...")
        
        # ── Stage 3: Build and Save Transcript ────────────────────────────────
        job.status = JobStatus.GENERATING_TRANSCRIPT
        db.commit()
        
        _save_transcript_to_db(db, job, transcription_result)
        
        # ── Stage 4: AI Enrichments (async, best-effort) ──────────────────────
        publish_progress(job_id, "generating_transcript", 92, "Finalizing", 
                        "Generating AI summary and keywords...")
        
        _run_ai_enrichments(db, job)
        
        # ── Complete ───────────────────────────────────────────────────────────
        job.status = JobStatus.COMPLETED
        job.progress_percent = 100.0
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        
        publish_progress(job_id, "completed", 100, "Complete",
                        "Transcription complete!")
        
        logger.info(f"Job {job_id} completed successfully")
        loop.close()
        
        return {"job_id": job_id, "status": "completed"}
        
    except Exception as exc:
        logger.error(f"Job {job_id} failed: {exc}", exc_info=True)
        
        try:
            job.status = JobStatus.FAILED
            job.error_message = str(exc)[:1000]
            db.commit()
        except Exception:
            pass
        
        publish_progress(job_id, "failed", 0, "Failed", f"Error: {str(exc)[:200]}")
        
        # Retry on transient errors
        if "timeout" in str(exc).lower() or "connection" in str(exc).lower():
            raise self.retry(exc=exc, countdown=60)
        
        raise
    
    finally:
        db.close()
        # Cleanup temp files
        import shutil
        for f in temp_files:
            try:
                if os.path.isdir(f):
                    shutil.rmtree(f, ignore_errors=True)
                elif os.path.exists(f):
                    os.unlink(f)
            except Exception as e:
                logger.warning(f"Failed to cleanup {f}: {e}")


def _save_transcript_to_db(db, job, transcription_result):
    """Persist transcription result to database."""
    from app.models.models import Transcript, TranscriptSegment
    
    # Collect full text
    full_text = " ".join(
        seg.text for seg in transcription_result.segments
        if seg.text.strip()
    )
    
    # Count unique speakers
    speakers = {seg.speaker for seg in transcription_result.segments if seg.speaker}
    
    transcript = Transcript(
        job_id=job.id,
        detected_language=transcription_result.language,
        language_confidence=transcription_result.language_probability,
        full_text=full_text,
        word_count=transcription_result.word_count,
        speaker_count=len(speakers),
        duration_seconds=transcription_result.duration,
    )
    db.add(transcript)
    db.flush()  # Get transcript.id
    
    # Persist segments
    for i, seg in enumerate(transcription_result.segments):
        if not seg.text.strip():
            continue
        
        words_data = [
            {
                "word": w.word,
                "start": w.start,
                "end": w.end,
                "score": w.score,
                "speaker": w.speaker,
            }
            for w in seg.words
        ] if seg.words else None
        
        segment = TranscriptSegment(
            transcript_id=transcript.id,
            start_time=seg.start,
            end_time=seg.end,
            text=seg.text.strip(),
            speaker_id=seg.speaker,
            confidence=seg.confidence,
            words=words_data,
            segment_index=i,
        )
        db.add(segment)
    
    db.commit()
    logger.info(f"Saved {len(transcription_result.segments)} segments to database")


def _run_ai_enrichments(db, job):
    """
    Optional AI enrichments: summarization, keyword extraction, sentiment.
    Best-effort — failures don't fail the job.
    """
    from app.models.models import Transcript
    
    transcript = db.query(Transcript).filter(
        Transcript.job_id == job.id
    ).first()
    
    if not transcript or not transcript.full_text:
        return
    
    try:
        # Simple keyword extraction (production: use KeyBERT or spaCy)
        keywords = _extract_keywords(transcript.full_text)
        transcript.keywords = keywords
        db.commit()
    except Exception as e:
        logger.warning(f"Keyword extraction failed: {e}")


def _extract_keywords(text: str, max_keywords: int = 15) -> list:
    """
    Simple frequency-based keyword extraction.
    Production: replace with KeyBERT, YAKE, or spaCy NER.
    """
    import re
    from collections import Counter
    
    # Common English stopwords (abbreviated)
    STOPWORDS = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "is", "was", "are", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "that", "this", "these",
        "those", "it", "its", "i", "you", "he", "she", "we", "they", "what",
        "which", "who", "when", "where", "why", "how", "not", "no", "so",
        "if", "then", "than", "just", "like", "about", "from", "up", "out",
        "as", "by", "my", "your", "his", "her", "our", "their", "all", "one",
    }
    
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text.lower())
    words = [w for w in words if w not in STOPWORDS]
    
    counter = Counter(words)
    return [word for word, _ in counter.most_common(max_keywords)]


@celery_app.task(name="app.workers.tasks.cleanup_temp_files_task")
def cleanup_temp_files_task(paths: list):
    """Delete temporary files after processing."""
    import shutil
    for path in paths:
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            elif os.path.exists(path):
                os.unlink(path)
            logger.info(f"Cleaned up: {path}")
        except Exception as e:
            logger.warning(f"Cleanup failed for {path}: {e}")
