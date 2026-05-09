"""
Core application configuration using Pydantic Settings.
All values read from environment variables with sensible defaults.
"""
from functools import lru_cache
from typing import List, Optional
from pydantic_settings import BaseSettings
from pydantic import field_validator


class Settings(BaseSettings):
    # App
    APP_NAME: str = "TranscriptAI"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production-use-openssl-rand-hex-32"
    API_VERSION: str = "v1"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/transcriptai"
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 40

    # Redis (for Celery + cache + pub/sub)
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # Storage
    STORAGE_BACKEND: str = "local"  # "local" | "s3" | "gcs"
    LOCAL_STORAGE_PATH: str = "/tmp/transcriptai/storage"
    S3_BUCKET: Optional[str] = None
    S3_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: Optional[str] = None
    AWS_SECRET_ACCESS_KEY: Optional[str] = None

    # Upload limits
    MAX_UPLOAD_SIZE_MB: int = 5000  # 5GB
    ALLOWED_VIDEO_EXTENSIONS: List[str] = ["mp4", "mov", "avi", "mkv", "webm", "m4v", "flv"]
    CHUNK_SIZE_BYTES: int = 1024 * 1024 * 10  # 10MB chunks

    # AI / Whisper
    WHISPER_MODEL: str = "large-v3"  # tiny|base|small|medium|large-v3
    WHISPER_DEVICE: str = "auto"  # auto|cpu|cuda
    WHISPER_COMPUTE_TYPE: str = "float16"  # float32|float16|int8
    WHISPER_BATCH_SIZE: int = 16
    USE_WHISPERX: bool = True  # WhisperX for better timestamps + diarization
    ENABLE_DIARIZATION: bool = True
    HF_TOKEN: Optional[str] = None  # HuggingFace token for pyannote diarization

    # FFmpeg
    FFMPEG_PATH: str = "ffmpeg"
    FFPROBE_PATH: str = "ffprobe"

    # Processing
    AUDIO_SAMPLE_RATE: int = 16000  # Whisper expects 16kHz
    AUDIO_CHANNELS: int = 1  # Mono
    MAX_CONCURRENT_JOBS: int = 4
    JOB_TIMEOUT_SECONDS: int = 7200  # 2 hours max per job

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "https://app.transcriptai.com",
    ]

    # JWT
    JWT_SECRET_KEY: str = "jwt-secret-change-in-production"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    @field_validator("WHISPER_DEVICE", mode="before")
    @classmethod
    def resolve_device(cls, v: str) -> str:
        if v == "auto":
            try:
                import torch
                return "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                return "cpu"
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
